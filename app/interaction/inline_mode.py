from __future__ import annotations

from typing import cast

from telegram import InlineQueryResultArticle, InputTextMessageContent, Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import parse_search_input
from app.interaction.renderers import (
    render_inline_description,
    render_inline_message,
    render_inline_title,
)


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
    parsed = parse_search_input(inline_query.query, mode="inline")
    if not parsed.query:
        await inline_query.answer([], cache_time=1, is_personal=True)
        return
    rows = runtime.search_service.search(
        query=parsed.query,
        limit=min(runtime.default_search_limit, 50),
        offset=0,
        channel_filter=parsed.channel,
    )
    keywords = [item for item in parsed.query.split() if item]
    results = [
        InlineQueryResultArticle(
            id=str(item.id),
            title=render_inline_title(item, keywords),
            description=render_inline_description(item),
            input_message_content=InputTextMessageContent(
                message_text=render_inline_message(item),
                parse_mode=ParseMode.MARKDOWN,
                disable_web_page_preview=False,
            ),
        )
        for item in rows
    ]
    await inline_query.answer(results, cache_time=1, is_personal=True)

