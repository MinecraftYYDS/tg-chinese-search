from __future__ import annotations

import hashlib
import hmac
import logging
import os
import secrets
import time
from contextlib import contextmanager
from pathlib import Path

import bcrypt
from cryptography.fernet import Fernet, InvalidToken

from app.admin.config_store import ConfigStore
from app.config import load_dotenv
from app.search.service import SearchService
from app.search.tokenizer import default_tokenizer
from app.storage.db import connect_db, init_db
from app.storage.repository import MessageRepository

logger = logging.getLogger(__name__)


def secret_file(path: Path, factory) -> str:
    try:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError:
        return path.read_text(encoding="utf-8").strip()
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        value = factory()
        f.write(value + "\n")
    return value


class WebState:
    """All mutations run on the ASGI event loop; reads use separate connections.

    Never await network I/O inside a DB transaction. Deploy with ONE ASGI worker:
    the same process owns the Telegram session and collector tasks.
    """

    def __init__(self, data_dir: Path):
        load_dotenv()
        self.data_dir = data_dir.resolve()
        self.data_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.db_path = os.getenv("SQLITE_PATH", str(self.data_dir / "tg_search.db"))
        self.conn = connect_db(self.db_path)
        init_db(self.conn)
        key_path = self.data_dir / "master.key"
        key = os.getenv("CONFIG_ENCRYPTION_KEY")
        if not key:
            encrypted = self.conn.execute("SELECT 1 FROM app_config WHERE is_sensitive=1 LIMIT 1").fetchone()
            if encrypted and not key_path.exists():
                self.conn.close()
                raise RuntimeError("Existing encrypted configuration: restore CONFIG_ENCRYPTION_KEY or data/master.key first")
            key = secret_file(key_path, lambda: Fernet.generate_key().decode())
        self.repo = MessageRepository(self.conn)
        self.store = ConfigStore(self.repo, Fernet(key.encode()))
        try:
            for row in self.conn.execute("SELECT value FROM app_config WHERE is_sensitive=1"):
                self.store.fernet.decrypt(row[0].encode())
        except (InvalidToken, ValueError):
            self.conn.close()
            raise RuntimeError("Cannot decrypt existing configuration: restore the original encryption key; nothing has been overwritten") from None
        self.tokenizer = default_tokenizer()
        self.bootstrap_path = self.data_dir / "bootstrap-token.txt"
        self.bootstrap_token = ""
        if not self.initialized:
            self.bootstrap_token = secret_file(self.bootstrap_path, lambda: secrets.token_urlsafe(32))
            logger.warning("WebUI setup required. One-time initialization credential: %s", self.bootstrap_path)
        else:
            self.bootstrap_path.unlink(missing_ok=True)
        self._migrate()
        self.collector = None
        self.bot = None

    @property
    def initialized(self) -> bool:
        return self.conn.execute("SELECT 1 FROM web_admin WHERE id=1").fetchone() is not None

    def _migrate(self) -> None:
        with self.conn:
            done = self.conn.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone()
            if not done:
                self.conn.execute("""
                    INSERT OR IGNORE INTO managed_channels(chat_id,title,username,collecting,published,status,created_at)
                    SELECT chat_id, COALESCE(MAX(channel_username), CAST(chat_id AS TEXT)),
                           MAX(channel_username), 0, 0, 'imported', ?
                    FROM channel_messages GROUP BY chat_id
                """, (int(time.time()),))
                self.conn.execute("INSERT INTO schema_migrations VALUES (2, ?)", (int(time.time()),))
        self.store.set("managed_channels_only", "true")
        for key, value in {
            "external_api_enabled": os.getenv("EXTERNAL_API_ENABLED", "false"),
            "default_search_limit": "50", "default_random_limit": "1", "max_random_limit": "10",
            "telegram_proxy_enabled": "false", "telegram_proxy_url": "",
            "bot_token": os.getenv("BOT_TOKEN", ""),
        }.items():
            if self.store.get(key) is None:
                self.store.set(key, value)
        if not self.store.get("external_api_token"):
            self.store.set("external_api_token", os.getenv("EXTERNAL_API_TOKEN") or secrets.token_urlsafe(32))

    @contextmanager
    def reader(self, *, private=False, hidden=False):
        conn = connect_db(self.db_path)
        try:
            repo = MessageRepository(conn, private=private, hidden=hidden)
            yield repo, SearchService(repo, self.tokenizer)
        finally:
            conn.close()

    def audit(self, action: str, detail: str = "") -> None:
        self.repo.insert_admin_audit(0, action, detail=detail[:500])

    @staticmethod
    def password_hash(password: str) -> str:
        return bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    @staticmethod
    def token_hash(token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def create_session(self) -> tuple[str, str]:
        token, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
        with self.conn:
            self.conn.execute("DELETE FROM web_sessions WHERE expires_at<?", (int(time.time()),))
            self.conn.execute("INSERT INTO web_sessions VALUES (?,?,?)",
                              (self.token_hash(token), csrf, int(time.time()) + 12 * 3600))
        return token, csrf

    def session(self, token: str):
        return self.conn.execute("SELECT csrf FROM web_sessions WHERE token_hash=? AND expires_at>?",
                                 (self.token_hash(token), int(time.time()))).fetchone()

    def check_limit(self, scope: str) -> bool:
        row = self.conn.execute("SELECT locked_until FROM web_login_limits WHERE scope=?", (scope,)).fetchone()
        return bool(row and row[0] > time.time())

    def failed_login(self, scope: str) -> None:
        now = int(time.time())
        with self.conn:
            self.conn.execute("""INSERT INTO web_login_limits VALUES (?,1,0)
                ON CONFLICT(scope) DO UPDATE SET failures=CASE WHEN locked_until>0 AND locked_until<=? THEN 1 ELSE failures+1 END,
                locked_until=CASE WHEN locked_until>0 AND locked_until<=? THEN 0 ELSE locked_until END""", (scope, now, now))
            self.conn.execute("UPDATE web_login_limits SET locked_until=? WHERE scope=? AND failures>=5", (now + 600, scope))

    def verify_bootstrap(self, value: str) -> bool:
        return bool(self.bootstrap_token and hmac.compare_digest(value, self.bootstrap_token))

    def channels(self) -> list[dict]:
        rows = self.conn.execute("""SELECT c.*,
            (SELECT COUNT(*) FROM channel_messages m WHERE m.chat_id=c.chat_id) AS messages
            FROM managed_channels c ORDER BY c.created_at DESC""").fetchall()
        return [dict(row) for row in rows]

    def settings(self) -> dict:
        get = self.store.get
        return {
            "telegram_api_id": get("telegram_api_id") or "",
            "api_hash_configured": bool(get("telegram_api_hash")),
            "proxy_enabled": get("telegram_proxy_enabled") == "true",
            "proxy_configured": bool(get("telegram_proxy_url")),
            "external_api_enabled": get("external_api_enabled") == "true",
            "api_token_configured": bool(get("external_api_token")),
            "bot_configured": bool(get("bot_token")),
            "bot_enabled": get("bot_enabled") == "true",
            "default_search_limit": int(get("default_search_limit") or 50),
            "max_random_limit": int(get("max_random_limit") or 10),
        }
