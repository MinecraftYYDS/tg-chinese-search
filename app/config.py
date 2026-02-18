from __future__ import annotations

import base64
import os
from dataclasses import dataclass
from pathlib import Path


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_admin_ids(value: str | None) -> set[int]:
    if not value:
        return set()
    ids: set[int] = set()
    for item in value.split(","):
        item = item.strip()
        if not item:
            continue
        ids.add(int(item))
    return ids


@dataclass(slots=True)
class Settings:
    bot_token: str
    app_mode: str
    sqlite_path: str
    default_search_limit: int
    private_page_size: int
    private_separator: str
    webhook_url: str
    webhook_listen_host: str
    webhook_listen_port: int
    webhook_cert_path: str | None
    webhook_key_path: str | None
    admin_ids: set[int]
    admin_password_hash: str
    admin_session_ttl_seconds: int
    admin_max_failed_attempts: int
    admin_lockout_seconds: int
    config_encryption_key: str
    telegram_proxy_enabled: bool
    telegram_proxy_url: str | None
    proxy_fail_open: bool

    @property
    def encryption_key_bytes(self) -> bytes:
        return base64.urlsafe_b64decode(self.config_encryption_key.encode("utf-8"))


def load_dotenv(dotenv_path: str = ".env") -> None:
    path = Path(dotenv_path)
    if not path.exists():
        return
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip("\"").strip("'"))


def load_settings() -> Settings:
    load_dotenv()
    key = os.getenv("CONFIG_ENCRYPTION_KEY")
    if not key:
        raise ValueError("CONFIG_ENCRYPTION_KEY is required")

    return Settings(
        bot_token=os.getenv("BOT_TOKEN", "").strip(),
        app_mode=os.getenv("APP_MODE", "polling").strip(),
        sqlite_path=os.getenv("SQLITE_PATH", "data/tg_search.db").strip(),
        default_search_limit=int(os.getenv("DEFAULT_SEARCH_LIMIT", "50")),
        private_page_size=int(os.getenv("PRIVATE_PAGE_SIZE", "10")),
        private_separator=os.getenv("PRIVATE_SEPARATOR", "🐾🐾🐾"),
        webhook_url=os.getenv("WEBHOOK_URL", "").strip(),
        webhook_listen_host=os.getenv("WEBHOOK_LISTEN_HOST", "0.0.0.0").strip(),
        webhook_listen_port=int(os.getenv("WEBHOOK_LISTEN_PORT", "8443")),
        webhook_cert_path=os.getenv("WEBHOOK_CERT_PATH"),
        webhook_key_path=os.getenv("WEBHOOK_KEY_PATH"),
        admin_ids=_parse_admin_ids(os.getenv("ADMIN_IDS")),
        admin_password_hash=os.getenv("ADMIN_PASSWORD_HASH", "").strip(),
        admin_session_ttl_seconds=int(os.getenv("ADMIN_SESSION_TTL_SECONDS", "1800")),
        admin_max_failed_attempts=int(os.getenv("ADMIN_MAX_FAILED_ATTEMPTS", "5")),
        admin_lockout_seconds=int(os.getenv("ADMIN_LOCKOUT_SECONDS", "600")),
        config_encryption_key=key.strip(),
        telegram_proxy_enabled=_parse_bool(os.getenv("TELEGRAM_PROXY_ENABLED"), False),
        telegram_proxy_url=os.getenv("TELEGRAM_PROXY_URL"),
        proxy_fail_open=_parse_bool(os.getenv("PROXY_FAIL_OPEN"), True),
    )
