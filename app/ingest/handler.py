from __future__ import annotations

from app.normalize.channel_message import normalize_channel_message
from app.search.tokenizer import Tokenizer
from app.storage.repository import MessageRepository


def handle_channel_message(raw_msg: object, repo: MessageRepository, tokenizer: Tokenizer) -> bool:
    normalized = normalize_channel_message(raw_msg, source="live")
    if normalized is None:
        return False
    tokens = tokenizer.tokenize(normalized.text)
    if not tokens:
        return False
    repo.upsert_message(normalized, tokens)
    return True

