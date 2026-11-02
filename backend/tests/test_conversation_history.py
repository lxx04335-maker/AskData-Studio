"""Offline regression checks: temporary data, fake models, no external HTTP."""
from __future__ import annotations

import json
import socket
import tempfile
import unittest
from pathlib import Path
from threading import Event
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import Settings
from app.demo_data import seed_demo_data
from app.models import QueryResult
from app.retrieval import SchemaIndex
from app.services.askdata_service import AskDataService
from app.services.memory_store import MemoryStore
from app.services.session_archive import SessionArchive
from app.services.session_context import SessionContext
from tests.test_service import FakeModelClient


class ConversationHistoryTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        for target in ("urllib.request.urlopen", "socket.create_connection"):
            guard = patch(target, side_effect=AssertionError("External network disabled in offline tests"))
            mock = guard.start()
            self.addCleanup(guard.stop)
            self.addCleanup(mock.assert_not_called)
        # Python 3.11 on Windows uses a loopback socket pair to wake its event loop.
        original_connect = socket.socket.connect
        external_connections: list[object] = []
        def connect(sock: socket.socket, address: object) -> None:
            if isinstance(address, tuple) and address[0] in {"127.0.0.1", "::1"}:
                return original_connect(sock, address)
            external_connections.append(address)
            raise AssertionError("External network disabled in offline tests")
        socket_patch = patch("socket.socket.connect", new=connect)
        socket_patch.start()
        self.addCleanup(socket_patch.stop)
        self.addCleanup(self.assertEqual, external_connections, [])
        database_patch = patch("app.querying.duckdb_engine.DATABASE_ROOT", self.root / "databases")
        database_patch.start()
        self.addCleanup(database_patch.stop)
        seed_demo_data(database_dir=self.root / "databases" / "askdata_mock")
        self.config = Settings(api_key="", embedding_dimensions=32, session_archive_enabled=True,
                               session_archive_path=str(self.root / "history.db"), short_term_summary_enabled=False)
        self.service = self.new_service()

    def new_service(self) -> AskDataService:
        model = FakeModelClient()
        service = AskDataService(model, SchemaIndex(model, self.config, self.root / "schema.json"))
        service.memories = MemoryStore(self.root / "assets.json")
        self.addCleanup(service.context.short_term_memory._executor.shutdown, wait=True)
        return service

    def test_empty_chat_and_selected_fields_survive_restart(self) -> None:
        chat = self.service.create_conversation("demo_admin")
        workspace = {"schema_fields": [{"name": "region", "tableId": "orders_current"}],
                     "analysis_table_ids": []}
        self.service.update_conversation(chat["id"], "demo_admin", workspace)
        restored = self.new_service()
        self.assertEqual(restored.list_conversations("demo_analyst"), [])
        self.assertEqual(restored.list_conversations("demo_admin")[0]["id"], chat["id"])
        detail = restored.get_conversation(chat["id"], "demo_admin")
        self.assertEqual(detail["turns"], [])
        self.assertEqual(detail["workspace"]["schema_fields"][0]["name"], "region")

    def test_sql_results_summary_and_followup_survive_restart(self) -> None:
        user = "demo_analyst"
        chat = self.service.create_conversation(user)
        result = self.service.submit("查询本月各地区销售额", chat["id"], user_id=user)
        self.service.context.archive.save_summary(f"{user}:{chat['id']}", "已按地区统计销售额", {result.task_id})
        self.service.save_memory(result.task_id, user)
        before = self.service.get_conversation(chat["id"], user)
        restored = self.new_service()
        detail = restored.get_conversation(chat["id"], user)
        self.assertEqual(detail["title"], "查询本月各地区销售额")
        self.assertEqual(detail["turns"][0]["result"]["sql"], result.sql)
        self.assertEqual(detail["turns"][0]["result"]["rows"], result.rows)
        self.assertEqual(detail["turns"][0]["result_at"], before["turns"][0]["result_at"])
        self.assertTrue(detail["turns"][0]["result_at"].endswith("+00:00"))
        self.assertTrue(detail["turns"][0]["result"]["saved"])
        self.assertEqual(restored.get_task(result.task_id, user).sql, result.sql)
        with self.assertRaises(PermissionError):
            restored.get_task(result.task_id, "demo_admin")
        summary = json.loads(restored.context.short_term_context(f"{user}:{chat['id']}"))
        self.assertEqual(summary["history_summary"], "已按地区统计销售额")
        followup = restored.submit("分析刚才的结果", chat["id"], user_id=user)
        self.assertEqual(followup.route, "data_qa")
        self.assertIn("复用了", followup.analysis)
        self.assertEqual(restored.get_conversation(chat["id"], user)["turns"][-1]["query"], "分析刚才的结果")

    def test_clarification_messages_have_independent_snapshots(self) -> None:
        chat = self.service.create_conversation("demo_analyst")
        waiting = self.service.submit("歧义查询各地区销售额", chat["id"])
        self.assertTrue(self.service.get_conversation(chat["id"], "demo_analyst")["turns"][0]["can_resume"])
        self.service.clarify(waiting.task_id, "orders_current")
        turns = self.new_service().get_conversation(chat["id"], "demo_analyst")["turns"]
        self.assertEqual(len(turns), 2)
        self.assertEqual(turns[0]["result"]["status"], "waiting_clarification")
        self.assertTrue(turns[0]["resolved"])
        self.assertFalse(turns[0]["can_resume"])
        self.assertEqual(turns[1]["query"], waiting.clarification.options[0].label)
        self.assertEqual(turns[1]["result"]["status"], "completed")

    def test_same_public_id_has_separate_owners_after_restart(self) -> None:
        admin = self.service.submit("你好，管理员", "same-id", user_id="demo_admin")
        analyst = self.service.submit("你好，分析员", "same-id", user_id="demo_analyst")
        restored = self.new_service()
        self.assertEqual(restored.get_conversation("same-id", "demo_admin")["turns"][0]["query"], "你好，管理员")
        self.assertEqual(restored.get_conversation("same-id", "demo_analyst")["turns"][0]["query"], "你好，分析员")
        self.assertEqual(restored.get_task(admin.task_id, "demo_admin").task_id, admin.task_id)
        with self.assertRaises(PermissionError):
            restored.get_task(analyst.task_id, "demo_admin")

    def test_pending_task_after_restart_requires_new_question(self) -> None:
        waiting = self.service.submit("歧义查询各地区销售额", "pending")
        restored = self.new_service()
        turn = restored.get_conversation("pending", "demo_analyst")["turns"][0]
        self.assertFalse(turn["can_resume"])
        with self.assertRaisesRegex(ValueError, "重新提问"):
            restored.clarify(waiting.task_id, "orders_current")
        fresh = restored.submit("查询本月各地区销售额", "pending")
        self.assertEqual(fresh.status, "completed")
        self.assertNotEqual(fresh.task_id, waiting.task_id)

    def test_interrupted_clarification_does_not_copy_previous_reply(self) -> None:
        waiting = self.service.submit("歧义查询各地区销售额", "interrupted")
        history = self.service.context.history
        history.begin("demo_analyst:interrupted", "demo_analyst", "当前订单", {}, waiting.task_id)
        history.end("demo_analyst:interrupted")
        turns = self.new_service().get_conversation("interrupted", "demo_analyst")["turns"]
        self.assertEqual(turns[0]["result"]["status"], "waiting_clarification")
        self.assertIsNone(turns[1]["result"])
        self.assertIn("未完成", turns[1]["error"])

    def test_deleted_chat_does_not_return_and_saved_asset_remains(self) -> None:
        result = self.service.submit("查询本月各地区销售额", "delete-me")
        self.service.save_memory(result.task_id)
        self.service.context.archive.save_summary("demo_analyst:delete-me", "摘要", {result.task_id})
        self.service.delete_conversation("delete-me", "demo_analyst")
        self.assertNotIn(result.task_id, self.service.tasks)
        self.assertFalse(self.service.workflow.checkpointer.get_tuple({"configurable": {"thread_id": result.task_id}}))
        restored = self.new_service()
        self.assertEqual(restored.list_conversations("demo_analyst"), [])
        self.assertEqual(restored.context.archive.load_turns(), [])
        self.assertEqual(restored.context.archive.load_messages("demo_analyst:delete-me"), [])
        self.assertEqual(restored.context.archive.load_summaries(), [])
        self.assertEqual(len(restored.memories.list("demo_analyst")), 1)

    def test_legacy_archive_is_migrated_with_correct_owner(self) -> None:
        archive = SessionArchive(self.root / "legacy.db")
        result = QueryResult(task_id="legacy", status="completed", route="data_qa", message="完成", analysis="旧回答")
        archive.save_turn("legacy", "demo_admin:old-chat", "旧问题", {}, result.model_dump(mode="json"))
        config = Settings(api_key="", session_archive_enabled=True,
                          session_archive_path=str(archive.path), short_term_summary_enabled=False)
        context = SessionContext(FakeModelClient(), config)
        self.addCleanup(context.short_term_memory._executor.shutdown, wait=True)
        self.assertEqual(context.history.list("demo_analyst"), [])
        self.assertEqual(context.tasks["legacy"]["user_id"], "demo_admin")
        self.assertEqual(context.conversation_detail("demo_admin:old-chat", "demo_admin")["turns"][0]["result"]["analysis"], "旧回答")

    def test_delete_rejects_running_request_and_interrupted_turn_is_visible(self) -> None:
        history = self.service.context.history
        history.begin("demo_analyst:busy", "demo_analyst", "问题", {}, "unfinished")
        with self.assertRaises(ValueError):
            self.service.delete_conversation("busy", "demo_analyst")
        history.end("demo_analyst:busy")
        turn = self.new_service().get_conversation("busy", "demo_analyst")["turns"][0]
        self.assertIsNone(turn["result"])
        self.assertIn("未完成", turn["error"])

    def test_api_requires_login_and_isolates_history_even_from_admin(self) -> None:
        # Import only the router, with its global service replaced before construction.
        with patch("app.services.askdata_service.AskDataService", return_value=self.service):
            from app.api import routes
        app = FastAPI()
        app.include_router(routes.router)
        with patch.object(routes, "service", self.service), TestClient(app) as client:
            self.assertEqual(client.get("/api/conversations").status_code, 401)
            def headers(username: str) -> dict:
                token = client.post("/api/auth/login", json={"username": username, "password": username + "123"}).json()["access_token"]
                return {"Authorization": f"Bearer {token}"}
            owner, admin = headers("mock"), headers("admin")
            created = client.post("/api/conversations", headers=owner)
            self.assertEqual(created.status_code, 201)
            chat_id = created.json()["id"]
            for method in ("GET", "DELETE", "PATCH"):
                response = client.request(method, f"/api/conversations/{chat_id}", headers=admin,
                                          **({"json": {"workspace": {}}} if method == "PATCH" else {}))
                self.assertEqual(response.status_code, 404)
            self.assertEqual(client.get("/api/conversations", headers=admin).json(), [])
            self.assertEqual(client.patch(f"/api/conversations/{chat_id}", json={"workspace": {}}, headers=owner).status_code, 200)
            self.assertEqual(client.get(f"/api/conversations/{chat_id}", headers=owner).json()["turns"], [])
            streamed = client.post("/api/query/stream", headers=owner,
                                   json={"query": "查询本月各地区销售额", "session_id": chat_id})
            events = [json.loads(line[6:]) for line in streamed.text.splitlines() if line.startswith("data: ")]
            result = next(event["result"] for event in events if event["event"] == "result")
            self.assertEqual(result["status"], "completed")
            with patch.object(routes, "service", self.new_service()):
                detail = client.get(f"/api/conversations/{chat_id}", headers=owner).json()
                self.assertEqual(detail["turns"][0]["result"]["rows"], result["rows"])
                followup = client.post("/api/query/stream", headers=owner,
                                       json={"query": "分析刚才的结果", "session_id": chat_id})
                self.assertNotIn('"event": "error"', followup.text)
                self.assertEqual(len(client.get(f"/api/conversations/{chat_id}", headers=owner).json()["turns"]), 2)
            self.assertEqual(client.delete(f"/api/conversations/{chat_id}", headers=owner).status_code, 200)
            self.assertEqual(client.get(f"/api/conversations/{chat_id}", headers=owner).status_code, 404)

    def test_deleted_chat_cannot_be_resurrected_by_running_summary(self) -> None:
        started, release = Event(), Event()
        class BlockingSummary:
            def chat(self, system: str, user: str) -> str:
                started.set()
                if not release.wait(3):
                    raise RuntimeError("Test release timed out")
                return "旧摘要"
        memory = self.service.context.short_term_memory
        memory.model_client = BlockingSummary()
        memory.summary_enabled = True
        memory.trigger_tokens = memory.batch_tokens = memory.min_recent_turns = 1
        self.service.context.history.ensure("demo_analyst:summary", "demo_analyst")
        items = [{"task_id": str(i), "query": "问题", "analysis": "回答"} for i in range(3)]
        self.assertTrue(memory.maybe_schedule("demo_analyst:summary", items))
        self.assertTrue(started.wait(3))
        future = memory._states["demo_analyst:summary"].future
        try:
            self.service.delete_conversation("summary", "demo_analyst")
        finally:
            release.set()
        future.result(timeout=3)
        self.assertEqual(self.service.context.archive.load_summaries(), [])
        self.assertNotIn("demo_analyst:summary", memory._states)


if __name__ == "__main__":
    unittest.main()
