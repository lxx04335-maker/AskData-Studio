from __future__ import annotations

import json
import math
import re
import time
import urllib.error
import urllib.request
from typing import Any

from .config import Settings, settings
from .streaming import emit, event_sink, partial_strings


class ModelClient:
    """Chat、Embedding 和 Rerank 接口客户端。"""

    def __init__(self, config: Settings | None = None) -> None:
        self.config = config or settings
        self._blocked_until: dict[str, float] = {}

    @property
    def enabled(self) -> bool:
        return bool(self.config.api_key)

    def chat(self, system: str, user: str, *, temperature: float | None = None) -> str:
        payload = {
            "model": self.config.llm_model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": self.config.temperature if temperature is None else temperature,
            "stream": False,
        }
        result = self._post(self.config.chat_url, payload)
        try:
            return str(result["choices"][0]["message"]["content"]).strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise RuntimeError("大模型响应缺少 message.content") from exc

    def chat_json(self, system: str, user: str) -> dict[str, Any]:
        fields = (
            {"response"} if system.startswith("你是问数系统的请求预处理器") else
            {"answer", "markdown", "title"} if system.startswith("你是数据问答智能体") else
            {"analysis"} if system.startswith("你是查询结果整理器") else set()
        )
        raw = self._stream_json(system, user, fields) if event_sink.get() and fields else self.chat(system, user)
        cleaned = re.sub(r"^```(?:json)?\s*|\s*```$", "", raw.strip(), flags=re.I)
        try:
            return json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{[\s\S]*\}", cleaned)
            if not match:
                raise RuntimeError("大模型未返回有效 JSON")
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError as exc:
                raise RuntimeError("大模型未返回有效 JSON") from exc

    def _stream_json(self, system: str, user: str, fields: set[str]) -> str:
        if not self.config.api_key:
            raise RuntimeError("LLM_API_KEY 尚未配置")
        payload = {
            "model": self.config.llm_model,
            "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
            "temperature": self.config.temperature,
            "stream": True,
        }
        request = urllib.request.Request(
            self.config.chat_url, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={"Authorization": f"Bearer {self.config.api_key}", "Content-Type": "application/json"},
            method="POST",
        )
        raw = ""
        previous: dict[str, str] = {}
        done = False
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                for line in response:
                    line = line.decode("utf-8").strip()
                    if not line.startswith("data:"):
                        continue
                    data = line[5:].strip()
                    if data == "[DONE]":
                        done = True
                        break
                    item = json.loads(data)
                    if item.get("error"):
                        raise RuntimeError(str(item["error"]))
                    choices = item.get("choices") or []
                    if not choices:
                        continue
                    if choices[0].get("finish_reason") == "stop":
                        done = True
                    raw += choices[0].get("delta", {}).get("content") or ""
                    for key, value in partial_strings(raw).items():
                        if key in fields and value != previous.get(key, ""):
                            previous[key] = value
                            # Snapshots avoid duplicated text and safely reconcile escapes across chunks.
                            emit("text", field=key, text=value)
        except (urllib.error.URLError, TimeoutError, ValueError, OSError) as exc:
            raise RuntimeError(f"模型流式响应失败：{exc}") from exc
        if not done:
            raise RuntimeError("模型流式响应意外中断，请重试")
        return raw

    def embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors: list[list[float]] = []
        for start in range(0, len(texts), 10):
            payload = {
                "model": self.config.embedding_model,
                "input": texts[start : start + 10],
                "dimensions": self.config.embedding_dimensions,
                "encoding_format": "float",
            }
            result = self._post(self.config.embeddings_url, payload)
            items = sorted(result.get("data", []), key=lambda item: item.get("index", 0))
            vectors.extend(self._normalize(item["embedding"]) for item in items)
        if len(vectors) != len(texts):
            raise RuntimeError("Embedding 返回数量与输入不一致")
        return vectors

    def rerank(self, query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
        if not documents:
            return []
        instruction = "Rank database schema fields by relevance to the user's analytics query."
        if self.config.rerank_model == "qwen3.7-text-rerank":
            payload = {
                "model": self.config.rerank_model,
                "input": {"query": query, "documents": documents},
                "parameters": {"top_n": min(top_n, len(documents)), "instruct": instruction},
            }
        else:
            payload = {
                "model": self.config.rerank_model,
                "query": query,
                "documents": documents,
                "top_n": min(top_n, len(documents)),
                "instruct": instruction,
            }
        result = self._post(self.config.rerank_url, payload)
        if self.config.rerank_model == "qwen3.7-text-rerank":
            results = result.get("output", {}).get("results", [])
        else:
            results = result.get("results", [])
        output = [
            (int(item["index"]), float(item.get("relevance_score", item.get("score", 0))))
            for item in results
        ]
        return sorted(output, key=lambda item: item[1], reverse=True)

    def _post(self, url: str, payload: dict[str, Any]) -> dict[str, Any]:
        if not self.config.api_key:
            raise RuntimeError("LLM_API_KEY 尚未配置")
        if time.monotonic() < self._blocked_until.get(url, 0):
            raise RuntimeError("模型接口暂时处于30秒熔断窗口，请稍后重试")
        request = urllib.request.Request(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.config.api_key}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        last_error: Exception | None = None
        for attempt in range(self.config.max_retries + 1):
            try:
                with urllib.request.urlopen(request, timeout=self.config.timeout) as response:
                    return json.loads(response.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                detail = exc.read().decode("utf-8", errors="replace")[:500]
                last_error = RuntimeError(f"模型接口返回 HTTP {exc.code}: {detail}")
                if exc.code < 500 and exc.code != 429:
                    break
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
            if attempt < self.config.max_retries:
                time.sleep(0.4 * (2**attempt))
        # 网络请求失败后对当前接口启用短时熔断。
        self._blocked_until[url] = time.monotonic() + 30
        raise RuntimeError(f"模型接口调用失败: {last_error}") from last_error

    @staticmethod
    def _normalize(vector: list[float]) -> list[float]:
        norm = math.sqrt(sum(value * value for value in vector)) or 1.0
        return [float(value) / norm for value in vector]
