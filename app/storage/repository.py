from __future__ import annotations

import sqlite3
import time
from contextlib import nullcontext
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
    def __init__(self, conn: sqlite3.Connection, *, private: bool = False, hidden: bool = False) -> None:
        self.conn = conn
        self.private = private
        self.hidden = hidden

    def _visibility(self, alias: str = "m") -> str:
        controls = f"length(trim({alias}.text))>0 AND NOT EXISTS (SELECT 1 FROM message_controls x WHERE x.chat_id={alias}.chat_id AND x.message_id={alias}.message_id AND (x.deleted=1"
        if not self.hidden:
            controls += " OR x.hidden=1"
        controls += "))"
        since, until = getattr(self, "time_range", (0, 0))
        if since:
            controls += f" AND {alias}.timestamp>={int(since)}"
        if until:
            controls += f" AND {alias}.timestamp<={int(until)}"
        if self.private:
            return controls
        return controls + f""" AND (
            (EXISTS (SELECT 1 FROM app_config WHERE key='managed_channels_only' AND value='true')
             AND EXISTS (SELECT 1 FROM managed_channels c WHERE c.chat_id={alias}.chat_id AND c.published=1))
            OR
            (NOT EXISTS (SELECT 1 FROM app_config WHERE key='managed_channels_only' AND value='true')
             AND (NOT EXISTS (SELECT 1 FROM allowed_channels)
                  OR EXISTS (SELECT 1 FROM allowed_channels a WHERE a.chat_id={alias}.chat_id AND a.enabled=1)))
        )"""

    def upsert_message(self, msg: NormalizedMessage, tokens: list[str], *, commit: bool = True) -> int:
        now = int(time.time())
        token_text = " ".join(tokens)
        with self.conn if commit else nullcontext():
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
                WHERE COALESCE(excluded.edited_timestamp, excluded.timestamp) >=
                      COALESCE(channel_messages.edited_timestamp, channel_messages.timestamp)
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
                self.conn.execute("DELETE FROM channel_alias WHERE username=? AND chat_id!=?",
                                  (msg.channel_username.lstrip("@"), msg.chat_id))
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

    def delete_message(self, chat_id: int, message_id: int) -> bool:
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM channel_messages WHERE chat_id=? AND message_id=?",
                (chat_id, message_id),
            )
            return cursor.rowcount > 0

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
        if self.conn.execute("SELECT 1 FROM app_config WHERE key='managed_channels_only' AND value='true'").fetchone():
            row = self.conn.execute("SELECT chat_id FROM managed_channels WHERE lower(username)=lower(?)", (stripped,)).fetchone()
            return int(row["chat_id"]) if row else None
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
        sql += " AND " + self._visibility()
        sql += " ORDER BY m.timestamp DESC, m.id DESC LIMIT ? OFFSET ?"
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
        sql += " AND " + self._visibility()
        row = self.conn.execute(sql, tuple(params)).fetchone()
        return int(row["c"]) if row else 0

    def random_messages(self, limit: int, channel: str | int | None = None) -> list[SearchRow]:
        chat_id = self.resolve_channel(channel)
        if channel is not None and chat_id is None:
            return []
        sql = """
            SELECT m.id, m.chat_id, m.message_id, m.channel_username, m.source_link, m.text, m.timestamp
            FROM channel_messages m
        """
        params: list[object] = []
        sql += " WHERE " + self._visibility()
        if chat_id is not None:
            sql += " AND m.chat_id = ?"
            params.append(chat_id)
        sql += " ORDER BY RANDOM() LIMIT ?"
        params.append(limit)
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

    def random_count(self, channel: str | int | None = None) -> int:
        chat_id = self.resolve_channel(channel)
        if channel is not None and chat_id is None:
            return 0
        sql = "SELECT COUNT(1) AS c FROM channel_messages m WHERE " + self._visibility()
        params: list[object] = []
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
    def add_allowed_channel(self, chat_id: int, channel_name: str, description: str = "") -> None:
        """Add a channel to the whitelist"""
        now = int(time.time())
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO allowed_channels(chat_id, channel_name, enabled, description, created_at, updated_at)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT(chat_id) DO UPDATE SET
                    channel_name=excluded.channel_name,
                    description=excluded.description,
                    enabled=1,
                    updated_at=excluded.updated_at
                """,
                (chat_id, channel_name, description, now, now),
            )

    def remove_allowed_channel(self, chat_id: int) -> bool:
        """Remove a channel from the whitelist"""
        with self.conn:
            cursor = self.conn.execute(
                "DELETE FROM allowed_channels WHERE chat_id=?",
                (chat_id,),
            )
            return cursor.rowcount > 0

    def disable_allowed_channel(self, chat_id: int) -> bool:
        """Disable a channel in the whitelist"""
        now = int(time.time())
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE allowed_channels SET enabled=0, updated_at=? WHERE chat_id=?",
                (now, chat_id),
            )
            return cursor.rowcount > 0

    def enable_allowed_channel(self, chat_id: int) -> bool:
        """Enable a channel in the whitelist"""
        now = int(time.time())
        with self.conn:
            cursor = self.conn.execute(
                "UPDATE allowed_channels SET enabled=1, updated_at=? WHERE chat_id=?",
                (now, chat_id),
            )
            return cursor.rowcount > 0

    def is_channel_allowed(self, chat_id: int) -> bool:
        """Check if a channel is in the whitelist and enabled"""
        if self.private:
            return True
        managed = self.conn.execute("SELECT 1 FROM app_config WHERE key='managed_channels_only' AND value='true'").fetchone()
        if managed:
            return self.conn.execute("SELECT 1 FROM managed_channels WHERE chat_id=? AND published=1", (chat_id,)).fetchone() is not None
        # If no whitelist exists (empty allowed_channels table), allow all channels by default
        has_whitelist = self.conn.execute(
            "SELECT 1 FROM allowed_channels LIMIT 1"
        ).fetchone() is not None

        if not has_whitelist:
            return True

        row = self.conn.execute(
            "SELECT enabled FROM allowed_channels WHERE chat_id=?",
            (chat_id,),
        ).fetchone()
        return bool(row and row["enabled"])

    def get_allowed_channels(self) -> list[dict]:
        """Get all allowed channels"""
        rows = self.conn.execute(
            "SELECT chat_id, channel_name, enabled, description, created_at, updated_at FROM allowed_channels ORDER BY channel_name ASC"
        ).fetchall()
        return [
            {
                "chat_id": int(row["chat_id"]),
                "channel_name": row["channel_name"],
                "enabled": bool(row["enabled"]),
                "description": row["description"],
                "created_at": int(row["created_at"]),
                "updated_at": int(row["updated_at"]),
            }
            for row in rows
        ]
