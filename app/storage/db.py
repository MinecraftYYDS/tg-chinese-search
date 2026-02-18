from __future__ import annotations

import sqlite3
from pathlib import Path


def connect_db(sqlite_path: str) -> sqlite3.Connection:
    path = Path(sqlite_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON;")
    return conn


def init_db(conn: sqlite3.Connection, schema_path: str = "app/storage/schema.sql") -> None:
    schema_sql = Path(schema_path).read_text(encoding="utf-8")
    conn.executescript(schema_sql)
    _ensure_columns(conn)
    conn.commit()


def _ensure_columns(conn: sqlite3.Connection) -> None:
    table_info = conn.execute("PRAGMA table_info(channel_messages)").fetchall()
    existing = {row[1] for row in table_info}
    if "source_link" not in existing:
        conn.execute("ALTER TABLE channel_messages ADD COLUMN source_link TEXT")
