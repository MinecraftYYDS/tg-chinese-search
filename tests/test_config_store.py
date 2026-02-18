import sqlite3

from cryptography.fernet import Fernet

from app.admin.config_store import ConfigStore
from app.storage.db import init_db
from app.storage.repository import MessageRepository


def test_sensitive_config_is_encrypted() -> None:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = MessageRepository(conn)
    store = ConfigStore(repo=repo, fernet=Fernet(Fernet.generate_key()))
    store.set("bot_token", "123:ABC")
    row = repo.get_config("bot_token")
    assert row is not None
    assert row["value"] != "123:ABC"
    assert store.get("bot_token") == "123:ABC"

