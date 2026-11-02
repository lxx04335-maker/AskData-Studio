from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
from threading import RLock
from typing import Any

from .session_archive import SessionArchive


def timestamp(value: str | None = None) -> str:
    """旧归档的 SQLite 时间也是 UTC；统一输出带时区的 ISO 时间。"""
    if value:
        parsed = datetime.fromisoformat(value)
        return parsed.replace(tzinfo=parsed.tzinfo or timezone.utc).isoformat()
    return datetime.now(timezone.utc).isoformat()


class ConversationHistory:
    """保存聊天列表和逐条消息快照；关闭归档时仍支持本次进程内的聊天。"""

    def __init__(self, archive: SessionArchive) -> None:
        self.archive = archive
        self.lock = RLock()
        self.items = {item["session_id"]: item for item in archive.load_conversations()}
        self.messages: dict[str, list[dict[str, Any]]] = {}
        self.busy: set[str] = set()
        self.deleted: set[str] = set()

    def ensure(
        self, session_id: str, user_id: str, workspace: dict[str, Any] | None = None,
        *, title: str = "新对话", created_at: str | None = None, updated_at: str | None = None,
    ) -> dict[str, Any]:
        with self.lock:
            if session_id in self.deleted:
                raise KeyError(session_id)
            if session_id not in self.items:
                item = dict(session_id=session_id, user_id=user_id,
                            id=session_id.split(":", 1)[-1], title=title,
                            workspace=deepcopy(workspace or {}),
                            created_at=timestamp(created_at), updated_at=timestamp(updated_at or created_at))
                self.archive.save_conversation(item)
                self.items[session_id] = item
            return self.items[session_id]

    def list(self, user_id: str) -> list[dict[str, Any]]:
        with self.lock:
            return [self._public(item) for item in sorted(
                self.items.values(), key=lambda item: item["updated_at"], reverse=True,
            ) if item["user_id"] == user_id]

    def get(self, session_id: str, user_id: str) -> dict[str, Any]:
        with self.lock:
            item = self.items.get(session_id)
            if not item or item["user_id"] != user_id:
                raise KeyError(session_id)
            return self._public(item)

    def update(self, session_id: str, user_id: str, workspace: dict[str, Any]) -> dict[str, Any]:
        with self.lock:
            self.get(session_id, user_id)
            item = {**self.items[session_id], "workspace": deepcopy(workspace), "updated_at": timestamp()}
            self.archive.save_conversation(item)
            self.items[session_id] = item
            return self._public(item)

    def begin(
        self, session_id: str, user_id: str, query: str, workspace: dict[str, Any],
        task_id: str, metadata: dict[str, Any] | None = None,
    ) -> None:
        with self.lock:
            if session_id in self.busy:
                raise ValueError("该对话正在处理问题，请稍后再试")
            self.ensure(session_id, user_id, workspace)
            item = {**self.items[session_id], "workspace": deepcopy(workspace), "updated_at": timestamp()}
            if item["title"] == "新对话":
                item["title"] = query[:22]
            self.archive.save_conversation(item)
            self.items[session_id] = item
            self.record(session_id, "user", query, task_id, metadata or {})
            self.busy.add(session_id)

    def record(self, session_id: str, role: str, content: str, task_id: str, metadata: dict[str, Any]) -> None:
        with self.lock:
            messages = self.load_messages(session_id)
            created_at = timestamp()
            self.archive.save_message(session_id, role, content, task_id=task_id,
                                      metadata=metadata, created_at=created_at)
            # The in-memory ID only needs to be stable for the current page.
            messages.append(dict(message_id=f"live-{len(messages) + 1}", role=role, content=content,
                                 task_id=task_id, metadata=deepcopy(metadata), created_at=created_at))
            item = {**self.items[session_id], "updated_at": created_at}
            self.archive.save_conversation(item)
            self.items[session_id] = item

    def end(self, session_id: str) -> None:
        with self.lock:
            self.busy.discard(session_id)

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        with self.lock:
            if session_id not in self.messages:
                self.messages[session_id] = self.archive.load_messages(session_id)
            return self.messages[session_id]

    def delete(self, session_id: str, user_id: str) -> None:
        with self.lock:
            self.get(session_id, user_id)
            if session_id in self.busy:
                raise ValueError("该对话正在处理问题，请等待回复完成后删除")
            self.archive.delete_conversation(session_id)
            self.deleted.add(session_id)
            self.items.pop(session_id, None)
            self.messages.pop(session_id, None)

    @staticmethod
    def _public(item: dict[str, Any]) -> dict[str, Any]:
        return deepcopy({key: item[key] for key in ("id", "title", "workspace", "created_at", "updated_at")})
