from __future__ import annotations

import argparse
import logging

from cryptography.fernet import Fernet
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    filters,
)

from app.admin.auth import AdminAuthService
from app.admin.commands import admin_apply, admin_get, admin_list, admin_login, admin_logout, admin_set
from app.admin.config_store import ConfigStore
from app.config import Settings, load_settings
from app.context import RuntimeContext
from app.importer.telegram_json import import_telegram_export
from app.ingest.telegram_adapter import on_channel_post, on_edited_channel_post
from app.interaction.commands import search_command
from app.interaction.inline_mode import handle_inline_query
from app.interaction.private_chat import handle_private_pagination, handle_private_search
from app.network.proxy import apply_proxy
from app.search.service import SearchService
from app.search.tokenizer import default_tokenizer
from app.storage.db import connect_db, init_db
from app.storage.repository import MessageRepository


logging.basicConfig(
    format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


def _seed_dynamic_config(config_store: ConfigStore, settings: Settings) -> None:
    defaults = {
        "telegram_proxy_enabled": str(settings.telegram_proxy_enabled).lower(),
        "telegram_proxy_url": settings.telegram_proxy_url or "",
        "bot_token": settings.bot_token or "",
        "sqlite_path": settings.sqlite_path,
        "webhook_url": settings.webhook_url,
        "webhook_listen_host": settings.webhook_listen_host,
        "webhook_listen_port": str(settings.webhook_listen_port),
        "default_search_limit": str(settings.default_search_limit),
        "private_page_size": str(settings.private_page_size),
        "private_separator": settings.private_separator,
    }
    for key, value in defaults.items():
        if config_store.get(key) is None and value != "":
            config_store.set(key, value)


def _resolve_runtime_value(config_store: ConfigStore, key: str, fallback: str) -> str:
    val = config_store.get(key)
    if val is None or val == "":
        return fallback
    return val


def create_runtime(settings: Settings) -> tuple[RuntimeContext, Settings]:
    conn = connect_db(settings.sqlite_path)
    init_db(conn)
    repo = MessageRepository(conn)
    config_store = ConfigStore(repo=repo, fernet=Fernet(settings.config_encryption_key.encode("utf-8")))
    _seed_dynamic_config(config_store, settings)

    token = _resolve_runtime_value(config_store, "bot_token", settings.bot_token)
    settings.bot_token = token
    settings.telegram_proxy_enabled = (
        _resolve_runtime_value(config_store, "telegram_proxy_enabled", str(settings.telegram_proxy_enabled)).lower()
        == "true"
    )
    settings.telegram_proxy_url = _resolve_runtime_value(config_store, "telegram_proxy_url", settings.telegram_proxy_url or "")
    settings.default_search_limit = int(
        _resolve_runtime_value(config_store, "default_search_limit", str(settings.default_search_limit))
    )
    settings.private_page_size = int(
        _resolve_runtime_value(config_store, "private_page_size", str(settings.private_page_size))
    )
    settings.private_separator = _resolve_runtime_value(
        config_store, "private_separator", settings.private_separator
    )
    settings.webhook_url = _resolve_runtime_value(config_store, "webhook_url", settings.webhook_url)
    settings.webhook_listen_host = _resolve_runtime_value(
        config_store, "webhook_listen_host", settings.webhook_listen_host
    )
    settings.webhook_listen_port = int(
        _resolve_runtime_value(config_store, "webhook_listen_port", str(settings.webhook_listen_port))
    )

    tokenizer = default_tokenizer()
    search_service = SearchService(repo=repo, tokenizer=tokenizer)
    admin_auth = AdminAuthService(
        repo=repo,
        admin_ids=settings.admin_ids,
        password_hash=settings.admin_password_hash,
        session_ttl_seconds=settings.admin_session_ttl_seconds,
        max_failed_attempts=settings.admin_max_failed_attempts,
        lockout_seconds=settings.admin_lockout_seconds,
    )
    runtime = RuntimeContext(
        repo=repo,
        tokenizer=tokenizer,
        search_service=search_service,
        admin_auth=admin_auth,
        config_store=config_store,
        default_search_limit=settings.default_search_limit,
        private_page_size=settings.private_page_size,
        private_separator=settings.private_separator,
        proxy_fail_open=settings.proxy_fail_open,
    )
    return runtime, settings


def _register_handlers(app: Application) -> None:
    app.add_handler(CommandHandler("admin_login", admin_login))
    app.add_handler(CommandHandler("admin_set", admin_set))
    app.add_handler(CommandHandler("admin_get", admin_get))
    app.add_handler(CommandHandler("admin_list", admin_list))
    app.add_handler(CommandHandler("admin_logout", admin_logout))
    app.add_handler(CommandHandler("admin_apply", admin_apply))

    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_handler(CallbackQueryHandler(handle_private_pagination, pattern=r"^pg:"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_private_search))
    app.add_handler(
        MessageHandler(filters.UpdateType.CHANNEL_POST & filters.ALL, on_channel_post)
    )
    app.add_handler(
        MessageHandler(filters.UpdateType.EDITED_CHANNEL_POST & filters.ALL, on_edited_channel_post)
    )


def _build_application(settings: Settings, runtime: RuntimeContext) -> Application:
    builder = ApplicationBuilder().token(settings.bot_token)
    apply_proxy(builder, settings.telegram_proxy_enabled, settings.telegram_proxy_url)
    app = builder.build()
    app.bot_data["runtime"] = runtime
    _register_handlers(app)
    return app


def run_bot(settings: Settings, runtime: RuntimeContext) -> None:
    if not settings.bot_token:
        raise ValueError("BOT_TOKEN missing (or not configured in dynamic config)")
    app = _build_application(settings, runtime)
    if settings.app_mode == "webhook":
        kwargs = dict(
            listen=settings.webhook_listen_host,
            port=settings.webhook_listen_port,
            webhook_url=settings.webhook_url,
            allowed_updates=["channel_post", "edited_channel_post", "message", "inline_query", "callback_query"],
            drop_pending_updates=False,
        )
        if settings.webhook_cert_path and settings.webhook_key_path:
            kwargs["cert"] = settings.webhook_cert_path
            kwargs["key"] = settings.webhook_key_path
        app.run_webhook(**kwargs)
        return
    app.run_polling(allowed_updates=["channel_post", "edited_channel_post", "message", "inline_query", "callback_query"])


def run_import(settings: Settings, runtime: RuntimeContext, json_path: str, dry_run: bool) -> None:
    stats = import_telegram_export(
        json_path=json_path,
        repo=runtime.repo,
        tokenizer=runtime.tokenizer,
        dry_run=dry_run,
    )
    logger.info(
        "import done total=%s imported=%s skipped=%s dry_run=%s",
        stats.total,
        stats.imported,
        stats.skipped,
        dry_run,
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Telegram Chinese search bot")
    sub = parser.add_subparsers(dest="command")
    run_parser = sub.add_parser("run", help="Run telegram bot")
    run_parser.set_defaults(command="run")
    import_parser = sub.add_parser("import", help="Import telegram desktop export JSON")
    import_parser.add_argument("--json", required=True, help="Path to result.json")
    import_parser.add_argument("--dry-run", action="store_true")
    return parser.parse_args()


def main() -> None:
    settings = load_settings()
    runtime, settings = create_runtime(settings)
    args = parse_args()
    if args.command == "import":
        run_import(settings, runtime, json_path=args.json, dry_run=args.dry_run)
        return
    run_bot(settings, runtime)


if __name__ == "__main__":
    main()
