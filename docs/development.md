# 开发说明

## macOS / Linux 启动

从项目根目录执行：

```bash
python3.11 -m venv backend/.venv
backend/.venv/bin/python -m pip install -r backend/requirements.txt
test -f backend/.env || cp backend/.env.example backend/.env
```

填写 `backend/.env` 中的接口地址、模型和密钥，然后生成示例数据：

```bash
backend/.venv/bin/python backend/seed_demo_data.py
backend/.venv/bin/python backend/scripts/generate_ecommerce_data.py
backend/.venv/bin/python backend/scripts/generate_ecommerce_schema.py
backend/.venv/bin/python backend/scripts/validate_ecommerce_data.py
cd frontend
npm ci
npm run build
cd ../backend
.venv/bin/python run.py
```

## 前端热更新

修改前端时，分别打开两个终端。后端在 `backend` 目录启动：

```powershell
.venv/Scripts/python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

前端在 `frontend` 目录启动：

```powershell
npm.cmd run dev
```

访问 `http://127.0.0.1:5173/`，Vite 会将 `/api` 代理到后端。默认不需要前端 `.env`，要连接其他地址时可复制 `frontend/.env.example` 并设置 `VITE_API_BASE`。模型密钥放在后端。

## 直接调用 API

服务启动后，在 PowerShell 中登录、新建对话并提交问题：

```powershell
$base = 'http://127.0.0.1:8000'
$login = Invoke-RestMethod "$base/api/auth/login" -Method Post -ContentType 'application/json' -Body '{"username":"mock","password":"mock123"}'
$headers = @{ Authorization = "Bearer $($login.access_token)" }
$chat = Invoke-RestMethod "$base/api/conversations" -Method Post -Headers $headers
$body = @{
    query = '统计2026年8月各地区的实付金额合计，按金额从高到低排序，不额外筛选订单状态。'
    session_id = $chat.id
} | ConvertTo-Json -Compress
$result = Invoke-RestMethod "$base/api/query" -Method Post -Headers $headers -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
$result | Select-Object status,route,sql,rows,analysis
```

页面使用 `POST /api/query/stream` 接收 SSE 消息，包括进度、文字、结果表和最终任务结果。需要用户确认时，返回 `waiting_clarification` 和选项；将选项ID提交给 `POST /api/tasks/{task_id}/clarify`，请求体为 `{"option_id":"选项ID"}`。

## 从哪里读代码

| 想看什么 | 入口 |
| --- | --- |
| HTTP接口和会话调用 | [routes.py](../backend/app/api/routes.py)、[askdata_service.py](../backend/app/services/askdata_service.py) |
| 意图判断、独立问题和检索词 | [preprocessing.py](../backend/app/preprocessing.py) |
| LangGraph节点、状态和澄清恢复 | [query_graph.py](../backend/app/workflows/query_graph.py)、[state.py](../backend/app/workflows/state.py) |
| 字段召回、融合、重排 | [service.py](../backend/app/retrieval/service.py)、[store.py](../backend/app/retrieval/store.py) |
| 关联路径和连接字段 | [graph.py](../backend/app/retrieval/graph.py) |
| Skill组织和SQL Agent | [registry.py](../backend/app/skills/registry.py)、[single_database_agent.py](../backend/app/querying/single_database_agent.py) |
| MCP工具注册与调用 | [server.py](../backend/app/mcp_runtime/server.py)、[client.py](../backend/app/mcp_runtime/client.py) |
| SQL校验与CSV查询 | [duckdb_engine.py](../backend/app/querying/duckdb_engine.py) |
| 对话、后台摘要和历史恢复 | [session_context.py](../backend/app/services/session_context.py)、[short_term_memory.py](../backend/app/services/short_term_memory.py)、[session_archive.py](../backend/app/services/session_archive.py) |
| 图表和报告 | [data_qa_agent.py](../backend/app/querying/data_qa_agent.py)、[components](../frontend/src/components) |

检索参数在 `backend/.env.example` 中：BM25、Dense默认各取30个候选，RRF取40个，重排阈值0.55，最多保留20个字段。本地JSON保存索引和向量，`SchemaGraphBuilder` 根据已定义的表关系补连接路径。

Skill目录里各有一份 `SKILL.md` 和 `config.json`。前者写模型指令，后者定义允许的动作、工具名称模式和调用次数；Agent读取配置后执行这些检查。MCP服务根据账号权限注册数据库工具，执行查询时再检查访问范围。

历史保存在 `backend/data/session_archive.db`，已存字段和表格在 `saved_memories.json`，向量索引在 `schema_store.json`。这些文件由程序生成，不加入版本控制。

## 验证命令

准备好两套数据后，在根目录运行离线测试：

```powershell
backend/.venv/Scripts/python.exe backend/scripts/verify_offline.py
```

使用实际模型配置检查接口和问数，在 `backend` 执行：

```powershell
.venv/Scripts/python.exe -m tests.connectivity_check
.venv/Scripts/python.exe -m tests.smoke_check
```

这两个命令会调用外部模型。固定任务集、标准SQL和历史评测说明见 [evaluation](../backend/evaluation/README.md)。
