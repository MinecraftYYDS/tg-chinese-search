from app.normalize.channel_message import NormalizedMessage
from app.search.tokenizer import default_tokenizer
from app.storage.db import init_db
from app.storage.repository import MessageRepository
import sqlite3


def _repo() -> MessageRepository:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return MessageRepository(conn)


def test_insert_and_search_channel_filter() -> None:
    repo = _repo()
    tokenizer = default_tokenizer()
    msg1 = NormalizedMessage(
        message_id=1,
        chat_id=100,
        text="你好世界 频道A",
        timestamp=1000,
        edited_timestamp=None,
        source="import",
        channel_username="a_channel",
        source_link=None,
    )
    msg2 = NormalizedMessage(
        message_id=2,
        chat_id=200,
        text="你好世界 频道B",
        timestamp=1001,
        edited_timestamp=None,
        source="import",
        channel_username="b_channel",
        source_link=None,
    )
    repo.upsert_message(msg1, tokenizer.tokenize(msg1.text))
    repo.upsert_message(msg2, tokenizer.tokenize(msg2.text))

    rows = repo.search('"你好"*', limit=10, channel="@a_channel")
    assert len(rows) == 1
    assert rows[0].chat_id == 100
