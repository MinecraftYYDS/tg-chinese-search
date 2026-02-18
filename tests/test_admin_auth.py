import sqlite3

import bcrypt

from app.admin.auth import AdminAuthService
from app.storage.db import init_db
from app.storage.repository import MessageRepository


def _auth() -> AdminAuthService:
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    init_db(conn)
    repo = MessageRepository(conn)
    pwd_hash = bcrypt.hashpw(b"secret", bcrypt.gensalt()).decode("utf-8")
    return AdminAuthService(
        repo=repo,
        admin_ids={123},
        password_hash=pwd_hash,
        session_ttl_seconds=1800,
        max_failed_attempts=5,
        lockout_seconds=600,
    )


def test_admin_auth_whitelist_and_password() -> None:
    auth = _auth()
    ok, _ = auth.login(999, "secret")
    assert not ok
    ok, _ = auth.login(123, "wrong")
    assert not ok
    ok, _ = auth.login(123, "secret")
    assert ok
    assert auth.is_authenticated(123)

