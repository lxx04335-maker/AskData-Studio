"""Regrade preserved raw results uniformly; never overwrite raw outputs or original scores."""
import argparse, json
from evaluation.run import OUT,EVAL,read,write,judge,failure_category

def audit(round_name):
    folder=OUT/round_name;cases=read(folder/'cases.v1.json')['cases'];by_id={c['id']:c for c in cases};gold=read(folder/'gold.json');rows=[]
    for path in sorted((folder/'cases').glob('*.json')):
        raw=read(path);c=by_id[raw['id']];out={k:raw.get(k) for k in ['id','title','kind','seconds','first_table_seconds','first_text_seconds','text_event_count','required_field_recall','missing_required_fields']}
        if c['kind'].startswith('guard_') or raw.get('exception'):
            ok=raw['passed'];reason=raw['reason'];category=raw['category']
        elif raw.get('category')=='dependency':
            setup_case=by_id[c['setup']];ok,reason=judge(setup_case,raw['setup_result'],gold)
            if ok:ok=False;reason='前置查询需重新运行，不把未执行的后续任务判为通过'
            category='dependency'
        else:
            ok,reason=judge(c,raw['result'],gold,raw.get('setup_result'))
            category='passed' if ok else failure_category(raw['result'],reason)
            if raw['result'].get('workflow_mode')=='route_fallback':ok=False;category='infrastructure'
        out.update(passed=bool(ok),reason=reason,category=category,original_passed=raw['passed']);rows.append(out)
    groups={}
    for name,items in [('all',rows),('query',[r for r in rows if r['kind']=='query']),('guardrails',[r for r in rows if r['kind'].startswith('guard_')]),('behavior',[r for r in rows if r['kind']!='query' and not r['kind'].startswith('guard_')])]:
        groups[name]={'total':len(items),'passed':sum(r['passed'] for r in items),'failed':sum(not r['passed'] for r in items)}
    result={'scorer_version':2,'note':'All rounds regraded with the same scorer; original traces and scores retained. Query matching ignores alias/column/row order and does not validate narrative factuality.','groups':groups,'cases':rows}
    write(folder/'audited-summary.json',result);print(json.dumps({'round':round_name,'groups':groups},ensure_ascii=False))
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('rounds',nargs='+');a=p.parse_args()
    for r in a.rounds:audit(r)
