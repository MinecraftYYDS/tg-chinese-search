from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.normalize.channel_message import normalize_channel_message
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository


@dataclass(slots=True)
class HandleResult:
    ok: bool
    reason: str
    chat_id: int | None = None
    message_id: int | None = None
    text_len: int = 0


def handle_channel_message(raw_msg: object, repo: MessageRepository, tokenizer: Tokenizer) -> HandleResult:
    normalized = normalize_channel_message(raw_msg, source="live")
    if normalized is None:
        return HandleResult(ok=False, reason="normalize_failed")
    tokens = tokenizer.tokenize(normalized.text)
    if not tokens:
        return HandleResult(
            ok=False,
            reason="tokenize_empty",
            chat_id=normalized.chat_id,
            message_id=normalized.message_id,
            text_len=len(normalized.text),
        )
    repo.upsert_message(normalized, tokens)
    return HandleResult(
        ok=True,
        reason="indexed",
        chat_id=normalized.chat_id,
        message_id=normalized.message_id,
        text_len=len(normalized.text),
    )


def _extract_message_identity(raw_msg: object) -> tuple[int | None, int | None]:
    chat: Any = getattr(raw_msg, "chat", None)
    if chat is None and isinstance(raw_msg, dict):
        chat = raw_msg.get("chat")

    chat_id = getattr(chat, "id", None) if chat is not None else None
    if chat_id is None and isinstance(chat, dict):
        chat_id = chat.get("id")

    message_id = getattr(raw_msg, "message_id", None)
    if message_id is None and isinstance(raw_msg, dict):
        message_id = raw_msg.get("id")

    try:
        return (int(chat_id), int(message_id))
    except (TypeError, ValueError):
        return (None, None)


def handle_edited_channel_message(raw_msg: object, repo: MessageRepository, tokenizer: Tokenizer) -> HandleResult:
    normalized = normalize_channel_message(raw_msg, source="live")
    if normalized is None:
        chat_id, message_id = _extract_message_identity(raw_msg)
        if chat_id is None or message_id is None:
            return HandleResult(ok=False, reason="normalize_failed")
        deleted = repo.delete_message(chat_id=chat_id, message_id=message_id)
        return HandleResult(
            ok=deleted,
            reason="deindexed" if deleted else "deindex_not_found",
            chat_id=chat_id,
            message_id=message_id,
        )

    tokens = tokenizer.tokenize(normalized.text)
    if not tokens:
        deleted = repo.delete_message(chat_id=normalized.chat_id, message_id=normalized.message_id)
        return HandleResult(
            ok=deleted,
            reason="deindexed" if deleted else "deindex_not_found",
            chat_id=normalized.chat_id,
            message_id=normalized.message_id,
            text_len=len(normalized.text),
        )

    repo.upsert_message(normalized, tokens)
    return HandleResult(
        ok=True,
        reason="indexed",
        chat_id=normalized.chat_id,
        message_id=normalized.message_id,
        text_len=len(normalized.text),
    )
