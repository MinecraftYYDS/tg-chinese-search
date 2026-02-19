from __future__ import annotations

import time
from typing import cast

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import extract_keywords, parse_search_input
from app.interaction.renderers import render_private_result


def _runtime(context: ContextTypes.DEFAULT_TYPE) -> RuntimeContext:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        raise RuntimeError("runtime context missing")
    return cast(RuntimeContext, runtime)


def _build_keyboard(query_id: str, offset: int, page_size: int, total_found: int) -> InlineKeyboardMarkup | None:
    buttons: list[InlineKeyboardButton] = []
    prev_offset = max(offset - page_size, 0)
    next_offset = offset + page_size
    current_page = (offset // page_size) + 1
    total_pages = max(((total_found - 1) // page_size) + 1, 1)
    if offset > 0:
        buttons.append(InlineKeyboardButton("上一页", callback_data=f"pg:{query_id}:{prev_offset}"))
    buttons.append(InlineKeyboardButton(f"{current_page}/{total_pages}", callback_data="noop"))
    if current_page < total_pages:
        buttons.append(InlineKeyboardButton("下一页", callback_data=f"pg:{query_id}:{next_offset}"))
    return InlineKeyboardMarkup([buttons])


async def handle_private_search(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not update.effective_chat or update.effective_chat.type != ChatType.PRIVATE:
        return
    msg = update.effective_message
    if msg is None or not msg.text or msg.text.startswith("/"):
        return
    runtime = _runtime(context)
    parsed = parse_search_input(msg.text, mode="private")
    if not parsed.query:
        await msg.reply_text("请输入关键词，例如：你好世界 或 @channel 你好")
        return
    page_size = runtime.private_page_size
    results = runtime.search_service.search(parsed.query, limit=page_size, offset=0, channel_filter=parsed.channel)
    if not results:
        await msg.reply_text("未找到匹配结果。")
        return
    query_id = str(int(time.time() * 1000))
    user_data = context.user_data.setdefault("search_queries", {})
    total_found = runtime.search_service.count(parsed.query, channel_filter=parsed.channel)
    user_data[query_id] = {"query": parsed.query, "channel": parsed.channel, "total_found": total_found}

    keywords = extract_keywords(parsed.query)
    chunks = [render_private_result(row, keywords) for row in results]
    text = f"\n{runtime.private_separator}\n".join(chunks)
    keyboard = _build_keyboard(query_id, offset=0, page_size=page_size, total_found=total_found)
    await msg.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True, reply_markup=keyboard)


async def handle_private_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None or not query.data or not query.data.startswith("pg:"):
        return
    await query.answer()
    runtime = _runtime(context)
    _, query_id, offset_raw = query.data.split(":", 2)
    offset = int(offset_raw)
    query_state = context.user_data.get("search_queries", {}).get(query_id)
    if not query_state:
        await query.edit_message_text("分页状态已失效，请重新搜索。")
        return
    q = query_state["query"]
    channel = query_state["channel"]
    total_found = int(query_state.get("total_found", 0))
    page_size = runtime.private_page_size
    results = runtime.search_service.search(q, limit=page_size, offset=offset, channel_filter=channel)
    if not results:
        await query.edit_message_text("没有更多结果。")
        return
    keywords = extract_keywords(q)
    text = f"\n{runtime.private_separator}\n".join(render_private_result(row, keywords) for row in results)
    keyboard = _build_keyboard(query_id, offset=offset, page_size=page_size, total_found=total_found)
    await query.edit_message_text(
        text=text,
        parse_mode=ParseMode.HTML,
        disable_web_page_preview=True,
        reply_markup=keyboard,
    )


async def handle_noop_pagination(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    if query is None:
        return
    await query.answer()
