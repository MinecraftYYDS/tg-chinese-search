from __future__ import annotations


def build_message_link(channel_username: str | None, message_id: int) -> str | None:
    if not channel_username:
        return None
    username = channel_username.lstrip("@")
    if not username:
        return None
    return f"https://t.me/{username}/{message_id}"

