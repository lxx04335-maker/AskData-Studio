# 演示数据与本机运行状态

本目录只提交这份说明。CSV、生成的 Schema、索引、会话归档和已存资产不提交；请不要将真实业务数据放入公开仓库。

在仓库根目录执行：

```powershell
backend/.venv/Scripts/python.exe backend/seed_demo_data.py
backend/.venv/Scripts/python.exe backend/scripts/generate_ecommerce_data.py
backend/.venv/Scripts/python.exe backend/scripts/generate_ecommerce_schema.py
backend/.venv/Scripts/python.exe backend/scripts/validate_ecommerce_data.py
```

生成命令会覆盖对应的演示文件；已有自定义数据时先另行备份，不要直接重跑。先生成 CSV，再生成 Schema，再启动后端；Schema 在模块导入时读取，更新后需要重启。

| 数据库 | 生成来源 | 固定规模/日期 |
| --- | --- | --- |
| `askdata_mock` | `app/demo_data.py`，seed `20260815` | 5表770行：客户30、产品12、当前订单240、历史订单480、目标8；订单时间2026年6月至8月 |
| `ecommerce_ops` | `scripts/generate_ecommerce_data.py`，seed `20260823` | 50表381,963行；2025-01-01至2026-08-22；Schema 生成后392字段、79条关系 |

名字、邮箱、掩码手机号、地址及交易记录来自脚本合成，不是采集的真实客户信息；生成数据用于演示和本项目固定任务，不等于外部基准或真实业务评测集。旧数据是否与生成结果一致应比较逐文件 SHA-256；固定随机种子本身不能证明字节完全一致。

运行后还可能生成：

- `schema_store.json`：字段文档、样例值与向量缓存；首次混合检索会调用外部 Embedding，后续按 Schema 签名和模型名决定是否复用。
- `session_archive.db`：本机 SQLite 对话、结果与摘要，可能包含用户输入和业务数据。
- `saved_memories.json`：按用户保存的字段与表格资产。

上述文件全部忽略。`SESSION_ARCHIVE_PATH` 相对路径以 `backend` 为基准；默认归档开启。不要用已有本机历史或索引充当公开示例。
