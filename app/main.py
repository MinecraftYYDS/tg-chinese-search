from __future__ import annotations

import argparse
import logging
import threading
import time

from cryptography.fernet import Fernet
from telegram import Update
from telegram.ext import (
    Application,
    ApplicationBuilder,
    CallbackQueryHandler,
    ContextTypes,
    CommandHandler,
    InlineQueryHandler,
    MessageHandler,
    TypeHandler,
    filters,
)

from app.admin.auth import AdminAuthService
from app.admin.commands import admin_apply, admin_get, admin_list, admin_login, admin_logout, admin_set
from app.admin.config_store import ConfigStore
from app.config import Settings, load_settings
from app.context import RuntimeContext
from app.importer.telegram_json import import_telegram_export
from app.ingest.telegram_adapter import on_any_update
from app.interaction.commands import help_command, search_command, start_command
from app.interaction.inline_mode import handle_inline_query
from app.interaction.private_chat import (
    handle_noop_pagination,
    handle_private_pagination,
    handle_private_search,
)
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
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("httpcore").setLevel(logging.WARNING)


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
    app.add_handler(TypeHandler(type=Update, callback=on_any_update), group=-1)

    app.add_handler(CommandHandler("admin_login", admin_login))
    app.add_handler(CommandHandler("admin_set", admin_set))
    app.add_handler(CommandHandler("admin_get", admin_get))
    app.add_handler(CommandHandler("admin_list", admin_list))
    app.add_handler(CommandHandler("admin_logout", admin_logout))
    app.add_handler(CommandHandler("admin_apply", admin_apply))

    app.add_handler(CommandHandler("start", start_command))
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("helph", help_command))
    app.add_handler(CommandHandler("search", search_command))
    app.add_handler(InlineQueryHandler(handle_inline_query))
    app.add_handler(CallbackQueryHandler(handle_noop_pagination, pattern=r"^noop$"))
    app.add_handler(CallbackQueryHandler(handle_private_pagination, pattern=r"^pg:"))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), handle_private_search))


async def _error_handler(update: object, context) -> None:
    err = getattr(context, "error", None)
    if isinstance(err, BaseException):
        logger.error("Unhandled PTB error on update=%s error=%r", update, err, exc_info=err)
        return
    logger.error("Unhandled PTB error on update=%s error=%r", update, err)


async def _api_probe_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    runtime = context.application.bot_data.get("runtime")
    if runtime is None:
        return
    now = time.time()
    try:
        await context.bot.get_me(
            connect_timeout=10,
            read_timeout=15,
            write_timeout=15,
            pool_timeout=10,
        )
        runtime.last_api_ok_ts = now
        logger.debug("api_probe_ok")
    except Exception as exc:
        logger.warning("api_probe_failed error=%r", exc)
    stale_seconds = int(now - runtime.last_api_ok_ts) if runtime.last_api_ok_ts > 0 else 10**9
    if stale_seconds > 180:
        logger.error("API stale for %ss, forcing polling restart via stop_running()", stale_seconds)
        context.application.stop_running()
        return


def _build_application(settings: Settings, runtime: RuntimeContext) -> Application:
    builder = (
        ApplicationBuilder()
        .token(settings.bot_token)
        .connect_timeout(10)
        .read_timeout(30)
        .write_timeout(30)
        .pool_timeout(10)
        .get_updates_connect_timeout(10)
        .get_updates_read_timeout(30)
        .get_updates_write_timeout(30)
        .get_updates_pool_timeout(10)
    )
    apply_proxy(builder, settings.telegram_proxy_enabled, settings.telegram_proxy_url)
    app = builder.build()
    app.bot_data["runtime"] = runtime
    runtime.last_api_ok_ts = time.time()
    _register_handlers(app)
    app.add_error_handler(_error_handler)
    if app.job_queue is not None:
        app.job_queue.run_repeating(
            _api_probe_job,
            interval=60,
            first=10,
            name="api_probe",
            job_kwargs={
                "max_instances": 1,
                "coalesce": True,
                "misfire_grace_time": 30,
            },
        )
    else:
        logger.warning(
            "JobQueue not available; api probe scheduler disabled. Install python-telegram-bot[job-queue] to enable it."
        )
    return app


def _start_heartbeat(runtime: RuntimeContext) -> threading.Event:
    stop_event = threading.Event()

    def _worker() -> None:
        while not stop_event.is_set():
            now = time.time()
            up_sec = int(now - runtime.started_at_ts)
            api_stale = int(now - runtime.last_api_ok_ts) if runtime.last_api_ok_ts > 0 else -1
            if runtime.last_update_ts > 0:
                idle_sec = int(now - runtime.last_update_ts)
                logger.info(
                    "heartbeat: alive uptime=%ss last_update_ago=%ss api_last_ok_ago=%ss",
                    up_sec,
                    idle_sec,
                    api_stale,
                )
            else:
                logger.info(
                    "heartbeat: alive uptime=%ss no_updates_yet=true api_last_ok_ago=%ss",
                    up_sec,
                    api_stale,
                )
            stop_event.wait(60)

    thread = threading.Thread(target=_worker, daemon=True, name="bot-heartbeat")
    thread.start()
    return stop_event


def run_bot(settings: Settings, runtime: RuntimeContext) -> None:
    if not settings.bot_token:
        raise ValueError("BOT_TOKEN missing (or not configured in dynamic config)")
    logger.info(
        "Starting bot mode=%s allowed_updates=%s",
        settings.app_mode,
        ["channel_post", "edited_channel_post", "message", "inline_query", "callback_query"],
    )
    logger.info(
        "If no channel updates arrive: ensure bot is admin in the channel and no conflicting webhook/polling setup."
    )
    heartbeat_stop = _start_heartbeat(runtime)
    retry_seconds = 5
    try:
        while True:
            app = _build_application(settings, runtime)
            try:
                if settings.app_mode == "webhook":
                    kwargs = dict(
                        listen=settings.webhook_listen_host,
                        port=settings.webhook_listen_port,
                        webhook_url=settings.webhook_url,
                        allowed_updates=["channel_post", "edited_channel_post", "message", "inline_query", "callback_query"],
                        drop_pending_updates=False,
                        bootstrap_retries=-1,
                    )
                    if settings.webhook_cert_path and settings.webhook_key_path:
                        kwargs["cert"] = settings.webhook_cert_path
                        kwargs["key"] = settings.webhook_key_path
                    logger.warning("Starting webhook loop instance")
                    app.run_webhook(**kwargs)
                else:
                    logger.warning("Starting polling loop instance")
                    app.run_polling(
                        allowed_updates=["channel_post", "edited_channel_post", "message", "inline_query", "callback_query"],
                        bootstrap_retries=-1,
                        timeout=20,
                        poll_interval=0.5,
                    )
                logger.error("Polling/webhook returned unexpectedly. Restarting in %s seconds.", retry_seconds)
                time.sleep(retry_seconds)
            except KeyboardInterrupt:
                logger.info("KeyboardInterrupt received, bot exiting")
                return
            except Exception:
                logger.exception("Bot crashed due to network/runtime error. Retrying in %s seconds.", retry_seconds)
                time.sleep(retry_seconds)
    finally:
        heartbeat_stop.set()


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
