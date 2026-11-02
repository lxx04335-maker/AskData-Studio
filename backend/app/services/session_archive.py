from __future__ import annotations

import json
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any


class SessionArchive:
    """持久化保存完整会话轮次和摘要状态。"""

    def __init__(self, path: Path, *, enabled: bool = True) -> None:
        self.path = path
        self.enabled = enabled
        self._lock = Lock()
        if enabled:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._initialize()

    def save_turn(
        self,
        task_id: str,
        session_id: str,
        query: str,
        workspace: dict[str, Any],
        result: dict[str, Any],
    ) -> None:
        if not self.enabled:
            return
        now = datetime.now(timezone.utc).isoformat()
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_turns (
                    task_id, session_id, query, workspace_json, result_json, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(task_id) DO UPDATE SET
                    query = excluded.query,
                    workspace_json = excluded.workspace_json,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    task_id,
                    session_id,
                    query,
                    json.dumps(workspace, ensure_ascii=False),
                    json.dumps(result, ensure_ascii=False),
                    now,
                    now,
                ),
            )

    def load_turns(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT task_id, session_id, query, workspace_json, result_json, created_at, updated_at
                FROM session_turns
                ORDER BY julianday(created_at), rowid
                """
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "session_id": row[1],
                "query": row[2],
                "workspace": json.loads(row[3]),
                "result": json.loads(row[4]),
                "created_at": row[5],
                "updated_at": row[6],
            }
            for row in rows
        ]

    def save_message(
        self,
        session_id: str,
        role: str,
        content: str,
        *,
        task_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        created_at: str | None = None,
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_messages (
                    session_id, task_id, role, content, metadata_json, created_at
                ) VALUES (?, ?, ?, ?, ?, COALESCE(?, CURRENT_TIMESTAMP))
                """,
                (
                    session_id,
                    task_id,
                    role,
                    content,
                    json.dumps(metadata or {}, ensure_ascii=False),
                    created_at,
                ),
            )

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                """
                SELECT task_id, role, content, metadata_json, message_id, created_at
                FROM session_messages
                WHERE session_id = ?
                ORDER BY message_id
                """,
                (session_id,),
            ).fetchall()
        return [
            {
                "task_id": row[0],
                "role": row[1],
                "content": row[2],
                "metadata": json.loads(row[3]),
                "message_id": row[4],
                "created_at": row[5],
            }
            for row in rows
        ]

    def save_summary(
        self, session_id: str, summary: str, summarized_ids: set[str]
    ) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """
                INSERT INTO session_summaries (
                    session_id, summary, summarized_ids_json
                ) VALUES (?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET
                    summary = excluded.summary,
                    summarized_ids_json = excluded.summarized_ids_json,
                    updated_at = CURRENT_TIMESTAMP
                """,
                (
                    session_id,
                    summary,
                    json.dumps(sorted(summarized_ids), ensure_ascii=False),
                ),
            )

    def load_summaries(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection, connection:
            rows = connection.execute(
                "SELECT session_id, summary, summarized_ids_json FROM session_summaries"
            ).fetchall()
        return [
            {
                "session_id": row[0],
                "summary": row[1],
                "summarized_ids": set(json.loads(row[2])),
            }
            for row in rows
        ]

    def save_conversation(self, item: dict[str, Any]) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            connection.execute(
                """INSERT INTO conversations
                (session_id, user_id, public_id, title, workspace_json, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(session_id) DO UPDATE SET title=excluded.title,
                    workspace_json=excluded.workspace_json, updated_at=excluded.updated_at""",
                (item["session_id"], item["user_id"], item["id"], item["title"],
                 json.dumps(item["workspace"], ensure_ascii=False), item["created_at"], item["updated_at"]),
            )

    def load_conversations(self) -> list[dict[str, Any]]:
        if not self.enabled:
            return []
        with self._lock, closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT session_id, user_id, public_id, title, workspace_json, created_at, updated_at FROM conversations"
            ).fetchall()
        return [dict(session_id=row[0], user_id=row[1], id=row[2], title=row[3],
                     workspace=json.loads(row[4]), created_at=row[5], updated_at=row[6]) for row in rows]

    def delete_conversation(self, session_id: str) -> None:
        if not self.enabled:
            return
        with self._lock, closing(self._connect()) as connection, connection:
            for table in ("session_messages", "session_turns", "session_summaries", "conversations"):
                connection.execute(f"DELETE FROM {table} WHERE session_id = ?", (session_id,))

    def _initialize(self) -> None:
        with closing(self._connect()) as connection, connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS session_turns (
                    task_id TEXT PRIMARY KEY,
                    session_id TEXT NOT NULL,
                    query TEXT NOT NULL,
                    workspace_json TEXT NOT NULL,
                    result_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS conversations (
                    session_id TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL,
                    public_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    workspace_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_conversations_user ON conversations(user_id, updated_at);
                CREATE INDEX IF NOT EXISTS idx_session_turns_session
                    ON session_turns(session_id, created_at);

                CREATE TABLE IF NOT EXISTS session_messages (
                    message_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    session_id TEXT NOT NULL,
                    task_id TEXT,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL,
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_session_messages_session
                    ON session_messages(session_id, message_id);

                CREATE TABLE IF NOT EXISTS session_summaries (
                    session_id TEXT PRIMARY KEY,
                    summary TEXT NOT NULL,
                    summarized_ids_json TEXT NOT NULL,
                    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path, timeout=10)
