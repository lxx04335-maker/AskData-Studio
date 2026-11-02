import unittest
from unittest.mock import patch

from app.config import Settings
from app.model_client import ModelClient


class RerankProtocolTest(unittest.TestCase):
    def test_qwen37_uses_dashscope_request_and_response(self) -> None:
        config = Settings(
            api_key="test",
            rerank_model="qwen3.7-text-rerank",
            rerank_base_url="https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1",
        )
        client = ModelClient(config)
        response = {
            "output": {
                "results": [
                    {"index": 1, "relevance_score": 0.9},
                    {"index": 0, "relevance_score": 0.3},
                ]
            }
        }

        with patch.object(client, "_post", return_value=response) as post:
            ranking = client.rerank("销售额", ["客户名称", "实付金额"], 2)

        self.assertEqual(ranking, [(1, 0.9), (0, 0.3)])
        url, payload = post.call_args.args
        self.assertEqual(
            url,
            "https://ws-example.cn-beijing.maas.aliyuncs.com/api/v1/"
            "services/rerank/text-rerank/text-rerank",
        )
        self.assertEqual(payload["input"], {
            "query": "销售额", "documents": ["客户名称", "实付金额"]
        })
        self.assertEqual(payload["parameters"]["top_n"], 2)

    def test_existing_qwen3_keeps_compatible_request(self) -> None:
        config = Settings(
            api_key="test",
            rerank_model="qwen3-rerank",
            rerank_base_url="https://dashscope.aliyuncs.com/compatible-api/v1",
        )
        client = ModelClient(config)

        with patch.object(client, "_post", return_value={"results": [{"index": 0, "relevance_score": 0.8}]}) as post:
            ranking = client.rerank("销售额", ["实付金额"], 1)

        self.assertEqual(ranking, [(0, 0.8)])
        url, payload = post.call_args.args
        self.assertEqual(url, "https://dashscope.aliyuncs.com/compatible-api/v1/reranks")
        self.assertEqual(payload["query"], "销售额")
        self.assertNotIn("input", payload)
