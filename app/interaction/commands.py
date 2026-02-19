from __future__ import annotations

from typing import cast

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import extract_keywords, parse_search_input
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
    keywords = extract_keywords(parsed.query)
    text = f"\n{runtime.private_separator}\n".join(render_private_result(row, keywords) for row in results)
    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "欢迎使用频道中文搜索。\n"
        "私聊直接输入关键词即可搜索。\n"
        "示例：你好世界 或 @channel 你好\n"
        "输入 /help 查看完整说明。"
    )


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None:
        return
    await message.reply_text(
        "可用功能：\n"
        "1. 私聊直接搜索：你好世界\n"
        "2. 指定频道：@channel 关键词\n"
        "3. 指令搜索：/search 关键词\n"
        "4. 内联搜索：@botname 关键词 或 @botname #频道 关键词\n"
        "5. 管理命令：/admin_login /admin_set /admin_get /admin_list /admin_logout /admin_apply"
    )
