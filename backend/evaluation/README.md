# 固定任务评测与历史证据

本目录包含固定任务集、标准 SQL、运行与评分脚本。这是小规模项目评测，不是生产流量或公开榜单。**历史结果与当前版本验证分开记录。**

## 统计单位和评分

- `cases.v1.json`：28任务，19条数值查询、6条行为任务、3条后端安全检查。
- `cases.v1.1.json`：原28题不变，补2条同名 CTE 边界安全检查，共30题。
- 查询的人工标准 SQL 在当前 CSV 上执行得到完整期望行，不由模型生成标准答案。
- 19题标注共49个“题目–必需字段”对，同一字段出现在不同题时分别计数。
- 数值匹配忽略行/列顺序、中文别名。金额绝对误差0.011；绝对值小于100且标准值超过两位小数的数值，误差收紧为0.00011，不使用相对误差。
- 行内文本与数值分别比较多重集，不验证列名含义、字段对应关系和排序，语义仍需人工复核。SQL 可执行、返回非空不单独等于正确。
- 解释/报告自动检查路由、来源任务、图表字段及结果复用，不证明所有文字事实正确。
- 行为、安全、数值查询分别统计，服务错误和前置任务失败保留在记录中。

评分源码：`run.py` 的 `rows_equal`、`judge`，对应6条检查在 `test_scoring.py`。曾修正“金额中的429被当成HTTP错误”的分类问题，历史轮次统一用评分器v2复算并保留原判定；这不是应用效果提升。

## 历史评测记录

已从本地 `output/evaluation-20261020/` 提取公开历史摘录到 [evidence/2026-10-20](evidence/2026-10-20)。每轮保留任务定义、标准答案、公开模型配置与源码/数据哈希、逐题评分必需输入、审计汇总。模型完整提示、流式事件、向量与个人会话归档未公开。`provenance.json` 记录原文件 SHA-256；摘录不是完整原始运行包，也不是当前版本结果。

| 指标 | 证据 | 判定与边界 |
| --- | --- | --- |
| 17/19，89.47% | `r1-scope/audited-summary.json` 与逐题 `cases/Qxx.json` | 19条有标准 SQL 的查询中17条数值匹配；Q04服务失败、Q12意外澄清保留在分母 |
| 45/49，91.84% | 同轮任务的 `required_fields` 与 `result.retrieval.hits[].doc_id` | 对19题求 `sum(必需字段 ∩ 命中字段) / sum(必需字段)`，微平均；不计 BFS 图补全后的字段；服务失败的空命中计入 |
| 71/71 | `full-regression-after.txt` 及对应 JSON | 2026-10-20历史 unittest 日志；缺少完整依赖快照和全部测试源码绑定，不能证明当前版本或任意环境也通过 |

在 backend 目录离线复算：

```powershell
.venv/Scripts/python.exe -m evaluation.verify_evidence
.venv/Scripts/python.exe -m unittest evaluation.test_scoring -v
```

`verify_evidence` 重调当前数值/行为评分器、核对历史判定与45/49；安全任务和异常记录只检查保存的断言，**不重演历史执行**。当前回归使用 `python scripts/verify_offline.py`。

## 历史轮次和失败案例

| 轮次 | 保存结果 | 限定解释 |
| --- | --- | --- |
| `r0-baseline` | WinError 10013 阻止模型连接 | 不作业务基线，完整记录仅保留本地 |
| `r0-live` | 原28任务通过，查询19/19，字段48/49 | 不同轮次不能挑选最佳值代表稳定效果 |
| `r0-edge` | 新补的2条同名 CTE 检查失败 | 源码审阅后追加，不是盲测样本 |
| `r1-scope` | 30条已尝试：24通过、6未通过，查询17/19 | CTE物理表作用域修复；5条模型/依赖错误，Q12缺时间字段而澄清 |
| `r2-confirm` | 只重跑5条安全检查，5通过、模型调用0 | 不能据此宣称全部30题通过 |

Q12：退款订单去重问题要求2026-08-01至22日下单，但未召回 `ecommerce_ops.orders.ordered_at`，Agent 无法确认下单时间而澄清。BFS 只能补连接路径和键，不能保证补齐时间过滤字段。

Q04 历史趋势查询受外部服务故障影响；Q22–Q25包含解释/报告任务，受模型或前置查询失败影响。历史诊断记录 HTTP 400 / Arrearage 后停止模型调用；这不解释本次2026-11-02 Chat连接关闭的原因。

旧执行器仅排除 CTE 名称会漏掉 CTE 内的同名真实表，现通过 `traverse_scope` 判断物理来源，11条工具链测试覆盖允许与拒绝场景；不等于完整 SQL 安全审计。

## 当前版本重新评测

先按根 README 生成两个库、配置模型。在 backend 目录：

```powershell
.venv/Scripts/python.exe -m tests.connectivity_check
# 使用新轮次名，有模型调用费用
.venv/Scripts/python.exe -m evaluation.run --round current-baseline --cases cases.v1.1.json --workers 3
.venv/Scripts/python.exe -m evaluation.audit current-baseline
```

输出沿用忽略的 `output/evaluation-20261020/<round>/`；目录名不是新运行日期，每轮 metadata 另存UTC时间、源码/数据哈希。不要覆盖历史，仅条件完全相同时可用 `--resume`。

不联网重跑5条安全检查（先初始化数据）：

```powershell
.venv/Scripts/python.exe -m evaluation.run --round current-guards --cases cases.v1.1.json --ids G26,G27,G28,G29,G30 --workers 1
```

纯安全子集不构建向量索引。普通评测首次可从零建索引，不依赖提交本机 `schema_store.json`。每题使用新服务/会话，统一关闭归档与异步摘要；并发3不是吞吐测试。

服务恢复后用新轮次复测失败集：

```powershell
.venv/Scripts/python.exe -m evaluation.run --round service-restored --cases cases.v1.1.json --ids Q04,Q12,Q22,Q23,Q24,Q25 --workers 3
```

默认示例配置与历史模型不同；严格对照需使用历史 metadata 的模型、参数和数据哈希，并确认服务仍提供该模型。公开摘录没有完整旧应用快照，不能逐字重建旧执行过程。

`report.py`、`finalize.py`、`run_regression.py --before`、`check_model_error.py` 是依赖本机完整原始归档的历史工具，公开摘录不满足它们全部输入。`check_report.cjs` 只检查本地历史HTML，已移除个人路径；可选依赖为 Playwright和Chromium，未纳入核心安装，本次未重跑历史页面检查。

后续保持题目、标准 SQL 和数据固定，一轮只改一个因素，保留失败与对照题；小样本波动不能外推总体准确率。
