from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from app.collector.service import Collector
from app.normalize.channel_message import NormalizedMessage
from app.storage.repository import MessageRepository
from app.web.server import create_app
from app.web.state import WebState


@pytest.fixture
def web(tmp_path, monkeypatch):
    monkeypatch.delenv("SQLITE_PATH", raising=False)
    monkeypatch.delenv("CONFIG_ENCRYPTION_KEY", raising=False)
    monkeypatch.delenv("TG_PUBLIC_ORIGIN", raising=False)
    with TestClient(create_app(tmp_path, background=False)) as client:
        yield client, client.app.state.service


def setup(client, state):
    response = client.post("/api/admin/setup", json={"username": "admin", "password": "test-password-12345", "bootstrap_token": state.bootstrap_token})
    assert response.status_code == 200, response.text
    return {"X-CSRF-Token": response.json()["csrf"]}


def seed(state, chat_id=-100123, message_id=1, text="你好世界 测试文案", edited=None):
    state.repo.upsert_message(NormalizedMessage(message_id, chat_id, text, 1000, edited, "test", "channel" + str(abs(chat_id)), None), state.tokenizer.tokenize(text))


def channel(state, chat_id=-100123, published=0, collecting=1):
    with state.conn:
        state.conn.execute("INSERT INTO managed_channels(chat_id,title,published,collecting,created_at) VALUES (?,'测试频道',?,?,1)", (chat_id, published, collecting))


def test_setup_requires_credential_and_is_single_use(web):
    client, state = web
    assert client.get("/api/admin/session").json()["initialized"] is False
    bad = client.post("/api/admin/setup", json={"username": "admin", "password": "test-password-12345", "bootstrap_token": "wrong"})
    assert bad.status_code == 403
    headers = setup(client, state)
    assert not state.bootstrap_path.exists()
    assert state.bootstrap_token == ""
    assert client.get("/api/admin/session").json()["authenticated"]
    assert client.post("/api/admin/setup", json={"username": "admin", "password": "test-password-12345", "bootstrap_token": "x"}).status_code == 409
    assert "HttpOnly" in client.cookies.get("tg_session", "") or client.cookies.get("tg_session")
    assert client.post("/api/admin/logout", headers=headers).status_code == 200
    assert client.get("/api/admin/overview").status_code == 401


def test_auth_csrf_and_cross_origin(web):
    client, state = web
    assert client.get("/api/admin/settings").status_code == 401
    headers = setup(client, state)
    assert client.patch("/api/admin/settings", json={"external_api_enabled": True}).status_code == 403
    assert client.patch("/api/admin/settings", json={"external_api_enabled": True}, headers={**headers, "Origin": "https://evil.example"}).status_code == 403
    assert client.patch("/api/admin/settings", json={"external_api_enabled": True}, headers=headers).status_code == 200


def test_failed_logins_locked_and_sensitive_input_not_echoed(web):
    client, state = web
    setup(client, state)
    for _ in range(5):
        assert client.post("/api/admin/login", json={"username": "admin", "password": "wrong"}).status_code == 401
    assert client.post("/api/admin/login", json={"username": "admin", "password": "test-password-12345"}).status_code == 429
    response = client.post("/api/admin/login", json={"username": "a", "password": "SECRET" * 100})
    assert response.status_code == 422
    assert "SECRET" not in response.text


def test_credentials_encrypted_and_never_read_back(web):
    client, state = web
    headers = setup(client, state)
    secret = "a" * 32
    response = client.patch("/api/admin/settings", json={"telegram_api_id": 12345, "telegram_api_hash": secret}, headers=headers)
    assert response.status_code == 200
    assert secret not in response.text
    assert secret not in client.get("/api/admin/overview").text
    stored = state.conn.execute("SELECT value FROM app_config WHERE key='telegram_api_hash'").fetchone()[0]
    assert stored != secret
    assert state.store.get("telegram_api_hash") == secret
    assert secret not in client.get("/api/admin/audit").text


def test_api_disabled_auth_and_contract(web):
    client, state = web
    headers = setup(client, state)
    assert client.get("/api/random").json()["code"] == "api_disabled"
    token = client.post("/api/admin/api-token", headers=headers).json()["token"]
    client.patch("/api/admin/settings", json={"external_api_enabled": True}, headers=headers)
    assert client.get("/api/random").status_code == 401
    api_headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/search", headers=api_headers).json()["code"] == "invalid_query"
    assert client.get("/api/random?limit=0", headers=api_headers).json()["code"] == "invalid_params"
    channel(state, published=1)
    seed(state)
    response = client.get("/api/search?q=你好&limit=200", headers=api_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["code"] == "ok" and body["data"]["total"] == 1
    assert {"id", "chat_id", "message_id", "channel_username", "source_link", "text", "timestamp"} <= body["data"]["items"][0].keys()
    assert body["data"]["items"][0]["source_link"].startswith("https://t.me/")
    client.post("/api/admin/api-token", headers=headers)
    assert client.get("/api/random", headers=api_headers).status_code == 401


def test_global_search_random_count_deny_unpublished_and_unconfigured(web):
    client, state = web
    headers = setup(client, state)
    for chat_id, published in [(-100123, 1), (-100456, 0)]:
        channel(state, chat_id, published)
        seed(state, chat_id)
    seed(state, -100789)
    state.store.set("external_api_enabled", "true")
    auth = {"Authorization": "Bearer " + state.store.get("external_api_token")}
    for path in ["/api/search?q=你好", "/api/random?limit=10"]:
        data = client.get(path, headers=auth).json()["data"]
        assert data["total"] == 1
        assert {item["chat_id"] for item in data["items"]} == {-100123}
    assert client.get("/api/random?channel=-100456", headers=auth).json()["data"]["total"] == 0
    assert client.get("/api/admin/search?q=你好").json()["total"] == 3
    client.patch("/api/admin/channels/-100123", json={"published": False}, headers=headers)
    assert client.get("/api/random", headers=auth).json()["data"]["total"] == 0


def test_suppression_survives_reingestion_and_deleted_never_visible(web):
    client, state = web
    headers = setup(client, state)
    channel(state, published=1)
    seed(state)
    assert client.patch("/api/admin/messages/-100123/1", json={"hidden": True}, headers=headers).status_code == 200
    seed(state, text="你好世界 编辑后", edited=2000)
    assert state.repo.random_count() == 0
    assert client.get("/api/admin/search?show_hidden=true").json()["items"][0]["hidden"]
    client.patch("/api/admin/messages/-100123/1", json={"hidden": False}, headers=headers)
    assert state.repo.random_count() == 1
    with state.conn:
        state.conn.execute("UPDATE message_controls SET deleted=1")
    assert client.get("/api/admin/search?show_hidden=true").json()["total"] == 0


def test_older_history_cannot_overwrite_edit_or_empty_edit(web):
    _, state = web
    channel(state, published=1)
    seed(state, text="新内容", edited=2000)
    seed(state, text="旧内容")
    assert state.repo.random_messages(1)[0].text == "新内容"
    seed(state, text="", edited=3000)
    seed(state, text="旧内容")
    assert state.repo.random_count() == 0


def test_time_range_and_channel_filter_consistent(web):
    client, state = web
    setup(client, state)
    seed(state)
    assert client.get("/api/admin/search?q=你好&since=1001").json()["total"] == 0
    assert client.get("/api/admin/search?q=你好&since=999&until=1001").json()["total"] == 1
    assert client.get("/api/admin/search?since=1001").json()["total"] == 0
    assert client.get("/api/admin/search?channel=unknown").json()["total"] == 0


def test_password_change_revokes_sessions(web):
    client, state = web
    headers = setup(client, state)
    response = client.post("/api/admin/password", headers=headers, json={"username": "newadmin", "old_password": "test-password-12345", "password": "a-new-password-123"})
    assert response.status_code == 200
    assert client.get("/api/admin/overview").status_code == 401
    assert client.post("/api/admin/login", json={"username": "newadmin", "password": "a-new-password-123"}).status_code == 200


def test_pause_and_remove_close_public_access(web):
    client, state = web
    headers = setup(client, state)
    channel(state, published=1)
    seed(state)
    client.patch("/api/admin/channels/-100123", json={"collecting": False}, headers=headers)
    assert state.repo.random_count() == 1  # Pause is not unpublish.
    client.delete("/api/admin/channels/-100123", headers=headers)
    assert state.repo.random_count() == 0
    assert client.get("/api/admin/search").json()["total"] == 1


def test_legacy_whitelist_filters_global_queries(web):
    _, state = web
    state.store.set("managed_channels_only", "false")
    seed(state, -100123)
    seed(state, -100456)
    state.repo.add_allowed_channel(-100123, "allowed")
    assert state.repo.random_count() == 1
    assert len(state.repo.search('"你好"*', 20)) == 1
    assert state.repo.search_count('"你好"*') == 1


def test_persistent_key_and_bootstrap(tmp_path):
    state = WebState(tmp_path)
    token = state.bootstrap_token
    state.store.set("telegram_api_hash", "a" * 32)
    state.conn.close()
    again = WebState(tmp_path)
    assert again.bootstrap_token == token
    assert again.store.get("telegram_api_hash") == "a" * 32
    again.conn.close()


def test_wrong_key_refuses_start_without_replacing_secrets(tmp_path):
    from cryptography.fernet import Fernet
    state = WebState(tmp_path)
    state.store.set("telegram_api_hash", "a" * 32)
    old_value = state.conn.execute("SELECT value FROM app_config WHERE key='telegram_api_hash'").fetchone()[0]
    state.conn.close()
    (tmp_path / "master.key").write_text(Fernet.generate_key().decode())
    with pytest.raises(RuntimeError, match="original encryption key"):
        WebState(tmp_path)
    import sqlite3
    with sqlite3.connect(tmp_path / "tg_search.db") as conn:
        assert conn.execute("SELECT value FROM app_config WHERE key='telegram_api_hash'").fetchone()[0] == old_value


def test_backup_is_restorable_snapshot_with_key(tmp_path):
    import subprocess
    import sys
    import tarfile
    import sqlite3
    from pathlib import Path
    state = WebState(tmp_path / "data")
    seed(state)
    output = tmp_path / "backup.tar.gz"
    result = subprocess.run([sys.executable, str(Path(__file__).resolve().parents[1] / "scripts" / "backup.py"),
                             "--data", str(state.data_dir), "--output", str(output)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    with tarfile.open(output) as archive:
        assert sorted(archive.getnames()) == ["data/master.key", "data/tg_search.db"]
        extracted = tmp_path / "snapshot.db"
        extracted.write_bytes(archive.extractfile("data/tg_search.db").read())
        assert archive.extractfile("data/master.key").read() == (state.data_dir / "master.key").read_bytes()
    with sqlite3.connect(extracted) as conn:
        assert conn.execute("SELECT count(*) FROM channel_messages").fetchone()[0] == 1
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    state.conn.close()


def test_migration_preserves_messages_but_does_not_publish(tmp_path):
    from app.storage.db import connect_db, init_db
    conn = connect_db(str(tmp_path / "tg_search.db"))
    init_db(conn)
    repo = MessageRepository(conn)
    repo.upsert_message(NormalizedMessage(1, -100123, "旧数据", 1000, None, "import", "old", None), ["旧数据"])
    conn.close()
    state = WebState(tmp_path)
    assert state.repo.get_all_messages_count() == 1
    assert state.channels()[0]["published"] == 0
    assert state.channels()[0]["collecting"] == 0
    assert state.repo.random_count() == 0
    state.conn.close()


def test_historical_batch_and_checkpoint_rollback_on_error(web, monkeypatch):
    _, state = web
    channel(state)
    collector = state.collector
    msg = SimpleNamespace(id=5, message="你好", date=datetime.fromtimestamp(1000, timezone.utc), edit_date=None)
    collector.client = SimpleNamespace(get_messages=AsyncMock(return_value=[msg]))
    row = dict(collector._active(-100123))
    original = state.repo.upsert_message

    def fail(*args, **kwargs):
        original(*args, **kwargs)
        raise RuntimeError("simulated transaction failure")

    monkeypatch.setattr(state.repo, "upsert_message", fail)
    with pytest.raises(RuntimeError):
        asyncio.run(collector._history(row))
    assert state.repo.get_all_messages_count() == 0
    assert collector._active(-100123)["history_before"] is None
    monkeypatch.setattr(state.repo, "upsert_message", original)
    asyncio.run(collector._history(row))
    assert state.repo.get_all_messages_count() == 1
    assert collector._active(-100123)["history_before"] == 5
    assert collector._active(-100123)["history_done"] == 1
    collector.client = None


def test_live_event_does_not_advance_incremental_checkpoint(web):
    _, state = web
    channel(state)
    msg = SimpleNamespace(id=99, message="你好", date=datetime.fromtimestamp(1000, timezone.utc), edit_date=None)
    asyncio.run(state.collector._new_or_edit(SimpleNamespace(chat_id=-100123, message=msg)))
    assert state.collector._active(-100123)["latest_id"] == 0
    assert state.repo.get_all_messages_count() == 1
    asyncio.run(state.collector._new_or_edit(SimpleNamespace(chat_id=-100999, message=msg)))
    assert state.repo.get_all_messages_count() == 1


def test_pause_during_history_request_does_not_commit(web):
    _, state = web
    channel(state)
    msg = SimpleNamespace(id=5, message="你好", date=datetime.fromtimestamp(1000, timezone.utc), edit_date=None)

    async def fetch(*args, **kwargs):
        with state.conn:
            state.conn.execute("UPDATE managed_channels SET collecting=0")
        return [msg]

    state.collector.client = SimpleNamespace(get_messages=fetch)
    row = dict(state.collector._active(-100123))
    asyncio.run(state.collector._history(row))
    assert state.repo.get_all_messages_count() == 0
    state.collector.client = None


def test_telegram_password_required_and_session_saved(web):
    from telethon.errors import SessionPasswordNeededError
    _, state = web
    collector = state.collector
    client = SimpleNamespace(
        send_code_request=AsyncMock(return_value=SimpleNamespace(phone_code_hash="test-hash")),
        sign_in=AsyncMock(side_effect=[SessionPasswordNeededError(None), None]),
        get_me=AsyncMock(return_value=SimpleNamespace(id=123, first_name="Test", last_name=None, username="tester", bot=False)),
        session=SimpleNamespace(save=lambda: "test-session-secret"),
        is_connected=lambda: True,
    )
    collector.client = client
    asyncio.run(collector.send_code("+8613812345678"))
    assert collector.login["phase"] == "code"
    asyncio.run(collector.submit_code("12345"))
    assert collector.login["phase"] == "password"
    asyncio.run(collector.submit_password("a-secret-password"))
    assert collector.login["phase"] == "authorized"
    assert collector.phone is None and collector.phone_hash is None
    assert state.store.get("telegram_session") == "test-session-secret"
    raw = state.conn.execute("SELECT value FROM app_config WHERE key='telegram_session'").fetchone()[0]
    assert "test-session-secret" not in raw
    assert "a-secret-password" not in str(state.settings())
    collector.client = None
    collector.account = None


def test_qr_expiry(web):
    _, state = web
    collector = state.collector
    qr = SimpleNamespace(url="tg://login?token=test", expires=datetime.now(timezone.utc), wait=AsyncMock(side_effect=asyncio.TimeoutError))
    collector.client = SimpleNamespace(qr_login=AsyncMock(return_value=qr), is_connected=lambda: True)

    async def run():
        status = await collector.start_qr()
        assert status["phase"] == "qr" and "<svg" in status["qr_svg"]
        await collector.qr_task
        assert collector.login["phase"] == "idle"
        assert "过期" in collector.login["error"]
    asyncio.run(run())
    collector.client = None
    collector.qr_task = None


def test_increment_checkpoint_uses_fetched_messages_not_live_max(web):
    _, state = web
    channel(state)
    seed(state, message_id=99)
    messages = [SimpleNamespace(id=i, message="你好", date=datetime.fromtimestamp(1000, timezone.utc), edit_date=None) for i in (1, 2, 3)]

    async def iter_messages(*args, **kwargs):
        assert kwargs["min_id"] == 0
        for msg in messages:
            yield msg
    state.collector.client = SimpleNamespace(iter_messages=iter_messages)
    asyncio.run(state.collector._increment(dict(state.collector._active(-100123))))
    assert state.collector._active(-100123)["latest_id"] == 3
    assert state.repo.get_all_messages_count() == 4
    state.collector.client = None
