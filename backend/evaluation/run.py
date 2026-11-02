"""Real service/model evaluation. Immutable cases + gold SQL + per-round raw traces."""
from __future__ import annotations
import argparse, dataclasses, hashlib, json, math, re, shutil, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

BACKEND=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(BACKEND))
from app.config import settings
from app.model_client import ModelClient
from app.retrieval import SchemaIndex
from app.service import AskDataService
from app.querying.duckdb_engine import DuckDbEngine
from app.security import AccessController, AccessScope
from app.streaming import event_sink

EVAL=Path(__file__).resolve().parent
OUT=BACKEND.parent/'output'/'evaluation-20261020'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def write(path,obj):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,ensure_ascii=False,indent=2,default=str),encoding='utf8')
def read(path):return json.loads(path.read_text(encoding='utf8'))

class RecordedModel(ModelClient):
    def __init__(self,config,prompt=''):
        super().__init__(config);self.calls=[];self.prompt=prompt
    def chat_json(self,system,user):
        if self.prompt and system.startswith('你是单数据库问数智能体'):
            system+='\n\n'+self.prompt
        item={'type':'chat_json','system':system,'user':user,'start':time.time()};self.calls.append(item)
        try:
            value=super().chat_json(system,user);item['response']=value;return value
        except Exception as exc:item['error']=str(exc);raise
        finally:item['seconds']=round(time.time()-item['start'],3)
    def embed(self,texts):
        item={'type':'embedding','texts':texts,'start':time.time()};self.calls.append(item)
        try:
            value=super().embed(texts);item['vectors']=len(value);return value
        except Exception as exc:item['error']=str(exc);raise
        finally:item['seconds']=round(time.time()-item['start'],3)
    def rerank(self,query,texts,top_n):
        item={'type':'rerank','query':query,'texts':texts,'start':time.time()};self.calls.append(item)
        try:
            value=super().rerank(query,texts,top_n);item['response']=value;return value
        except Exception as exc:item['error']=str(exc);raise
        finally:item['seconds']=round(time.time()-item['start'],3)

def get_gold(cases):
    gold={}
    engine=DuckDbEngine()
    for db in sorted({c['database'] for c in cases if c.get('gold_sql')}):
        with engine.connect(db) as connection:
            for c in cases:
                if c.get('database')!=db or not c.get('gold_sql'):continue
                cursor=connection.execute(c['gold_sql'])
                gold[c['id']]={'columns':[x[0] for x in cursor.description],
                    'rows':[list(row) for row in cursor.fetchall()],'sql':c['gold_sql']}
    return gold

def norm(row):
    # Chinese aliases and SQL column ordering may vary; compare text/numeric multisets.
    text=[];number=[]
    for x in row:
        if x is None:text.append('<NULL>')
        elif isinstance(x,(int,float)) and not isinstance(x,bool):number.append(float(x))
        else:text.append(str(x))
    return sorted(text),sorted(number)
def rows_equal(actual,expected,tolerance):
    if len(actual)!=len(expected):return False,f'行数：实际{len(actual)}，标准{len(expected)}'
    remaining=[norm(x) for x in expected]
    for index,row in enumerate(actual):
        a,b=norm(list(row.values()));match=None
        for j,(x,y) in enumerate(remaining):
            # Four-decimal ratios have stricter absolute tolerance than money.
            # Never use a relative tolerance that permits yuan-sized errors on large totals.
            if a==x and len(b)==len(y) and all(math.isclose(v,w,rel_tol=0,abs_tol=min(tolerance,.00011) if abs(w)<100 and abs(w-round(w,2))>1e-8 else tolerance) for v,w in zip(b,y)):
                match=j;break
        if match is None:return False,f'第{index+1}行未匹配标准值：{list(row.values())}'
        remaining.pop(match)
    return True,'结果数值和维度匹配（忽略别名、列顺序、行顺序）'

def failure_category(result,reason):
    text=(str(result.get('analysis',''))+' '+reason).lower()
    if re.search(r'http(?: error)?\s+[45]\d\d',text) or any(w in text for w in ['timed out','timeout','urlopen','ssl','accessdenied','尚未配置','getaddrinfo','connection refused','模型服务暂时不可用','winerror 10013']):return 'infrastructure'
    stages=[x.get('stage','') for x in result.get('execution_log',[])]
    if result.get('status')=='waiting_clarification':return 'unexpected_clarification'
    if result.get('workflow_mode')=='multi_database_handoff':return 'database_scope'
    if not result.get('sql'):
        if any('retriev' in s or 'schema_' in s for s in stages):return 'retrieval_or_schema'
        return 'routing_or_generation'
    if result.get('status')!='completed':return 'sql_execution'
    return 'business_result_mismatch'

def judge(c,result,gold,setup=None):
    k=c['kind'];status=result.get('status');route=result.get('route');sql=result.get('sql')
    if k=='query':
        if status!='completed':return False,'期望查询完成，实际 '+str(status)
        if not sql:return False,'期望执行SQL，但未生成SQL'
        ok,reason=rows_equal(result.get('rows',[]),gold[c['id']]['rows'],c.get('tolerance',.011))
        return ok,reason
    if k=='greeting':return (route=='direct_response' and not sql and bool(result.get('analysis'))),'问候应直接回答且不查询数据库'
    if k=='clarify':
        text=result.get('analysis','')
        ok=not sql and (status=='waiting_clarification' or any(w in text for w in ['哪','请','希望','？','?']))
        return ok,'信息不足应先询问，不能自行选定业务口径'
    if k=='unsupported':return (not sql and not result.get('rows') and bool(result.get('analysis') or result.get('clarification'))),'缺失指标应澄清或明确停止，不编造表格'
    if k=='workspace_denied':return (status=='failed' and not sql and '无权' in str(result)),'工作区无权字段应在执行前拦截'
    if k in ('explain','report'):
        sources=result.get('analysis_sources',[])
        common=route=='data_qa' and status=='completed' and not sql and not result.get('tool_calls') and any(s['task_id']==setup['task_id'] for s in sources)
        if k=='explain':return common and bool(result.get('analysis')),'解释应复用来源任务，且不再次执行SQL（文字事实需人工复核）'
        report=result.get('report') or {};charts=report.get('visualizations',[])
        ok=common and bool(report.get('markdown')) and bool(charts) and all(v['source_task_id']==setup['task_id'] and v['category_field'] in setup['columns'] and v['value_field'] in setup['columns'] for v in charts)
        return ok,'报告Markdown和图表必须引用原表有效字段（报告事实需人工复核）'
    raise ValueError(k)

def guards(case):
    engine=DuckDbEngine();ac=AccessController();k=case['kind']
    if k=='guard_write':
        statements=["UPDATE orders_current SET region='华东'","SELECT order_id FROM orders_current; SELECT customer_id FROM customers","SELECT * FROM orders_current"]
        values=[engine.execute('askdata_mock',s,ac.resolve('demo_analyst')).__dict__ for s in statements]
        return all(not x['success'] for x in values),values
    if k=='guard_cte':
        scope=AccessScope('eval_restricted',('test',),frozenset({'askdata_mock'}),frozenset({'orders_current','customers'}))
        value=engine.execute('askdata_mock','WITH h AS (SELECT order_id FROM orders_history) SELECT order_id FROM h',scope).__dict__
        return not value['success'] and '无权' in value.get('error',''),value
    if k in ('guard_cte_shadow','guard_cte_empty_scope'):
        allowed=frozenset({'orders_current','customers'}) if k=='guard_cte_shadow' else frozenset()
        table='orders_history' if k=='guard_cte_shadow' else 'orders_current'
        scope=AccessScope('eval_shadow_restricted',('test',),frozenset({'askdata_mock'}),allowed)
        statement=f'WITH {table} AS (SELECT order_id FROM {table}) SELECT COUNT(order_id) AS n FROM {table}'
        value=engine.execute('askdata_mock',statement,scope).__dict__
        return not value['success'] and '无权' in value.get('error',''),value
    if k=='guard_auth':
        from fastapi.testclient import TestClient
        from app.main import app
        with TestClient(app) as client:
            unauth=client.get('/api/schema').status_code
            token=client.post('/api/auth/login',json={'username':'sales','password':'sales123'}).json()['access_token']
            schema=client.get('/api/schema',headers={'Authorization':'Bearer '+token}).json()
        value={'unauthenticated_status':unauth,'sales_tables':len(schema),'databases':sorted({t['database'] for t in schema})}
        return unauth==401 and len(schema)==50 and value['databases']==['ecommerce_ops'],value
    raise ValueError(k)

def execute(c,args,config,cache,prompt,gold,by_id,folder):
    start=time.perf_counter();out={'id':c['id'],'title':c['title'],'kind':c['kind'],'query':c.get('query'),'user':c.get('user'),'started_utc':datetime.now(timezone.utc).isoformat()}
    model=None;events=[];setup_result=None
    try:
        if c['kind'].startswith('guard_'):
            ok,value=guards(c);out.update(passed=ok,result=value,reason='确定性后端边界检查',category='passed' if ok else 'guardrail')
        else:
            model=RecordedModel(config,prompt);index=SchemaIndex(model,config,cache)
            service=AskDataService(model,index)
            token=event_sink.set(lambda event:events.append({'seconds':round(time.perf_counter()-start,3),**event}))
            try:
                session=f'eval-{args.round}-{c["id"]}'
                if c.get('setup'):
                    source=by_id[c['setup']]
                    source_obj=service.submit(source['query'],session,user_id=c['user'])
                    setup_result=source_obj.model_dump(mode='json');out['setup_result']=setup_result
                    source_ok,source_reason=judge(source,setup_result,gold)
                    if not source_ok:
                        out.update(passed=False,reason='前置查询失败：'+source_reason,category='dependency',result=setup_result)
                        return finish(out,start,model,events,folder)
                    workspace={'analysis_table_ids':[setup_result['task_id']]}
                else:workspace=c.get('workspace') or {}
                result=service.submit(c['query'],session,workspace,user_id=c['user']).model_dump(mode='json')
            finally:event_sink.reset(token)
            ok,reason=judge(c,result,gold,setup_result)
            category='passed' if ok else failure_category(result,reason)
            if not ok and failure_category(result,reason)=='infrastructure':category='infrastructure'
            out.update(passed=ok,reason=reason,category=category,result=result)
            if c.get('required_fields'):
                hits={h['doc_id'] for h in (result.get('retrieval') or {}).get('hits',[])}
                out['missing_required_fields']=sorted(set(c['required_fields'])-hits)
                out['required_field_recall']=round(len(set(c['required_fields'])&hits)/len(c['required_fields']),4)
    except Exception as exc:
        out.update(passed=False,reason=str(exc),category='harness_or_runtime',exception=traceback.format_exc())
    return finish(out,start,model,events,folder)

def finish(out,start,model,events,folder):
    out['seconds']=round(time.perf_counter()-start,3)
    out['model_calls']=model.calls if model else []
    out['events']=events
    out['text_event_count']=sum(x.get('event')=='text' for x in events)
    out['first_table_seconds']=next((x['seconds'] for x in events if x.get('event')=='table'),None)
    out['first_text_seconds']=next((x['seconds'] for x in events if x.get('event')=='text'),None)
    write(folder/'cases'/f'{out["id"]}.json',out)
    return out

def main():
    p=argparse.ArgumentParser();p.add_argument('--round',required=True);p.add_argument('--ids');p.add_argument('--workers',type=int,default=3)
    p.add_argument('--sql-prompt',type=Path);p.add_argument('--threshold',type=float);p.add_argument('--resume',action='store_true');p.add_argument('--cases',default='cases.v1.json');args=p.parse_args()
    if not all(ch.isalnum() or ch in '-_' for ch in args.round):raise ValueError('Invalid round name')
    case_path=(EVAL/args.cases).resolve()
    assert case_path.parent==EVAL,'Cases must be in evaluation directory'
    all_cases=read(case_path)['cases'];by_id={c['id']:c for c in all_cases}
    selected=[by_id[i] for i in args.ids.split(',')] if args.ids else all_cases
    folder=OUT/args.round
    if folder.exists() and not args.resume:raise RuntimeError('Round exists. Use --resume without changing factors or choose a new round.')
    folder.mkdir(parents=True,exist_ok=True)
    config=dataclasses.replace(settings,session_archive_enabled=False,short_term_summary_enabled=False,session_archive_path=str(folder/'unused-session.db'))
    if args.threshold is not None:config=dataclasses.replace(config,schema_recall_threshold=args.threshold)
    prompt=args.sql_prompt.read_text(encoding='utf8') if args.sql_prompt else ''
    meta={'created_utc':datetime.now(timezone.utc).isoformat(),'round':args.round,'settings':config.public_status(),'temperature':config.temperature,
        'timeout':config.timeout,'max_retries':config.max_retries,'workers':args.workers,'cases_sha256':digest(case_path),'case_file':case_path.name,
        'factor':{'sql_prompt_append':prompt,'threshold_override':args.threshold},'isolation':'Fresh service/session per task; archive and async summaries disabled in every round; no HTTP server; no asset writes',
        'source_hashes':{str(x.relative_to(BACKEND)):digest(x) for x in sorted((BACKEND/'app').rglob('*')) if x.is_file() and x.suffix in ('.py','.md','.json') and '__pycache__' not in str(x)},
        'dataset_hashes':{str(x.relative_to(BACKEND)):digest(x) for x in sorted((BACKEND/'data/databases').rglob('*.csv'))}}
    if (folder/'metadata.json').exists():
        previous=read(folder/'metadata.json')
        for key in ['settings','temperature','cases_sha256','factor','source_hashes','dataset_hashes']:
            assert previous[key]==meta[key],f'Cannot resume with changed {key}'
    else:
        write(folder/'metadata.json',meta);shutil.copy2(case_path,folder/'cases.v1.json')
        snap=folder/'source';snap.mkdir(exist_ok=True)
        for name in meta['source_hashes']:
            dest=snap/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(BACKEND/name,dest)
    gold=get_gold(all_cases);write(folder/'gold.json',gold)
    cache=OUT/'schema-cache.json'
    # A fresh checkout has no embedding cache. Local guard-only runs need no models.
    documents=0
    if any(not c['kind'].startswith('guard_') for c in selected):
        source_cache=BACKEND/'data/schema_store.json'
        if not cache.exists() and source_cache.exists():shutil.copy2(source_cache,cache)
        warm=SchemaIndex(ModelClient(config),config,cache);warm.ensure_built()
        documents=len(warm.documents)
    print('READY '+json.dumps({'round':args.round,'tasks':len(selected),'model':config.llm_model,'cache_documents':documents},ensure_ascii=False),flush=True)
    results=[];pending=[]
    for c in selected:
        file=folder/'cases'/f'{c["id"]}.json'
        if args.resume and file.exists():results.append(read(file))
        else:pending.append(c)
    def summary():
        groups={}
        for label,items in [('all',results),('query',[r for r in results if r['kind']=='query']),('guardrails',[r for r in results if r['kind'].startswith('guard_')]),('behavior',[r for r in results if r['kind']!='query' and not r['kind'].startswith('guard_')])]:
            groups[label]={'total':len(items),'passed':sum(bool(r['passed']) for r in items),'failed':sum(not r['passed'] for r in items)}
        write(folder/'summary.json',{'groups':groups,'cases':[{k:r.get(k) for k in ['id','title','kind','passed','category','reason','seconds','first_table_seconds','first_text_seconds','text_event_count','missing_required_fields','required_field_recall']} for r in sorted(results,key=lambda r:r['id'])]})
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        futures={pool.submit(execute,c,args,config,cache,prompt,gold,by_id,folder):c for c in pending}
        for future in as_completed(futures):
            r=future.result();results.append(r);summary()
            print(json.dumps({k:r.get(k) for k in ['id','passed','category','reason','seconds']},ensure_ascii=False),flush=True)
    summary();print('DONE '+str(folder),flush=True)

if __name__=='__main__':main()
