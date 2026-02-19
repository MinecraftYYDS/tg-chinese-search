from __future__ import annotations

import logging
from typing import cast

from telegram import Update
from telegram.ext import ContextTypes

from app.context import RuntimeContext
from app.ingest.handler import handle_channel_message


logger = logging.getLogger(__name__)


def _runtime(context: ContextTypes.DEFAULT_TYPE) -> RuntimeContext:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        raise RuntimeError("runtime context missing")
    return cast(RuntimeContext, runtime)


async def on_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.channel_post is None:
        return
    runtime = _runtime(context)
    post = update.channel_post
    logger.info(
        "received channel_post chat_id=%s message_id=%s has_text=%s has_caption=%s",
        post.chat_id,
        post.message_id,
        bool(post.text),
        bool(post.caption),
    )
    result = handle_channel_message(post, runtime.repo, runtime.tokenizer)
    if result.ok:
        logger.info(
            "indexed channel_post chat_id=%s message_id=%s text_len=%s",
            result.chat_id,
            result.message_id,
            result.text_len,
        )
        return
    logger.warning(
        "skipped channel_post reason=%s chat_id=%s message_id=%s",
        result.reason,
        result.chat_id,
        result.message_id,
    )


async def on_edited_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_channel_post is None:
        return
    runtime = _runtime(context)
    post = update.edited_channel_post
    logger.info(
        "received edited_channel_post chat_id=%s message_id=%s has_text=%s has_caption=%s",
        post.chat_id,
        post.message_id,
        bool(post.text),
        bool(post.caption),
    )
    result = handle_channel_message(post, runtime.repo, runtime.tokenizer)
    if result.ok:
        logger.info(
            "indexed edited_channel_post chat_id=%s message_id=%s text_len=%s",
            result.chat_id,
            result.message_id,
            result.text_len,
        )
        return
    logger.warning(
        "skipped edited_channel_post reason=%s chat_id=%s message_id=%s",
        result.reason,
        result.chat_id,
        result.message_id,
    )
