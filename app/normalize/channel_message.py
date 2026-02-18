from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(slots=True)
class NormalizedMessage:
    message_id: int
    chat_id: int
    text: str
    timestamp: int
    edited_timestamp: int | None
    source: str
    channel_username: str | None
    source_link: str | None


def extract_text_field(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, list):
        return ""
    parts: list[str] = []
    for item in value:
        if isinstance(item, str):
            parts.append(item)
        elif isinstance(item, dict):
            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
    return "".join(parts)


def normalize_channel_message(raw: Any, source: str = "live") -> NormalizedMessage | None:
    chat = getattr(raw, "chat", None)
    if chat is None:
        chat = raw.get("chat") if isinstance(raw, dict) else None
    chat_type = getattr(chat, "type", None) if chat is not None else None
    if chat_type is None and isinstance(chat, dict):
        chat_type = chat.get("type")
    if chat_type != "channel":
        return None

    text = getattr(raw, "text", None)
    if text is None and isinstance(raw, dict):
        text = raw.get("text")
    text = extract_text_field(text).strip()
    if not text:
        return None

    message_id = getattr(raw, "message_id", None)
    if message_id is None and isinstance(raw, dict):
        message_id = raw.get("id")
    chat_id = getattr(chat, "id", None) if chat is not None else None
    if chat_id is None and isinstance(chat, dict):
        chat_id = chat.get("id")
    date = getattr(raw, "date", None)
    if date is None and isinstance(raw, dict):
        date = raw.get("date_unixtime")
    edited_date = getattr(raw, "edit_date", None)
    if edited_date is None and isinstance(raw, dict):
        edited_date = raw.get("edited_unixtime")

    if message_id is None or chat_id is None or date is None:
        return None

    timestamp = int(date.timestamp()) if hasattr(date, "timestamp") else int(date)
    edited_timestamp = None
    if edited_date:
        edited_timestamp = (
            int(edited_date.timestamp())
            if hasattr(edited_date, "timestamp")
            else int(edited_date)
        )
    username = getattr(chat, "username", None) if chat is not None else None
    if username is None and isinstance(chat, dict):
        username = chat.get("username")

    return NormalizedMessage(
        message_id=int(message_id),
        chat_id=int(chat_id),
        text=text,
        timestamp=timestamp,
        edited_timestamp=edited_timestamp,
        source=source,
        channel_username=username,
        source_link=None,
    )
