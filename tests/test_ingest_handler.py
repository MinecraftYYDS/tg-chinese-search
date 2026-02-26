from __future__ import annotations

from dataclasses import dataclass
import sqlite3

from app.ingest.handler import handle_channel_message, handle_edited_channel_message
from app.search.tokenizer import default_tokenizer
from app.storage.db import init_db
from app.storage.repository import MessageRepository


@dataclass
class _Chat:
    id: int
    type: str = "channel"
    username: str | None = "demo_channel"


@dataclass
class _Message:
    message_id: int
    chat: _Chat
    date: int
    text: str | None = None
    caption: str | None = None
    edit_date: int | None = None


def _repo() -> MessageRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return MessageRepository(conn)


def test_edited_message_cleared_text_should_remove_index() -> None:
    repo = _repo()
    tokenizer = default_tokenizer()

    created = _Message(
        message_id=10,
        chat=_Chat(id=-1001234567890),
        date=1700000000,
        text="这是会被检索到的内容",
    )
    indexed = handle_channel_message(created, repo, tokenizer)
    assert indexed.ok is True
    assert indexed.reason == "indexed"

    rows_before = repo.search('"内容"*', limit=10, channel=-1001234567890)
    assert len(rows_before) == 1

    edited_to_empty = _Message(
        message_id=10,
        chat=_Chat(id=-1001234567890),
        date=1700000010,
        text=None,
        caption=None,
        edit_date=1700000010,
    )
    result = handle_edited_channel_message(edited_to_empty, repo, tokenizer)
    assert result.ok is True
    assert result.reason == "deindexed"

    rows_after = repo.search('"内容"*', limit=10, channel=-1001234567890)
    assert rows_after == []
