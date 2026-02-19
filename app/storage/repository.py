from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass

from app.normalize.channel_message import NormalizedMessage


@dataclass(slots=True)
class SearchRow:
    id: int
    chat_id: int
    message_id: int
    channel_username: str | None
    source_link: str | None
    text: str
    timestamp: int


class MessageRepository:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def upsert_message(self, msg: NormalizedMessage, tokens: list[str]) -> int:
        now = int(time.time())
        token_text = " ".join(tokens)
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO channel_messages (
                    chat_id, message_id, channel_username, source_link, text, tokens,
                    timestamp, edited_timestamp, source, created_at, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    channel_username=excluded.channel_username,
                    source_link=COALESCE(excluded.source_link, channel_messages.source_link),
                    text=excluded.text,
                    tokens=excluded.tokens,
                    timestamp=excluded.timestamp,
                    edited_timestamp=excluded.edited_timestamp,
                    source=excluded.source,
                    updated_at=excluded.updated_at
                """,
                (
                    msg.chat_id,
                    msg.message_id,
                    msg.channel_username,
                    msg.source_link,
                    msg.text,
                    token_text,
                    msg.timestamp,
                    msg.edited_timestamp,
                    msg.source,
                    now,
                    now,
                ),
            )
            if msg.channel_username:
                self.conn.execute(
                    """
                    INSERT INTO channel_alias(chat_id, username) VALUES (?, ?)
                    ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username
                    """,
                    (msg.chat_id, msg.channel_username.lstrip("@")),
                )
            row = self.conn.execute(
                "SELECT id FROM channel_messages WHERE chat_id=? AND message_id=?",
                (msg.chat_id, msg.message_id),
            ).fetchone()
            return int(row["id"])

    def resolve_channel(self, channel: str | int | None) -> int | None:
        if channel is None:
            return None
        if isinstance(channel, int):
            return channel
        stripped = channel.strip()
        if not stripped:
            return None
        if stripped.startswith("@") or stripped.startswith("#"):
            stripped = stripped[1:]
        if stripped.lstrip("-").isdigit():
            return int(stripped)
        row = self.conn.execute(
            "SELECT chat_id FROM channel_alias WHERE username=?",
            (stripped,),
        ).fetchone()
        return int(row["chat_id"]) if row else None

    def search(
        self,
        fts_query: str,
        limit: int,
        offset: int = 0,
        channel: str | int | None = None,
    ) -> list[SearchRow]:
        chat_id = self.resolve_channel(channel)
        if channel is not None and chat_id is None:
            return []
        sql = """
            SELECT m.id, m.chat_id, m.message_id, m.channel_username, m.source_link, m.text, m.timestamp
            FROM channel_messages_fts f
            JOIN channel_messages m ON m.id = f.rowid
            WHERE channel_messages_fts MATCH ?
        """
        params: list[object] = [fts_query]
        if chat_id is not None:
            sql += " AND m.chat_id = ?"
            params.append(chat_id)
        sql += " ORDER BY m.timestamp DESC LIMIT ? OFFSET ?"
        params.extend([limit, offset])
        rows = self.conn.execute(sql, tuple(params)).fetchall()
        return [
            SearchRow(
                id=int(row["id"]),
                chat_id=int(row["chat_id"]),
                message_id=int(row["message_id"]),
                channel_username=row["channel_username"],
                source_link=row["source_link"],
                text=row["text"],
                timestamp=int(row["timestamp"]),
            )
            for row in rows
        ]

    def search_count(self, fts_query: str, channel: str | int | None = None) -> int:
        chat_id = self.resolve_channel(channel)
        if channel is not None and chat_id is None:
            return 0
        sql = """
            SELECT COUNT(1) AS c
            FROM channel_messages_fts f
            JOIN channel_messages m ON m.id = f.rowid
            WHERE channel_messages_fts MATCH ?
        """
        params: list[object] = [fts_query]
        if chat_id is not None:
            sql += " AND m.chat_id = ?"
            params.append(chat_id)
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return int(row["c"]) if row else 0

    def set_config(self, key: str, value: str, is_sensitive: bool) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO app_config(key, value, is_sensitive, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(key) DO UPDATE SET
                    value=excluded.value,
                    is_sensitive=excluded.is_sensitive,
                    updated_at=excluded.updated_at
                """,
                (key, value, int(is_sensitive), int(time.time())),
            )

    def get_config(self, key: str) -> sqlite3.Row | None:
        return self.conn.execute(
            "SELECT key, value, is_sensitive, updated_at FROM app_config WHERE key=?",
            (key,),
        ).fetchone()

    def list_config(self) -> list[sqlite3.Row]:
        return self.conn.execute(
            "SELECT key, value, is_sensitive, updated_at FROM app_config ORDER BY key ASC"
        ).fetchall()

    def insert_admin_audit(
        self,
        admin_user_id: int,
        action: str,
        key: str | None = None,
        masked_value: str | None = None,
        detail: str | None = None,
    ) -> None:
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO admin_audit_log(admin_user_id, action, key, masked_value, detail, created_at)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (admin_user_id, action, key, masked_value, detail, int(time.time())),
            )

    def get_all_messages_count(self) -> int:
        row = self.conn.execute("SELECT COUNT(1) AS c FROM channel_messages").fetchone()
        return int(row["c"])
