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


def _snippet_around_keyword(text: str, keyword: str | None, before: int = 12, after: int = 25) -> str:
    if not text:
        return text
    if not keyword:
        return _truncate(text, 50)
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    match = pattern.search(text)
    if not match:
        return _truncate(text, 50)
    start = max(match.start() - before, 0)
    end = min(match.end() + after, len(text))
    snippet = text[start:end]
    if start > 0:
        snippet = "..." + snippet
    if end < len(text):
        snippet = snippet + "..."
    return snippet


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


def render_private_result(row: SearchRow, keywords: list[str], include_message_ids: bool = False) -> str:
    channel_label = f"@{row.channel_username}" if row.channel_username else str(row.chat_id)
    first_keyword = keywords[0] if keywords else None
    preview = _snippet_around_keyword(row.text, first_keyword, before=12, after=25)
    content = _highlight_html(preview, keywords)
    link = build_message_link(
        row.channel_username,
        row.message_id,
        source_link=row.source_link,
        chat_id=row.chat_id,
    )
    link_text = f'<a href="{html.escape(link)}">跳转到原文</a>' if link else "不可跳转（链接信息不足）"
    id_line = (
        f"\n🆔 <b>ID：</b><code>{row.chat_id}:{row.message_id}</code>"
        if include_message_ids
        else ""
    )
    return (
        f"📌 <b>频道：</b>{html.escape(channel_label)}\n"
        f"🕒 <b>时间：</b>{_format_time(row.timestamp)}\n"
        f"📝 <b>内容：</b>\n{content}\n"
        f"🔗 {link_text}"
        f"{id_line}"
    )


def render_inline_title(row: SearchRow, keywords: list[str]) -> str:
    first_keyword = keywords[0] if keywords else None
    return _highlight_md(_snippet_around_keyword(row.text, first_keyword, before=2, after=8), keywords)


def render_inline_description(row: SearchRow) -> str:
    channel = f"@{row.channel_username}" if row.channel_username else str(row.chat_id)
    return f"{channel} · {_format_time(row.timestamp)}"


def render_inline_message(row: SearchRow) -> str:
    return row.text
