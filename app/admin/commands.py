from __future__ import annotations

import logging

from telegram import Update
from telegram.constants import ChatType
from telegram.ext import ContextTypes

from app.context import RuntimeContext


logger = logging.getLogger(__name__)


def _ctx(context: ContextTypes.DEFAULT_TYPE) -> RuntimeContext:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        raise RuntimeError("runtime context missing")
    return runtime


def _is_private(update: Update) -> bool:
    return bool(update.effective_chat and update.effective_chat.type == ChatType.PRIVATE)


def _extract_password(update: Update) -> str:
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        return ""
    return parts[1].strip()


async def admin_login(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _is_private(update):
        return
    runtime = _ctx(context)
    user = update.effective_user
    if user is None:
        return
    password = _extract_password(update)
    if not password:
        await update.effective_message.reply_text("Usage: /admin_login <password>")
        return
    success, reason = runtime.admin_auth.login(user.id, password)
    runtime.repo.insert_admin_audit(user.id, "admin_login", detail=reason)
    if success:
        await update.effective_message.reply_text("Admin login success.")
        return
    await update.effective_message.reply_text("Admin auth failed.")


def _check_admin(update: Update, runtime: RuntimeContext) -> int | None:
    if not _is_private(update):
        return None
    user = update.effective_user
    if user is None:
        return None
    if not runtime.admin_auth.is_authenticated(user.id):
        return None
    return user.id


async def admin_logout(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _ctx(context)
    user = update.effective_user
    if not user:
        return
    runtime.admin_auth.logout(user.id)
    runtime.repo.insert_admin_audit(user.id, "admin_logout")
    await update.effective_message.reply_text("Admin logout success.")


async def admin_set(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _ctx(context)
    admin_id = _check_admin(update, runtime)
    if admin_id is None:
        await update.effective_message.reply_text("Admin authentication required.")
        return
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=2)
    if len(parts) < 3:
        await update.effective_message.reply_text("Usage: /admin_set <key> <value>")
        return
    key = parts[1].strip()
    value = parts[2]
    runtime.config_store.set(key, value)
    runtime.repo.insert_admin_audit(
        admin_id,
        action="admin_set",
        key=key,
        masked_value=runtime.config_store.mask_value(key, value),
        detail="ok",
    )
    await update.effective_message.reply_text(f"Set success: {key}")


async def admin_get(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _ctx(context)
    admin_id = _check_admin(update, runtime)
    if admin_id is None:
        await update.effective_message.reply_text("Admin authentication required.")
        return
    text = (update.effective_message.text or "").strip()
    parts = text.split(maxsplit=1)
    if len(parts) < 2:
        await update.effective_message.reply_text("Usage: /admin_get <key>")
        return
    key = parts[1]
    value = runtime.config_store.get(key)
    runtime.repo.insert_admin_audit(
        admin_id, action="admin_get", key=key, masked_value=runtime.config_store.mask_value(key, value)
    )
    await update.effective_message.reply_text(f"{key} = {runtime.config_store.mask_value(key, value)}")


async def admin_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _ctx(context)
    admin_id = _check_admin(update, runtime)
    if admin_id is None:
        await update.effective_message.reply_text("Admin authentication required.")
        return
    items = runtime.config_store.list_masked()
    lines = [f"{key}={value}" for key, value, _ in items]
    runtime.repo.insert_admin_audit(admin_id, action="admin_list")
    await update.effective_message.reply_text("\n".join(lines) if lines else "No dynamic config found.")


async def admin_apply(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = _ctx(context)
    admin_id = _check_admin(update, runtime)
    if admin_id is None:
        await update.effective_message.reply_text("Admin authentication required.")
        return
    # Current version marks apply as acknowledged. Runtime restart still needed for token/listen changes.
    runtime.repo.insert_admin_audit(admin_id, action="admin_apply", detail="acknowledged")
    await update.effective_message.reply_text("Apply done. Proxy/settings requiring restart will take effect on restart.")

