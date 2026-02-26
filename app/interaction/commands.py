from __future__ import annotations

from typing import cast

from telegram import Update
from telegram.constants import ParseMode
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.interaction.parser import extract_keywords, parse_random_command_input, parse_search_input
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
    
    # Check if requested channel is allowed
    chat_id = runtime.repo.resolve_channel(parsed.channel)
    if parsed.channel is not None and chat_id is None:
        await message.reply_text("频道不存在或未找到。")
        return
    if chat_id is not None and not runtime.repo.is_channel_allowed(chat_id):
        await message.reply_text("该频道不在搜索白名单中，无法搜索。")
        return
    
    results = runtime.search_service.search(parsed.query, limit=runtime.private_page_size, channel_filter=parsed.channel)
    if not results:
        await message.reply_text("未找到匹配结果。")
        return
    keywords = extract_keywords(parsed.query)
    user = update.effective_user
    is_admin = bool(user and runtime.admin_auth.is_authenticated(user.id))
    text = f"\n{runtime.private_separator}\n".join(
        render_private_result(row, keywords, include_message_ids=is_admin) for row in results
    )
    await message.reply_text(text, parse_mode=ParseMode.HTML, disable_web_page_preview=True)


async def sj_command(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    message = update.effective_message
    if message is None or not message.text:
        return
    runtime = _runtime(context)

    try:
        parsed = parse_random_command_input(
            message.text,
            default_limit=runtime.default_random_limit,
            max_limit=runtime.max_random_limit,
        )
    except ValueError as exc:
        await message.reply_text(
            f"{exc}\n用法: /sj [#channel|@channel|-100chat_id] [条数，默认{runtime.default_random_limit}，最大{runtime.max_random_limit}]"
        )
        return

    chat_id = runtime.repo.resolve_channel(parsed.channel)
    if parsed.channel is not None and chat_id is None:
        await message.reply_text("频道不存在或未找到。")
        return
    if chat_id is not None and not runtime.repo.is_channel_allowed(chat_id):
        await message.reply_text("该频道不在搜索白名单中，无法随机。")
        return

    rows = runtime.search_service.random(limit=parsed.limit, channel_filter=parsed.channel)
    if not rows:
        await message.reply_text("当前范围内没有可随机的内容。")
        return

    user = update.effective_user
    is_admin = bool(user and runtime.admin_auth.is_authenticated(user.id))
    text = f"\n{runtime.private_separator}\n".join(
        render_private_result(row, [], include_message_ids=is_admin) for row in rows
    )
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
        "4. 随机文案：/sj [#频道|@频道|-100chat_id] [条数]\n"
        "5. 内联搜索：@botname 关键词 或 @botname #频道 关键词\n"
        "6. 内联随机：@botname （空查询）\n"
        "7. 管理命令：/admin_login /admin_set /admin_get /admin_list /admin_logout /admin_apply\n"
        "8. 频道管理：/admin_channel_add /admin_channel_remove /admin_channel_disable /admin_channel_enable /admin_channel_list\n"
        "9. 手动清理：/admin_delete_msg <chat_id> <message_id>"
    )
