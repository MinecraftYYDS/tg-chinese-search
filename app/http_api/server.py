from __future__ import annotations

import hmac
import json
import logging
import threading
from dataclasses import dataclass
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from app.context import RuntimeContext


logger = logging.getLogger(__name__)


def _parse_bool(value: str | None, default: bool = False) -> bool:
    if value is None:
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _parse_positive_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed <= 0:
        raise ValueError("must be a positive integer")
    return parsed


def _parse_non_negative_int(value: str | None, default: int) -> int:
    if value is None or value == "":
        return default
    parsed = int(value)
    if parsed < 0:
        raise ValueError("must be a non-negative integer")
    return parsed


def _build_handler(runtime: RuntimeContext):
    class Handler(BaseHTTPRequestHandler):
        server_version = "tg-search-api/1.0"

        def do_GET(self) -> None:  # noqa: N802
            parsed = urlparse(self.path)
            if parsed.path == "/healthz":
                self._write_json(HTTPStatus.OK, {"code": "ok", "message": "ok", "data": None})
                return
            if parsed.path not in {"/api/search", "/api/random"}:
                self._write_json(
                    HTTPStatus.NOT_FOUND,
                    {"code": "not_found", "message": "route not found", "data": None},
                )
                return

            if not self._api_enabled():
                self._write_json(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    {"code": "api_disabled", "message": "external api is disabled", "data": None},
                )
                return

            token = (runtime.config_store.get("external_api_token") or "").strip()
            if token and not self._check_bearer_token(token):
                self._write_json(
                    HTTPStatus.UNAUTHORIZED,
                    {"code": "unauthorized", "message": "invalid or missing bearer token", "data": None},
                )
                return

            try:
                query_dict = parse_qs(parsed.query)
                channel_filter = query_dict.get("channel", [None])[0]
                if channel_filter is not None:
                    channel_filter = channel_filter.strip()
                    if not channel_filter:
                        channel_filter = None

                if parsed.path == "/api/random":
                    limit = _parse_positive_int(
                        query_dict.get("limit", [None])[0],
                        default=runtime.default_random_limit,
                    )
                    limit = min(limit, runtime.max_random_limit)
                    total = runtime.search_service.random_count(channel_filter=channel_filter)
                    rows = runtime.search_service.random(limit=limit, channel_filter=channel_filter)
                    data = {
                        "channel": channel_filter,
                        "limit": limit,
                        "total": total,
                        "items": [
                            {
                                "id": row.id,
                                "chat_id": row.chat_id,
                                "message_id": row.message_id,
                                "channel_username": row.channel_username,
                                "source_link": row.source_link,
                                "text": row.text,
                                "timestamp": row.timestamp,
                            }
                            for row in rows
                        ],
                    }
                    self._write_json(
                        HTTPStatus.OK,
                        {"code": "ok", "message": "ok", "data": data},
                    )
                    return

                query = (query_dict.get("q", [""])[0] or "").strip()
                if not query:
                    self._write_json(
                        HTTPStatus.BAD_REQUEST,
                        {"code": "invalid_query", "message": "q is required", "data": None},
                    )
                    return

                limit = _parse_positive_int(
                    query_dict.get("limit", [None])[0],
                    default=runtime.default_search_limit,
                )
                limit = min(limit, 200)
                offset = _parse_non_negative_int(query_dict.get("offset", [None])[0], default=0)

                total = runtime.search_service.count(query=query, channel_filter=channel_filter)
                rows = runtime.search_service.search(
                    query=query,
                    limit=limit,
                    offset=offset,
                    channel_filter=channel_filter,
                )
                items = [
                    {
                        "id": row.id,
                        "chat_id": row.chat_id,
                        "message_id": row.message_id,
                        "channel_username": row.channel_username,
                        "source_link": row.source_link,
                        "text": row.text,
                        "timestamp": row.timestamp,
                    }
                    for row in rows
                ]
                self._write_json(
                    HTTPStatus.OK,
                    {
                        "code": "ok",
                        "message": "ok",
                        "data": {
                            "q": query,
                            "channel": channel_filter,
                            "limit": limit,
                            "offset": offset,
                            "total": total,
                            "items": items,
                        },
                    },
                )
            except ValueError as exc:
                self._write_json(
                    HTTPStatus.BAD_REQUEST,
                    {"code": "invalid_params", "message": str(exc), "data": None},
                )
            except Exception:
                logger.exception("external api request failed")
                self._write_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"code": "internal_error", "message": "internal server error", "data": None},
                )

        def log_message(self, fmt: str, *args) -> None:
            logger.info("external_api access: %s", fmt % args)

        def _api_enabled(self) -> bool:
            raw = runtime.config_store.get("external_api_enabled")
            if raw is None:
                return False
            return _parse_bool(raw, default=False)

        def _check_bearer_token(self, expected: str) -> bool:
            auth = self.headers.get("Authorization", "")
            if not auth.startswith("Bearer "):
                return False
            provided = auth[len("Bearer ") :].strip()
            if not provided:
                return False
            return hmac.compare_digest(provided, expected)

        def _write_json(self, status: HTTPStatus, payload: dict) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status.value)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return Handler


@dataclass(slots=True)
class ExternalSearchApiServer:
    runtime: RuntimeContext
    host: str
    port: int
    _server: ThreadingHTTPServer | None = None
    _thread: threading.Thread | None = None

    def start(self) -> None:
        if self._server is not None:
            return
        self._server = ThreadingHTTPServer((self.host, self.port), _build_handler(self.runtime))
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="external-search-api",
            daemon=True,
        )
        self._thread.start()

    def stop(self) -> None:
        if self._server is None:
            return
        self._server.shutdown()
        self._server.server_close()
        if self._thread is not None:
            self._thread.join(timeout=5)
        self._thread = None
        self._server = None

    @property
    def bound_port(self) -> int:
        if self._server is None:
            return self.port
        return int(self._server.server_address[1])
