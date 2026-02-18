from __future__ import annotations

import logging

from telegram.ext import ApplicationBuilder


logger = logging.getLogger(__name__)


def apply_proxy(
    builder: ApplicationBuilder,
    proxy_enabled: bool,
    proxy_url: str | None,
) -> None:
    if not proxy_enabled or not proxy_url:
        return
    builder.proxy(proxy_url)
    builder.get_updates_proxy(proxy_url)
    logger.info("Telegram proxy enabled")

