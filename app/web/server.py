from __future__ import annotations

import asyncio
import hmac
import ipaddress
import logging
import os
import re
import secrets
import sqlite3
import time
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from urllib.parse import urlparse

import bcrypt
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, field_validator
from telethon.errors import FloodWaitError

from app.collector.service import Collector, proxy_options
from app.utils.link_builder import build_message_link
from app.web.bot import BotManager
from app.web.state import WebState

logger = logging.getLogger(__name__)


class Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Credentials(Model):
    username: str = Field(min_length=1, max_length=64, pattern=r"^[a-zA-Z0-9_.-]+$")
    password: str = Field(min_length=12, max_length=72)

    @field_validator("password")
    @classmethod
    def password_size(cls, value):
        if len(value.encode()) > 72:
            raise ValueError("密码 UTF-8 长度不能超过 72 字节")
        return value


class Setup(Credentials):
    bootstrap_token: str = Field(min_length=1, max_length=128)


class Login(Model):
    username: str = Field(max_length=64)
    password: str = Field(max_length=256)


class SettingsUpdate(Model):
    telegram_api_id: int | None = Field(default=None, gt=0)
    telegram_api_hash: str | None = Field(default=None, pattern=r"^[a-fA-F0-9]{32}$")
    proxy_enabled: bool | None = None
    proxy_url: str | None = Field(default=None, max_length=1000)
    external_api_enabled: bool | None = None
    bot_token: str | None = Field(default=None, max_length=200)
    bot_enabled: bool | None = None
    default_search_limit: int | None = Field(default=None, ge=1, le=200)
    max_random_limit: int | None = Field(default=None, ge=1, le=200)


class Phone(Model):
    phone: str = Field(pattern=r"^\+[1-9]\d{6,14}$")


class Code(Model):
    code: str = Field(pattern=r"^\d{4,8}$")


class Password(Model):
    password: str = Field(min_length=1, max_length=256)


class ChangePassword(Credentials):
    old_password: str = Field(max_length=256)


class ChannelAdd(Model):
    reference: str = Field(min_length=1, max_length=300)
    start_time: int = Field(default=0, ge=0)


class ChannelUpdate(Model):
    collecting: bool | None = None
    published: bool | None = None
    retry: bool = False


class Visibility(Model):
    hidden: bool


def create_app(data_dir: str | Path | None = None, *, background: bool = True) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app):
        state = WebState(Path(data_dir or os.getenv("TG_DATA_DIR", "data")))
        app.state.service = state
        # Bind the directory to a single process before opening the Telegram session.
        lock_file = (state.data_dir / "service.lock").open("a+")
        try:
            if os.name == "posix":
                import fcntl
                fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
            state.collector = Collector(state)
            state.bot = BotManager(state)
            if background:
                await state.collector.start()
                await state.bot.start()
            yield
        finally:
            if state.bot:
                await state.bot.stop()
            if state.collector:
                await state.collector.stop()
            state.conn.close()
            lock_file.close()

    app = FastAPI(title="TG Chinese Search", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def safety(request: Request, call_next):
        if request.method not in {"GET", "HEAD", "OPTIONS"}:
            origin = request.headers.get("origin")
            expected = os.getenv("TG_PUBLIC_ORIGIN") or f"{request.url.scheme}://{request.url.netloc}"
            if origin and origin.rstrip("/") != expected.rstrip("/"):
                return JSONResponse({"detail": "不允许跨站操作"}, status_code=403)
            length = request.headers.get("content-length", "0")
            if not length.isdigit() or int(length) > 16384:
                return JSONResponse({"detail": "请求过大"}, status_code=413)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        response.headers["Content-Security-Policy"] = "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
        if request.url.path.startswith("/api/"):
            response.headers["Cache-Control"] = "no-store"
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Pydantic's default response echoes rejected values (including passwords).
        return JSONResponse({"detail": "参数格式不正确，请检查输入长度、格式和必填项"}, status_code=422)

    def state(request: Request) -> WebState:
        return request.app.state.service

    def admin(request: Request):
        service = state(request)
        row = service.session(request.cookies.get("tg_session", ""))
        if not row:
            raise HTTPException(401, "请先登录管理后台")
        if request.method not in {"GET", "HEAD"}:
            if not hmac.compare_digest(request.headers.get("x-csrf-token", ""), row["csrf"]):
                raise HTTPException(403, "CSRF 校验失败，请刷新页面")
        return service

    def secure_telegram(request: Request, service=Depends(admin)):
        host = request.client.host if request.client else ""
        try:
            local = ipaddress.ip_address(host).is_loopback
        except ValueError:
            local = host == "testclient"
        # In production TG_PUBLIC_ORIGIN also rejects forged/misconfigured HTTP access.
        public = os.getenv("TG_PUBLIC_ORIGIN", "")
        if request.url.scheme != "https" and (public.startswith("https://") or not local):
            raise HTTPException(400, "Telegram 登录凭据只能通过 HTTPS 或本机连接提交")
        return service

    def check_auth_limit(service, scope):
        if service.check_limit(scope):
            raise HTTPException(429, "尝试过多，请 10 分钟后重试")

    def set_cookie(request, response, token):
        response.set_cookie("tg_session", token, httponly=True, samesite="strict",
                            secure=request.url.scheme == "https", max_age=12 * 3600, path="/")

    @app.get("/api/admin/session")
    async def session_info(request: Request):
        service = state(request)
        row = service.session(request.cookies.get("tg_session", ""))
        return {"initialized": service.initialized, "authenticated": bool(row),
                "csrf": row["csrf"] if row else None}

    @app.post("/api/admin/setup")
    async def setup(body: Setup, request: Request, response: Response):
        service = state(request)
        if service.initialized:
            raise HTTPException(409, "初始化已经完成")
        check_auth_limit(service, "setup")
        if not service.verify_bootstrap(body.bootstrap_token):
            service.failed_login("setup")
            raise HTTPException(403, "初始化凭据不正确")
        hashed = await asyncio.to_thread(service.password_hash, body.password)
        # Hashing yields the event loop: recheck inside the single writer transaction.
        with service.conn:
            if service.initialized:
                raise HTTPException(409, "初始化已经完成")
            service.conn.execute("INSERT INTO web_admin VALUES (1,?,?,?)", (body.username, hashed, int(time.time())))
        service.bootstrap_token = ""
        service.bootstrap_path.unlink(missing_ok=True)
        token, csrf = service.create_session()
        set_cookie(request, response, token)
        service.audit("web_setup")
        return {"csrf": csrf}

    @app.post("/api/admin/login")
    async def login(body: Login, request: Request, response: Response):
        service = state(request)
        check_auth_limit(service, "web_login")
        row = service.conn.execute("SELECT * FROM web_admin WHERE id=1").fetchone()
        encoded = body.password.encode()
        valid = bool(row and len(encoded) <= 72 and await asyncio.to_thread(bcrypt.checkpw, encoded, row["password_hash"].encode()))
        if not valid or not hmac.compare_digest(body.username.encode(), row["username"].encode() if row else b""):
            service.failed_login("web_login")
            raise HTTPException(401, "账号或密码错误")
        current = service.conn.execute("SELECT password_hash FROM web_admin WHERE id=1").fetchone()
        if current[0] != row["password_hash"]:
            raise HTTPException(401, "账号已修改，请重新登录")
        with service.conn:
            service.conn.execute("DELETE FROM web_login_limits WHERE scope='web_login'")
        token, csrf = service.create_session()
        set_cookie(request, response, token)
        service.audit("web_login")
        return {"csrf": csrf}

    @app.post("/api/admin/logout")
    async def logout(request: Request, response: Response, service=Depends(admin)):
        with service.conn:
            service.conn.execute("DELETE FROM web_sessions WHERE token_hash=?", (service.token_hash(request.cookies.get("tg_session", "")),))
        response.delete_cookie("tg_session", path="/")
        return {"ok": True}

    @app.post("/api/admin/password")
    async def password_change(body: ChangePassword, service=Depends(admin)):
        check_auth_limit(service, "password_change")
        row = service.conn.execute("SELECT * FROM web_admin WHERE id=1").fetchone()
        old = body.old_password.encode()
        if len(old) > 72 or not await asyncio.to_thread(bcrypt.checkpw, old, row["password_hash"].encode()):
            service.failed_login("password_change")
            raise HTTPException(403, "原密码不正确")
        hashed = await asyncio.to_thread(service.password_hash, body.password)
        with service.conn:
            service.conn.execute("UPDATE web_admin SET username=?,password_hash=? WHERE id=1", (body.username, hashed))
            service.conn.execute("DELETE FROM web_sessions")
        service.audit("password_change")
        return {"ok": True, "message": "密码已修改，请重新登录"}

    @app.get("/api/admin/overview")
    async def overview(service=Depends(admin)):
        return {"telegram": service.collector.status(), "bot": service.bot.status(),
                "channels": [{k: v for k, v in row.items() if k != "access_hash"} for row in service.channels()], "messages": service.repo.get_all_messages_count(),
                "settings": service.settings()}

    @app.get("/api/admin/settings")
    async def settings(service=Depends(admin)):
        return service.settings()

    @app.patch("/api/admin/settings")
    async def update_settings(body: SettingsUpdate, service=Depends(secure_telegram)):
        values = body.model_dump(exclude_unset=True, exclude_none=True)
        if values.get("proxy_url"):
            try:
                proxy_options(values["proxy_url"])
            except Exception:
                raise HTTPException(400, "代理地址格式错误")
        if values.get("proxy_enabled") and not (values.get("proxy_url") or service.store.get("telegram_proxy_url")):
            raise HTTPException(400, "请先填写代理地址")
        if values.get("bot_token") and not re.fullmatch(r"\d+:[A-Za-z0-9_-]{20,}", values["bot_token"]):
            raise HTTPException(400, "Bot Token 格式不正确")
        creds_changed = any(str(values[k]) != service.store.get(k) for k in ("telegram_api_id", "telegram_api_hash") if k in values)
        if creds_changed and service.collector.account:
            raise HTTPException(409, "更换应用凭据前请先退出采集账号")
        mapping = {"proxy_enabled": "telegram_proxy_enabled", "proxy_url": "telegram_proxy_url"}
        reconnect = creds_changed or any(
            (str(values[k]).lower() if isinstance(values[k], bool) else str(values[k])) != (service.store.get(mapping[k]) or "")
            for k in ("proxy_enabled", "proxy_url") if k in values
        )
        if reconnect:
            await service.collector.reset()
        for key, value in values.items():
            service.store.set(mapping.get(key, key), str(value).lower() if isinstance(value, bool) else str(value))
        service.audit("settings_update", ",".join(values.keys()))
        return service.settings()

    @app.post("/api/admin/api-token")
    async def rotate_token(service=Depends(admin)):
        token = secrets.token_urlsafe(32)
        service.store.set("external_api_token", token)
        service.audit("api_token_rotate")
        return {"token": token, "message": "仅显示这一次，请妥善保存，并更新 bot 配置"}

    async def telegram_call(service, method, *args):
        check_auth_limit(service, "telegram_actions")
        if service.collector.account_retry_at > time.time():
            raise HTTPException(429, f"Telegram 限流等待中，请在 {int(service.collector.account_retry_at-time.time())+1} 秒后重试")
        if method.__name__ in {"send_code", "start_qr"}:
            scope = "tg_cooldown_" + method.__name__
            if service.check_limit(scope):
                raise HTTPException(429, "操作过于频繁，请稍后再试")
            with service.conn:
                service.conn.execute("INSERT OR REPLACE INTO web_login_limits VALUES (?,0,?)", (scope, int(time.time()) + 30))
        service.failed_login("telegram_actions")
        try:
            result = await asyncio.wait_for(method(*args), timeout=60)
            with service.conn:
                service.conn.execute("DELETE FROM web_login_limits WHERE scope='telegram_actions'")
            return result
        except (HTTPException, asyncio.CancelledError):
            raise
        except FloodWaitError as exc:
            service.collector.account_retry_at = int(time.time()) + exc.seconds + 1
            raise HTTPException(429, Collector.safe_error(exc))
        except ValueError as exc:
            # Only explicit user-facing validation errors, not raw RPC argument data.
            message = str(exc)
            if not any('\u4e00' <= ch <= '\u9fff' for ch in message):
                message = Collector.safe_error(exc)
            raise HTTPException(400, message)
        except Exception as exc:
            raise HTTPException(400, Collector.safe_error(exc))

    @app.get("/api/admin/telegram")
    async def telegram_status(service=Depends(secure_telegram)):
        return service.collector.status()

    @app.post("/api/admin/telegram/phone")
    async def telegram_phone(body: Phone, service=Depends(secure_telegram)):
        return await telegram_call(service, service.collector.send_code, body.phone)

    @app.post("/api/admin/telegram/code")
    async def telegram_code(body: Code, service=Depends(secure_telegram)):
        return await telegram_call(service, service.collector.submit_code, body.code)

    @app.post("/api/admin/telegram/password")
    async def telegram_password(body: Password, service=Depends(secure_telegram)):
        return await telegram_call(service, service.collector.submit_password, body.password)

    @app.post("/api/admin/telegram/qr")
    async def telegram_qr(service=Depends(secure_telegram)):
        return await telegram_call(service, service.collector.start_qr)

    @app.post("/api/admin/telegram/cancel")
    async def telegram_cancel(service=Depends(secure_telegram)):
        await service.collector.reset()
        return service.collector.status()

    @app.post("/api/admin/telegram/logout")
    async def telegram_logout(service=Depends(secure_telegram)):
        try:
            await service.collector.reset(logout=True)
        except Exception as exc:
            raise HTTPException(400, Collector.safe_error(exc))
        return service.collector.status()

    @app.get("/api/admin/telegram/channels")
    async def joined_channels(service=Depends(admin)):
        return await telegram_call(service, service.collector.joined_channels)

    @app.post("/api/admin/telegram/test")
    async def test_connection(service=Depends(secure_telegram)):
        return await telegram_call(service, service.collector.test_connection)

    @app.get("/api/admin/channels")
    async def channels(service=Depends(admin)):
        # Never expose peer access hashes in management responses.
        return [{k: v for k, v in row.items() if k != "access_hash"} for row in service.channels()]

    @app.post("/api/admin/channels")
    async def channel_add(body: ChannelAdd, service=Depends(admin)):
        chat_id = await telegram_call(service, service.collector.add_channel, body.reference, body.start_time)
        return {"chat_id": chat_id}

    @app.patch("/api/admin/channels/{chat_id}")
    async def channel_update(chat_id: int, body: ChannelUpdate, service=Depends(admin)):
        if not service.conn.execute("SELECT 1 FROM managed_channels WHERE chat_id=?", (chat_id,)).fetchone():
            raise HTTPException(404, "频道不存在")
        with service.conn:
            for key in ("collecting", "published"):
                value = getattr(body, key)
                if value is not None:
                    service.conn.execute(f"UPDATE managed_channels SET {key}=? WHERE chat_id=?", (int(value), chat_id))
            if body.collecting is False:
                service.conn.execute("UPDATE managed_channels SET status='paused' WHERE chat_id=?", (chat_id,))
            elif body.collecting is True or body.retry:
                service.conn.execute("UPDATE managed_channels SET collecting=1,retry_at=0,error='',status='pending' WHERE chat_id=?", (chat_id,))
        service.audit("channel_update", f"{chat_id}: {body.model_dump(exclude_none=True)}")
        return {"ok": True}

    @app.delete("/api/admin/channels/{chat_id}")
    async def channel_delete(chat_id: int, delete_data: bool = False, service=Depends(admin)):
        with service.conn:
            service.conn.execute("DELETE FROM managed_channels WHERE chat_id=?", (chat_id,))
            if delete_data:
                service.conn.execute("DELETE FROM channel_messages WHERE chat_id=?", (chat_id,))
                service.conn.execute("DELETE FROM channel_alias WHERE chat_id=?", (chat_id,))
                service.conn.execute("DELETE FROM message_controls WHERE chat_id=?", (chat_id,))
        service.collector.entities.pop(chat_id, None)
        service.audit("channel_delete", f"{chat_id}, delete_data={delete_data}")
        return {"ok": True}

    def message_item(row):
        item = asdict(row)
        if not item["source_link"]:
            item["source_link"] = build_message_link(row.channel_username, row.message_id, row.source_link, row.chat_id)
        return item

    @app.get("/api/admin/search")
    async def admin_search(q: str = Query(default="", max_length=1000), channel: str | None = None,
                           limit: int = Query(default=20, ge=1, le=100), offset: int = Query(default=0, ge=0),
                           show_hidden: bool = False, since: int = 0, until: int = 0, service=Depends(admin)):
        with service.reader(private=True, hidden=show_hidden) as (repo, search):
            if since or until:
                # Shared repository applies an optional time range to both query and count.
                repo.time_range = (since, until)
            if q.strip():
                rows, total = search.search(q, limit, offset, channel), search.count(q, channel)
            else:
                chat_id = repo.resolve_channel(channel)
                where = repo._visibility()
                params = []
                if channel is not None:
                    where += " AND m.chat_id=?"
                    params.append(chat_id)
                if since:
                    where += " AND m.timestamp>=?"
                    params.append(since)
                if until:
                    where += " AND m.timestamp<=?"
                    params.append(until)
                total = repo.conn.execute("SELECT COUNT(*) FROM channel_messages m WHERE " + where, params).fetchone()[0]
                raw = repo.conn.execute("SELECT m.* FROM channel_messages m WHERE " + where + " ORDER BY m.timestamp DESC,m.id DESC LIMIT ? OFFSET ?", [*params, limit, offset]).fetchall()
                from app.storage.repository import SearchRow
                rows = [SearchRow(**{k: r[k] for k in SearchRow.__dataclass_fields__}) for r in raw]
            items = []
            for row in rows:
                item = message_item(row)
                control = repo.conn.execute("SELECT hidden FROM message_controls WHERE chat_id=? AND message_id=?", (row.chat_id, row.message_id)).fetchone()
                item["hidden"] = bool(control and control[0])
                items.append(item)
        return {"items": items, "total": total}

    @app.patch("/api/admin/messages/{chat_id}/{message_id}")
    async def hide_message(chat_id: int, message_id: int, body: Visibility, service=Depends(admin)):
        with service.conn:
            service.conn.execute("""INSERT INTO message_controls(chat_id,message_id,hidden) VALUES (?,?,?)
                ON CONFLICT(chat_id,message_id) DO UPDATE SET hidden=excluded.hidden""", (chat_id, message_id, int(body.hidden)))
        service.audit("message_visibility", f"{chat_id}:{message_id} hidden={body.hidden}")
        return {"ok": True}

    @app.get("/api/admin/audit")
    async def audit(service=Depends(admin)):
        return [dict(row) for row in service.conn.execute("SELECT id,action,detail,created_at FROM admin_audit_log ORDER BY id DESC LIMIT 100")]

    def api_error(status, code, message):
        return JSONResponse({"code": code, "message": message, "data": None}, status_code=status)

    async def external(request: Request, random: bool):
        service = state(request)
        if service.store.get("external_api_enabled") != "true":
            return api_error(503, "api_disabled", "external api is disabled")
        token = service.store.get("external_api_token") or ""
        auth = request.headers.get("authorization", "")
        if not token or not hmac.compare_digest(auth.encode(), f"Bearer {token}".encode()):
            return api_error(401, "unauthorized", "invalid or missing bearer token")
        try:
            params = request.query_params
            channel = params.get("channel") or None
            q = (params.get("q") or "").strip()
            if not random and not q:
                return api_error(400, "invalid_query", "q is required")
            if len(q) > 1000 or (channel and len(channel) > 300):
                return api_error(400, "invalid_params", "query too long")
            default = service.store.get("default_random_limit" if random else "default_search_limit") or ("1" if random else "50")
            limit, offset = int(params.get("limit") or default), int(params.get("offset") or 0)
            if limit <= 0 or offset < 0:
                raise ValueError()
            limit = min(limit, int(service.store.get("max_random_limit") or 10) if random else 200)
            with service.reader() as (_, search):
                if random:
                    rows, total = search.random(limit, channel), search.random_count(channel)
                else:
                    rows, total = search.search(q, limit, offset, channel), search.count(q, channel)
                data = {"channel": channel, "limit": limit, "total": total, "items": [message_item(row) for row in rows]}
                if not random:
                    data.update(q=q, offset=offset)
            return {"code": "ok", "message": "ok", "data": data}
        except (ValueError, OverflowError):
            return api_error(400, "invalid_params", "invalid limit or offset")
        except Exception as exc:
            logger.warning("External query failed (%s)", type(exc).__name__)
            return api_error(500, "internal_error", "internal server error")

    @app.get("/api/search")
    async def api_search(request: Request):
        return await external(request, False)

    @app.get("/api/random")
    async def api_random(request: Request):
        return await external(request, True)

    @app.get("/healthz")
    async def health():
        return {"code": "ok", "message": "ok", "data": None}

    dist = Path(__file__).resolve().parents[2] / "web" / "dist"
    if (dist / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=dist / "assets"), name="assets")

    @app.get("/{path:path}")
    async def frontend(path: str):
        if path.startswith("api/"):
            return api_error(404, "not_found", "route not found")
        if (dist / "index.html").is_file():
            return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})
        return JSONResponse({"message": "WebUI 尚未构建，请在 web 目录执行 npm ci && npm run build"}, status_code=503)

    return app
