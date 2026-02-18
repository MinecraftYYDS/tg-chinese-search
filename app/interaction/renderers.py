from __future__ import annotations

import datetime as dt
import html
import re

from app.storage.repository import SearchRow
from app.utils.link_builder import build_message_link


def _format_time(timestamp: int) -> str:
    return dt.datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")


def _truncate(text: str, limit: int) -> str:
    if len(text) <= limit:
        return text
    return f"{text[:limit]}..."


def _highlight_html(text: str, keywords: list[str]) -> str:
    escaped = html.escape(text)
    for keyword in sorted(set(keywords), key=len, reverse=True):
        if not keyword:
            continue
        pattern = re.compile(re.escape(html.escape(keyword)), re.IGNORECASE)
        escaped = pattern.sub(lambda m: f"<b>{m.group(0)}</b>", escaped)
    return escaped


def _highlight_md(text: str, keywords: list[str]) -> str:
    value = text
    for keyword in sorted(set(keywords), key=len, reverse=True):
        if not keyword:
            continue
        pattern = re.compile(re.escape(keyword), re.IGNORECASE)
        value = pattern.sub(lambda m: f"**{m.group(0)}**", value)
    return value


def render_private_result(row: SearchRow, keywords: list[str]) -> str:
    channel_label = f"@{row.channel_username}" if row.channel_username else str(row.chat_id)
    preview = _truncate(row.text, 50)
    content = _highlight_html(preview, keywords)
    link = build_message_link(row.channel_username, row.message_id)
    link_text = f'<a href="{html.escape(link)}">跳转到原文</a>' if link else "不可跳转（无公开用户名）"
    return (
        f"📌 <b>频道：</b>{html.escape(channel_label)}\n"
        f"🕒 <b>时间：</b>{_format_time(row.timestamp)}\n"
        f"📝 <b>内容：</b>\n{content}\n"
        f"🔗 {link_text}"
    )


def render_inline_title(row: SearchRow, keywords: list[str]) -> str:
    return _highlight_md(_truncate(row.text, 10), keywords)


def render_inline_description(row: SearchRow) -> str:
    channel = f"@{row.channel_username}" if row.channel_username else str(row.chat_id)
    return f"{channel} · {_format_time(row.timestamp)}"


def render_inline_message(row: SearchRow) -> str:
    link = build_message_link(row.channel_username, row.message_id)
    if link:
        return f"{row.text}\n\n[查看原文]({link})"
    return f"{row.text}\n\n原文链接不可用"

