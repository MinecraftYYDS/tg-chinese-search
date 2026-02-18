from __future__ import annotations

from typing import cast

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import parse_search_input
from app.interaction.renderers import render_private_result


def _runtime(context: ContextTypes.DEFAULT_TYPE) -> RuntimeContext:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        raise RuntimeError("runtime context missing")
    return cast(RuntimeContext, runtime)


async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    runtime = _runtime(context)
    parsed = parse_search_input(message.text, mode="command")
    if not parsed.query:
        await message.reply_text("Usage: /search 关键词 或 /search @channel 关键词")
        return
    results = runtime.search_service.search(parsed.query, limit=runtime.private_page_size, channel_filter=parsed.channel)
    if not results:
        await message.reply_text("未找到匹配结果。")
        return
    keywords = [item for item in parsed.query.split() if item]
    text = f"\n{runtime.private_separator}\n".join(render_private_result(row, keywords) for row in results)
    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)
