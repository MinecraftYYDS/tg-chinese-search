from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.normalize.channel_message import NormalizedMessage, extract_text_field
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository


@dataclass(slots=True)
class ImportStats:
    total: int = 0
    skipped: int = 0
    imported: int = 0


def _to_bot_api_chat_id(raw_chat_id: int) -> int:
    # Telegram Desktop export channel id is usually positive, while Bot API uses -100 prefix.
    if raw_chat_id < 0:
        return raw_chat_id
    return int(f"-100{raw_chat_id}")


def _normalize_import_message(message: dict[str, Any], chat_id: int) -> NormalizedMessage | None:
    if message.get("type") != "message":
        return None
    text = extract_text_field(message.get("text")).strip()
    if not text:
        return None
    timestamp = int(message.get("date_unixtime", 0))
    if not timestamp:
        return None
    edited_timestamp = message.get("edited_unixtime")
    return NormalizedMessage(
        message_id=int(message["id"]),
        chat_id=chat_id,
        text=text,
        timestamp=timestamp,
        edited_timestamp=int(edited_timestamp) if edited_timestamp else None,
        source="import",
        channel_username=None,
        source_link=message.get("link") if isinstance(message.get("link"), str) else None,
    )


def import_telegram_export(
    json_path: str,
    repo: MessageRepository,
    tokenizer: Tokenizer,
    dry_run: bool = False,
) -> ImportStats:
    data = json.loads(Path(json_path).read_text(encoding="utf-8"))
    chat_id = _to_bot_api_chat_id(int(data["id"]))
    channel_name = data.get("name", "")
    stats = ImportStats(total=len(data.get("messages", [])))

    for item in data.get("messages", []):
        normalized = _normalize_import_message(item, chat_id)
        if normalized is None:
            stats.skipped += 1
            continue
        if channel_name and not normalized.channel_username:
            normalized.channel_username = None
        tokens = tokenizer.tokenize(normalized.text)
        if not tokens:
            stats.skipped += 1
            continue
        if not dry_run:
            repo.upsert_message(normalized, tokens)
        stats.imported += 1
    return stats
