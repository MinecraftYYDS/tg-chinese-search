from __future__ import annotations

from typing import cast

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    InlineQueryResultArticle,
    InputTextMessageContent,
    Update,
)
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import extract_keywords, parse_search_input
from app.interaction.renderers import (
    render_inline_description,
    render_inline_message,
    render_inline_title,
)
from app.utils.link_builder import build_message_link


def _runtime(context: ContextTypes.DEFAULT_TYPE) -> RuntimeContext:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        raise RuntimeError("runtime context missing")
    return cast(RuntimeContext, runtime)


async def handle_inline_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    inline_query = update.inline_query
    if inline_query is None:
        return
    runtime = _runtime(context)
    raw_query = inline_query.query.strip()
    if not raw_query:
        random_rows = runtime.search_service.random(limit=runtime.default_random_limit)
        if not random_rows:
            await inline_query.answer([], cache_time=1, is_personal=True)
            return
        random_results = [
            InlineQueryResultArticle(
                id=f"random:{item.id}",
                title=render_inline_title(item, []),
                description=render_inline_description(item),
                input_message_content=InputTextMessageContent(
                    message_text=render_inline_message(item),
                    disable_web_page_preview=False,
                ),
                reply_markup=(
                    InlineKeyboardMarkup(
                        [[InlineKeyboardButton("查看原文", url=link)]]
                    )
                    if (
                        link := build_message_link(
                            item.channel_username,
                            item.message_id,
                            source_link=item.source_link,
                            chat_id=item.chat_id,
                        )
                    )
                    else None
                ),
            )
            for item in random_rows
        ]
        await inline_query.answer(random_results, cache_time=1, is_personal=True)
        return

    parsed = parse_search_input(inline_query.query, mode="inline")
    if not parsed.query:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    
    # Check if requested channel is allowed
    chat_id = runtime.repo.resolve_channel(parsed.channel)
    if parsed.channel is not None and chat_id is None:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    if chat_id is not None and not runtime.repo.is_channel_allowed(chat_id):
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="access_denied",
                    title="频道访问被禁用",
                    description=f"该频道不在搜索白名单中",
                    input_message_content=InputTextMessageContent(
                        message_text="该频道不在搜索白名单中，无法搜索。",
                        disable_web_page_preview=True,
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return
    
    rows = runtime.search_service.search(
        query=parsed.query,
        limit=min(runtime.default_search_limit, 50),
        offset=0,
        channel_filter=parsed.channel,
    )
    if not rows:
        await inline_query.answer(
            [
                InlineQueryResultArticle(
                    id="no_result",
                    title="没有搜索结果",
                    description=f"没有搜到 {parsed.query} 的内容",
                    input_message_content=InputTextMessageContent(
                        message_text=f"没有搜到 {parsed.query} 的内容",
                        disable_web_page_preview=True,
                    ),
                )
            ],
            cache_time=1,
            is_personal=True,
        )
        return
    keywords = extract_keywords(parsed.query)
    results = [
        InlineQueryResultArticle(
            id=str(item.id),
            title=render_inline_title(item, keywords),
            description=render_inline_description(item),
            input_message_content=InputTextMessageContent(
                message_text=render_inline_message(item),
                disable_web_page_preview=False,
            ),
            reply_markup=(
                InlineKeyboardMarkup(
                    [[InlineKeyboardButton("查看原文", url=link)]]
                )
                if (
                    link := build_message_link(
                        item.channel_username,
                        item.message_id,
                        source_link=item.source_link,
                        chat_id=item.chat_id,
                    )
                )
                else None
            ),
        )
        for item in rows
    ]
    await inline_query.answer(results, cache_time=1, is_personal=True)
