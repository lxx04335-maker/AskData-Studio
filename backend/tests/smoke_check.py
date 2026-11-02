"""手动运行：使用当前 .env 完成一次端到端查询。"""

from dataclasses import replace

from app.config import settings
from app.model_client import ModelClient
from app.retrieval import SchemaIndex
from app.service import AskDataService


if __name__ == "__main__":
    config = replace(settings, session_archive_enabled=False, short_term_summary_enabled=False)
    client = ModelClient(config)
    service = AskDataService(client, SchemaIndex(client, config))
    result = service.submit(
        "只查询 askdata_mock：统计2026年8月各地区实付金额合计，不额外筛选状态。",
        "smoke-session",
        user_id="demo_analyst",
    )
    if result.status != "completed" or not result.sql or not result.rows:
        raise SystemExit("未完成真实问数；请检查 API 配置、服务可用性和结果状态。")
    print(
        {
            "status": result.status,
            "route": result.route,
            "coder_calls": len(result.tool_calls),
            "sql_executed": bool(result.sql),
            "row_count": len(result.rows),
            "schema_threshold": (result.retrieval or {}).get("threshold"),
            "embedding_source": (result.retrieval or {}).get("embedding_source"),
            "rerank_source": (result.retrieval or {}).get("rerank_source"),
        }
    )
