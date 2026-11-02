"""Render an evidence-led comparison and interview narrative from preserved runs."""
from __future__ import annotations
import csv,difflib,html,json,re
from pathlib import Path
from evaluation.run import OUT,EVAL,read,write
from evaluation.audit import audit

def raw(round_name,id):
    path=OUT/round_name/'cases'/f'{id}.json'
    return read(path) if path.exists() else None
def label(r):
    if not r:return '未复测'
    if r['passed']:return '通过'
    return '服务超时/连接失败' if r['category']=='infrastructure' else '失败'
def pretty(value):return json.dumps(value,ensure_ascii=False,indent=2,default=str)

def main():
    base=audit('r0-live');edge=audit('r0-edge');after=audit('r1-scope')
    assert len(base['cases'])==28 and len(after['cases'])==30
    baseline={r['id']:r for r in base['cases']+edge['cases']};final={r['id']:r for r in after['cases']}
    confirm={}
    if (OUT/'r2-confirm/cases').exists():confirm={r['id']:r for r in audit('r2-confirm')['cases']}
    cases=read(EVAL/'cases.v1.1.json')['cases']
    before_meta=read(OUT/'r0-live/metadata.json');after_meta=read(OUT/'r1-scope/metadata.json')
    changed=[k for k,v in before_meta['source_hashes'].items() if after_meta['source_hashes'].get(k)!=v]
    conditions={'changed_application_files':changed,'same_model_and_public_settings':before_meta['settings']==after_meta['settings'],
        'same_temperature':before_meta['temperature']==after_meta['temperature'],
        'same_datasets':before_meta['dataset_hashes']==after_meta['dataset_hashes'],
        'original_28_case_objects_unchanged':read(OUT/'r0-live/cases.v1.json')['cases']==cases[:28],
        'note':'R0 edge cases were source-informed additions, not held-out samples. R1 began while R0 final tasks were finishing; timing is diagnostic only and is not a controlled performance comparison.'}
    assert changed==['app\\querying\\duckdb_engine.py'] or changed==['app/querying/duckdb_engine.py'],changed
    assert all(conditions[k] for k in ['same_model_and_public_settings','same_temperature','same_datasets','original_28_case_objects_unchanged'])
    write(OUT/'comparison-conditions.json',conditions)
    old=OUT/'r0-live/source/app/querying/duckdb_engine.py';new=OUT/'r1-scope/source/app/querying/duckdb_engine.py'
    diff=''.join(difflib.unified_diff(old.read_text(encoding='utf8').splitlines(True),new.read_text(encoding='utf8').splitlines(True),fromfile='before/duckdb_engine.py',tofile='after/duckdb_engine.py'))
    (OUT/'application-change.diff').write_text(diff,encoding='utf8')
    full_log=(OUT/'full-regression-after.txt').read_text(encoding='utf8',errors='replace')
    count_match=re.search(r'Ran (\d+) tests',full_log);full_count=int(count_match[1]) if count_match else None
    full_ok=bool(count_match and re.search(r'\bOK\s*$',full_log))
    unit_before=read(OUT/'cte-tests-before.json');unit_after=read(OUT/'cte-tests-after.json')
    passes0=sum(r['passed'] for r in baseline.values());passes1=sum(r['passed'] for r in final.values())
    infra=[r for r in final.values() if r['category']=='infrastructure']
    service_diagnosis=read(OUT/'upstream-error-diagnosis.json')
    diagnoses={
        'Q04':{'category':'模型服务超时','evidence':'single_database_agent 调用返回 The read operation timed out；没有进入本轮修改的表权限解析。','next':'余额/服务恢复后按原条件重跑，不把超时当作数值错误。'},
        'Q12':{'category':'时间字段漏召回','evidence':'R0 ordered_at 在重排候选中得分约0.9696；R1候选文本不含该字段，Schema图缺下单时间，模型提出字段澄清并给出未核实的字段名选项。','next':'尚未修复。下一轮固定抽取输出，比较召回候选；只改检索输入的时间事件词保留策略，配合相邻查询复测。'},
        'Q22':{'category':'模型服务HTTP 400','evidence':'数据问答阶段原始响应为 HTTP Error 400，未进入SQL校验。','next':'同一账户Q25请求重放已确认Arrearage；这条请求的原始响应体未保留，不能逐条断言相同原因。'},
        'Q23':{'category':'模型服务HTTP 400','evidence':'报告请求未能完成模型处理，保留了首次失败；未生成可用报告。','next':'服务恢复后按原问题重跑，不用R0成功记录代替本轮结果。'},
        'Q24':{'category':'模型服务错误','evidence':'保留了超时/上游调用失败的原始记录；未完成预期澄清行为。','next':'服务恢复后重跑，不能将保护性兜底当成业务澄清通过。'},
        'Q25':{'category':'模型服务HTTP 400，重放确认Arrearage','evidence':'相同预处理请求重放得到账户欠费/状态受限的Arrearage响应；原任务提前走保守兜底，尚未执行到工作区字段权限分支。','next':'没有泄露结果，但本次任务没有验证到目标分支，仍记未通过。'},
        'G29':{'category':'CTE同名遮蔽权限缺陷，已修复','evidence':'R0受限身份查询无权orders_history，返回计数480；R1和R2拒绝，错误为无权访问。','next':'保留回归测试。'},
        'G30':{'category':'CTE同名遮蔽权限缺陷，已修复','evidence':'R0允许库但无表权限仍读到计数；R1和R2拒绝。','next':'保留回归测试。'},
    }
    write(OUT/'failure-diagnoses.json',diagnoses)
    md=['# AskData：30 条任务与一次失败复盘','',
        '记录日期：2026-10-20。实际使用项目配置的模型服务，任务与原始输出均保留。',
        f"模型：{before_meta['settings']['llm_model']}；Embedding：{before_meta['settings']['embedding_model']}；Rerank：{before_meta['settings']['rerank_model']}。",'',
        '## 结果与统计范围','',
        f'- R0 原始 28 条任务：{base["groups"]["all"]["passed"]}/28；其中数值查询 {base["groups"]["query"]["passed"]}/19、行为检查 {base["groups"]["behavior"]["passed"]}/6、原安全检查 {base["groups"]["guardrails"]["passed"]}/3。',
        '- 阅读执行器后补充两条 CTE 同名遮蔽边界任务，两条均失败。因此扩展任务集的修复前结果为 '+f'{passes0}/30。补充题是针对源码设计的回归案例，不是盲测集。',
        f'- R1 只修改物理表引用的作用域解析，首轮结果为 {passes1}/30；其中服务错误 {len(infra)} 条。',
        '- R1 未通过项保留：5条模型服务错误，另1条退款查询缺少下单时间字段而进入澄清。没有把首次失败重跑后覆盖。',
        f'- 工具链回归：修复前 {unit_before["tests"]} 条中 {unit_before["failures"]} 条失败；修复后 {unit_after["tests"]} 条中 {unit_after["failures"]} 条失败。',
        f'- 完整自动化回归：{full_count} 条，'+('全部通过。' if full_ok else '没有取得完整通过结论，请查看日志。'),
        '- 不能把以上混合任务通过率称为模型问数准确率；报告文字的全部事实、结果排序和字段别名不在自动判定范围。','']
    if confirm:
        md+=['## R2：不改因素的确认复测','']
        md+=['本轮仅重复5条本地安全检查，不调用模型。相同请求诊断返回Arrearage后已停止联网模型调用，未对余额充值或更换模型。模型相关失败尚未补测。','']
        md += [f'- {id}：{label(r)}；{r["reason"]}。保留 R1 首轮结果，不以复测覆盖。' for id,r in sorted(confirm.items())]
        md += ['']
    md += ['## 模型服务中断与未关闭问题','',
        '相同Q25模型请求的诊断重放返回HTTP 400，响应体code和type为Arrearage，说明账户欠费或余额状态受限。证据见upstream-error-diagnosis.json。同期其他HTTP 400可能同源，但没有逐条响应体，超时也独立记录，不统一编造原因。','',
        '这说明R1整体通过数不能直接作为权限修复效果的评价。权限问题通过固定SQL、正反回归和本地再次确认闭环；Q12时间字段漏召回仍是独立应用问题，尚未实施或验证检索修复。','']
    for id,description in diagnoses.items():
        md += [f'### {id}：{description["category"]}','',description['evidence'],'','处理：'+description['next'],'']
    md += ['## 每条任务结果','', '| ID | 场景 | R0 | R1 | R2 确认 | R1 原因 |','|---|---|---|---|---|---|']
    for c in cases:
        r=final[c['id']]
        md.append(f"| {c['id']} | {c['title']} | {label(baseline[c['id']])} | {label(r)} | {label(confirm.get(c['id']))} | {r['reason'].replace('|','/')} |")
    md += ['','## 失败如何定位','',
        '受限测试身份只允许 askdata_mock 的 orders_current 和 customers，不允许 orders_history。下面的非递归 CTE 在 DuckDB 中，内层同名引用解析到物理表，外层引用才解析到 CTE：','',
        '```sql','WITH orders_history AS (','  SELECT order_id FROM orders_history',')','SELECT COUNT(order_id) AS n FROM orders_history;','```','',
        '修复前 G29 返回 success=true，计数为 480；G30 在允许库但无任何表权限的测试身份下，同样通过同名 CTE 读到了当前订单计数。它们证明的是受控表级权限校验缺陷，不能描述为真实用户或生产数据泄露事件。','',
        '原逻辑先收集整个语句的 CTE 名称，再把同名 Table 节点全部排除。这样丢失了 SQL 作用域信息，内层真实表被误认为 CTE 引用，未进入权限检查集合。','',
        '定位时直接用执行器和固定 SQL 复现，不经过模型生成。相同 SQL 在不同权限下的结果，以及 SQLGlot 对内层 Table、外层 Scope 的解析，排除了检索、提示词和模型随机性的影响。','',
        '## 为什么改变方案','',
        '把“全局按名称排除 CTE”改为“按作用域解析物理来源表”。使用项目固定版本 SQLGlot 的 traverse_scope 和 selected_sources，仅把实际解析为 exp.Table 的来源加入权限校验；解析为派生 Scope 的 CTE 引用不当成物理表。','',
        '本轮没有调整模型、温度、检索阈值、提示词或标准答案。修改点只有执行器的物理表识别，同时让作用域解析错误安全返回失败。没有进行过“改提示词但失败”的试验，面试时不能编造这段经历。','',
        '## 怎样验证','',
        '负例：无权历史表同名 CTE、无任何表权限的同名 CTE，必须拒绝；正例：有权物理表与 CTE 同名，以及 CTE 起名为无权表但内部只读取有权表，必须允许。两类都测，避免通过全面禁止 CTE 获得表面通过。','',
        '在修复前源码快照上运行新增回归，11 条中 2 条失败；当前源码 11 条全部通过。之后对 30 条固定任务做 R1 对照，并保留网络失败和后续确认记录。完整回归结果以日志为准。','',
        '## 证据与限制','',
        '- 原始任务：backend/evaluation/cases.v1.json 与 cases.v1.1.json；原始 28 条任务对象未改变。',
        '- 每轮 metadata.json 记录模型、温度、配置、数据哈希和应用源码快照。comparison-conditions.json 验证只有一个应用文件变化。',
        '- 每条 cases/*.json 保存模型输入输出、检索字段、SQL、结果、流式事件和耗时；gold.json 保存标准 SQL 与结果。',
        '- 同一评分器 v2 复算各轮；保留原始评分。修正金额包含“429”导致错误分类的问题，属于评测工具修正，不算应用改进。',
        '- 金额绝对容差 0.011，超过两位小数的小比率容差 0.00011；忽略行顺序、列顺序和中文别名。应结合 SQL 人工检查指标对应关系。',
        '- 实测使用 3 个任务并发，且 R0 与 R1 有短暂重叠；不据此宣称性能提升、线上吞吐或 P95。',
        '- 温度为 0 仍不能保证远端模型完全确定；单次小样本对照不能外推总体准确率。',
        '- r0-baseline 为沙箱连接被拒的预检，不计入业务基线。测试进程曾因沙箱限制阻塞本地异步套接字，正常权限下重新执行；环境问题不算应用失败。','']
    (OUT/'评测记录与面试复盘.md').write_text('\n'.join(md),encoding='utf8')
    speech=f'''我给项目整理了一组固定测试任务，先覆盖销售汇总、客户分层、多表关联、退款和目标进度，再检查连续解释、报告来源和权限边界。每条查询都有人工确定的参考 SQL，模型生成的结果要和实际计算出的标准值比较，同时保留模型响应、检索字段、执行 SQL 和错误信息。

最初的二十八条都通过了。但我没有据此认为权限已经没有问题，而是在阅读 SQL 校验代码后，又补了两条 CTE 同名遮蔽测试，两条都失败了。

复现时，我设置了一个只允许当前订单和客户表、不允许历史订单表的测试身份。查询把 CTE 也命名为历史订单表。结果执行器仍返回成功，读到了历史订单四百八十条记录的计数。这是测试环境中发现的表级权限校验缺陷，没有真实生产用户受影响的证据。

我把同一条 SQL 直接交给执行器复现，发现不经过模型也会出现，所以定位重点转到了执行前的 SQL 校验。原来的实现把整个语句中的 CTE 名称收集起来，再把所有同名表引用排除。问题是，非递归 CTE 的内部同名引用仍可能指向物理表，它被一起排除了，导致后面的权限检查没看到这张表。

因此，我把全局名称过滤改成按 SQL 作用域解析物理来源表。内层实际读取的物理表要检查权限，外层引用 CTE 的派生结果不重复当成物理表。本轮只改这一处识别逻辑，模型、提示词、检索参数和任务标准答案都保持不变。

验证时我同时补了负例和正例。无权表同名遮蔽、空表权限的查询必须拒绝；合法的同名 CTE 和只读取有权表的嵌套 CTE 必须继续成功。相关工具链十一条回归在修复前有两条失败，修复后全部通过；然后又把三十条任务跑了一轮对照。

修复后的首轮是{passes1}条通过、{30-passes1}条未通过。其中五条是模型服务错误，另有一条退款查询漏掉了下单时间字段，进入了澄清。后者仍是独立的待修复问题。

对于服务错误，我又重放了一条相同请求，拿到了Arrearage，也就是账户欠费或余额状态受限的明确响应，因此停止了继续调用模型。没有拿基线成功结果替换这次失败，也没有把整体通过数下降归因到权限修复。后续只对五条本地安全检查做了不改代码和配置的确认，五条都通过。模型相关的失败还需要在服务恢复后补测。{'完整自动化回归的'+str(full_count)+'条也全部通过了。' if full_ok else ''}

这次迭代让我形成的做法是：先区分标准答案、评分器、模型和程序各自的问题，再用最小复现定位。涉及执行权限的约束，要落到确定性的校验代码上；验证时既证明错误被拦截，也证明合法查询没有被误伤。小样本测试说明的是这组任务的结果，不能直接说成模型总体准确率或生产安全保证。'''
    (OUT/'面试口述稿.md').write_text('# 一次失败如何定位、修改和验证\n\n'+speech+'\n',encoding='utf8')
    data=[]
    for c in cases:
        records={}
        for name in ['r0-live','r0-edge','r1-scope','r2-confirm']:
            r=raw(name,c['id'])
            if r:
                records[name]={'seconds':r['seconds'],'reason':r.get('reason'),'result':r.get('result'),'setup_result':r.get('setup_result'),'missing_required_fields':r.get('missing_required_fields'),'model_call_errors':[{'type':m['type'],'error':m['error'],'seconds':m['seconds']} for m in r.get('model_calls',[]) if m.get('error')],'raw_link':f'{name}/cases/{c["id"]}.json'}
        data.append({'case':c,'before':baseline[c['id']],'after':final[c['id']],'confirm':confirm.get(c['id']),'records':records,'diagnosis':diagnoses.get(c['id'])})
    payload={'cases':data,'conditions':conditions,'passes0':passes0,'passes1':passes1,'infra':len(infra),'full_count':full_count,'full_ok':full_ok,'service_diagnosis':service_diagnosis}
    write(OUT/'comparison.json',payload)
    with (OUT/'任务结果.csv').open('w',encoding='utf-8-sig',newline='') as f:
        w=csv.writer(f);w.writerow(['ID','场景','类型','问题','标准SQL','R0','R1','R2','R1原因'])
        for c in cases:w.writerow([c['id'],c['title'],c['kind'],c.get('query','确定性后端边界检查'),c.get('gold_sql',''),label(baseline[c['id']]),label(final[c['id']]),label(confirm.get(c['id'])),final[c['id']]['reason']])
    template=(EVAL/'report.template.html').read_text(encoding='utf8')
    (OUT/'index.html').write_text(template.replace('__PAYLOAD__',json.dumps(payload,ensure_ascii=False,default=str).replace('<','\\u003c')),encoding='utf8')
    print(json.dumps({'before':passes0,'after_first_run':passes1,'total':30,'infra':len(infra),'full_regression':full_count,'full_ok':full_ok,'report':str(OUT/'index.html')},ensure_ascii=False))

if __name__=='__main__':main()
