from __future__ import annotations

from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken

from app.storage.repository import MessageRepository


SENSITIVE_KEYS = {
    "bot_token",
    "telegram_proxy_url",
    "sqlite_path",
    "webhook_url",
    "webhook_listen_host",
    "webhook_listen_port",
}


@dataclass(slots=True)
class ConfigStore:
    repo: MessageRepository
    fernet: Fernet

    def set(self, key: str, value: str) -> None:
        sensitive = key in SENSITIVE_KEYS
        stored = self.fernet.encrypt(value.encode("utf-8")).decode("utf-8") if sensitive else value
        self.repo.set_config(key=key, value=stored, is_sensitive=sensitive)

    def get(self, key: str) -> str | None:
        row = self.repo.get_config(key)
        if not row:
            return None
        value = row["value"]
        if int(row["is_sensitive"]) == 0:
            return value
        return self._decrypt_value(value)

    def list_masked(self) -> list[tuple[str, str, bool]]:
        rows = self.repo.list_config()
        result: list[tuple[str, str, bool]] = []
        for row in rows:
            key = row["key"]
            sensitive = bool(row["is_sensitive"])
            value = self._decrypt_value(row["value"]) if sensitive else row["value"]
            masked = self.mask_value(key, value)
            result.append((key, masked, sensitive))
        return result

    def mask_value(self, key: str, value: str | None) -> str:
        if value is None:
            return "(null)"
        if key in SENSITIVE_KEYS:
            if len(value) <= 8:
                return "*" * len(value)
            return f"{value[:4]}***{value[-2:]}"
        return value

    def _decrypt_value(self, value: str) -> str:
        try:
            return self.fernet.decrypt(value.encode("utf-8")).decode("utf-8")
        except (InvalidToken, ValueError):
            return ""

