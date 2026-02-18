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
    ok = handle_channel_message(update.channel_post, runtime.repo, runtime.tokenizer)
    if not ok:
        logger.debug("skip channel_post")


async def on_edited_channel_post(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if update.edited_channel_post is None:
        return
    runtime = _runtime(context)
    ok = handle_channel_message(update.edited_channel_post, runtime.repo, runtime.tokenizer)
    if not ok:
        logger.debug("skip edited_channel_post")

