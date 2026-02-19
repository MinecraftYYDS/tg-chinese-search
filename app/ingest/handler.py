from __future__ import annotations

from dataclasses import dataclass

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
