from __future__ import annotations


def _build_c_link(chat_id: int, message_id: int) -> str | None:
    # Telegram internal format for channel/supergroup ids: -100xxxxxxxxxx -> c/xxxxxxxxxx
    if chat_id >= 0:
        return None
    s = str(abs(chat_id))
    if not s.startswith("100"):
        return None
    internal_id = s[3:]
    if not internal_id:
        return None
    return f"https://t.me/c/{internal_id}/{message_id}"


def build_message_link(
    channel_username: str | None,
    message_id: int,
    source_link: str | None = None,
    chat_id: int | None = None,
) -> str | None:
    if source_link:
        return source_link
    if not channel_username:
        if chat_id is not None:
            return _build_c_link(chat_id, message_id)
        return None
    username = channel_username.lstrip("@")
    if not username:
        if chat_id is not None:
            return _build_c_link(chat_id, message_id)
        return None
    return f"https://t.me/{username}/{message_id}"
