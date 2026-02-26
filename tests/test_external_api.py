from __future__ import annotations

import json
from pathlib import Path
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from cryptography.fernet import Fernet

from app.admin.config_store import ConfigStore
from app.context import RuntimeContext
from app.http_api import ExternalSearchApiServer
from app.normalize.channel_message import NormalizedMessage
from app.search.service import SearchService
from app.search.tokenizer import default_tokenizer
from app.storage.db import connect_db, init_db
from app.storage.repository import MessageRepository


def _build_runtime(tmp_path: Path, enabled: bool, token: str) -> RuntimeContext:
    db_path = tmp_path / "test_external_api.db"
    conn = connect_db(str(db_path))
    init_db(conn)
    repo = MessageRepository(conn)
    store = ConfigStore(repo=repo, fernet=Fernet(Fernet.generate_key()))
    store.set("external_api_enabled", str(enabled).lower())
    if token:
        store.set("external_api_token", token)

    tokenizer = default_tokenizer()
    repo.upsert_message(
        NormalizedMessage(
            message_id=1,
            chat_id=-100123,
            text="你好 世界 telegram 搜索",
            timestamp=1730000000,
            edited_timestamp=None,
            source="test",
            channel_username="mychannel",
            source_link="https://t.me/mychannel/1",
        ),
        tokenizer.tokenize("你好 世界 telegram 搜索"),
    )
    search_service = SearchService(repo=repo, tokenizer=tokenizer)
    return RuntimeContext(
        repo=repo,
        tokenizer=tokenizer,
        search_service=search_service,
        admin_auth=None,
        config_store=store,
        default_search_limit=10,
        private_page_size=10,
        private_separator="---",
        proxy_fail_open=True,
        polling_idle_restart_seconds=3600,
    )


def _request_json(url: str, token: str | None = None) -> tuple[int, dict[str, Any]]:
    headers = {}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    req = Request(url, method="GET", headers=headers)
    try:
        with urlopen(req, timeout=5) as resp:
            body = resp.read().decode("utf-8")
            return resp.status, json.loads(body)
    except HTTPError as exc:
        body = exc.read().decode("utf-8")
        return exc.code, json.loads(body)


def test_external_api_disabled_returns_503(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path=tmp_path, enabled=False, token="")
    server = ExternalSearchApiServer(runtime=runtime, host="127.0.0.1", port=0)
    server.start()
    try:
        status, payload = _request_json(f"http://127.0.0.1:{server.bound_port}/api/search?q=telegram")
    finally:
        server.stop()

    assert status == 503
    assert payload["code"] == "api_disabled"


def test_external_api_anonymous_when_no_token(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path=tmp_path, enabled=True, token="")
    server = ExternalSearchApiServer(runtime=runtime, host="127.0.0.1", port=0)
    server.start()
    try:
        status, payload = _request_json(
            f"http://127.0.0.1:{server.bound_port}/api/search?q=telegram&limit=5&offset=0"
        )
    finally:
        server.stop()

    assert status == 200
    assert payload["code"] == "ok"
    data = payload["data"]
    assert data["total"] == 1
    assert len(data["items"]) == 1
    item = data["items"][0]
    assert item["chat_id"] == -100123
    assert item["message_id"] == 1
    assert item["source_link"] == "https://t.me/mychannel/1"


def test_external_api_requires_token_when_configured(tmp_path: Path) -> None:
    runtime = _build_runtime(tmp_path=tmp_path, enabled=True, token="secret-token")
    server = ExternalSearchApiServer(runtime=runtime, host="127.0.0.1", port=0)
    server.start()
    try:
        status_no_token, payload_no_token = _request_json(
            f"http://127.0.0.1:{server.bound_port}/api/search?q=telegram"
        )
        status_bad_token, payload_bad_token = _request_json(
            f"http://127.0.0.1:{server.bound_port}/api/search?q=telegram",
            token="wrong-token",
        )
        status_ok, payload_ok = _request_json(
            f"http://127.0.0.1:{server.bound_port}/api/search?q=telegram",
            token="secret-token",
        )
    finally:
        server.stop()

    assert status_no_token == 401
    assert payload_no_token["code"] == "unauthorized"
    assert status_bad_token == 401
    assert payload_bad_token["code"] == "unauthorized"
    assert status_ok == 200
    assert payload_ok["data"]["total"] == 1
