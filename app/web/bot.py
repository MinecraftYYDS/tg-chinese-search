from __future__ import annotations

import asyncio
import contextlib
import logging

from telegram.ext import ApplicationBuilder, CommandHandler

from app.admin.auth import AdminAuthService
from app.context import RuntimeContext
from app.main import _register_handlers
from app.network.proxy import apply_proxy
from app.search.service import SearchService

logger = logging.getLogger(__name__)


class BotManager:
    """Optional Telegram interaction only; never a second ingestion source."""

    def __init__(self, state):
        self.state = state
        self.app = None
        self.task = None
        self.error = ""
        self.username = ""

    def status(self):
        return {"running": bool(self.app and self.app.running), "username": self.username, "error": self.error}

    async def start(self):
        self.task = asyncio.create_task(self._run(), name="telegram-search-bot")

    async def stop(self):
        if self.task:
            self.task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self.task

    async def _cleanup(self):
        if not self.app:
            return
        try:
            if self.app.updater and self.app.updater.running:
                await self.app.updater.stop()
            if self.app.running:
                await self.app.stop()
            await self.app.shutdown()
        except Exception as exc:
            logger.warning("TG interaction shutdown: %s", type(exc).__name__)
        self.app = None
        self.username = ""

    async def _run(self):
        signature = None
        try:
            while True:
                store = self.state.store
                desired = (store.get("bot_token"), store.get("bot_enabled"), store.get("telegram_proxy_enabled"), store.get("telegram_proxy_url"))
                if signature != desired:
                    await self._cleanup()
                    signature = desired
                    self.error = ""
                if desired[0] and desired[1] == "true" and not self.app:
                    try:
                        builder = ApplicationBuilder().token(desired[0])
                        apply_proxy(builder, desired[2] == "true", desired[3])
                        app = builder.build()
                        self.app = app
                        runtime = RuntimeContext(
                            repo=self.state.repo, tokenizer=self.state.tokenizer,
                            search_service=SearchService(self.state.repo, self.state.tokenizer),
                            admin_auth=AdminAuthService(self.state.repo, set(), "", 1800, 5, 600),
                            config_store=store, default_search_limit=int(store.get("default_search_limit") or 50),
                            default_random_limit=1, max_random_limit=int(store.get("max_random_limit") or 10),
                            private_page_size=10, private_separator=store.get("private_separator") or "——",
                            proxy_fail_open=False, polling_idle_restart_seconds=0,
                        )
                        app.bot_data["runtime"] = runtime
                        _register_handlers(app)
                        # Existing register function includes Bot API ingestion. Remove it:
                        # only the specified-channel MTProto collector may write messages.
                        app.handlers.pop(-1, None)
                        # Administration is WebUI-only; do not revive old TG admin sessions.
                        for group, handlers in list(app.handlers.items()):
                            for handler in list(handlers):
                                if isinstance(handler, CommandHandler) and any(command.startswith("admin_") for command in handler.commands):
                                    app.remove_handler(handler, group)
                        await app.initialize()
                        info = await app.bot.get_webhook_info()
                        if info.url:
                            self.error = "检测到已有 webhook；请先停用旧部署并清除 webhook，再启用 polling"
                            await self._cleanup()
                            await asyncio.sleep(30)
                            continue
                        self.username = app.bot.username
                        await app.start()
                        await app.updater.start_polling(allowed_updates=["message", "inline_query", "callback_query"], bootstrap_retries=0)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:
                        self.error = f"TG 搜索机器人启动失败（{type(exc).__name__}）"
                        await self._cleanup()
                        await asyncio.sleep(30)
                await asyncio.sleep(3)
        finally:
            await self._cleanup()
