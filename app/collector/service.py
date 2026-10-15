from __future__ import annotations

import asyncio
import contextlib
import io
import logging
import time
from datetime import datetime, timezone
from urllib.parse import urlparse, unquote

import qrcode
import qrcode.image.svg
from telethon import TelegramClient, events, functions, types, utils
from telethon.errors import (
    AuthKeyUnregisteredError, ChannelPrivateError, FloodWaitError,
    PhoneCodeExpiredError, SessionPasswordNeededError, UserDeactivatedError,
)
from telethon.sessions import StringSession

from app.normalize.channel_message import NormalizedMessage

logger = logging.getLogger(__name__)


def proxy_options(url: str):
    import socks
    p = urlparse(url)
    if p.scheme not in {"socks5", "socks4", "http"} or not p.hostname or not p.port:
        raise ValueError("代理必须为 socks5://、socks4:// 或 http://host:port")
    kind = {"socks5": socks.SOCKS5, "socks4": socks.SOCKS4, "http": socks.HTTP}[p.scheme]
    return (kind, p.hostname, p.port, True, unquote(p.username) if p.username else None,
            unquote(p.password) if p.password else None)


class Collector:
    def __init__(self, state):
        self.state = state
        self.client = None
        self.task = None
        self.qr_task = None
        self.login = {"phase": "idle", "error": ""}
        self.account = None
        self.phone = None
        self.phone_hash = None
        self.auth_lock = asyncio.Lock()
        self.reconcile_at = {}
        self.account_retry_at = 0
        self.entities = {}

    def status(self):
        return {**self.login, "connected": bool(self.client and self.client.is_connected()),
                "account": self.account, "retry_at": self.account_retry_at}

    async def start(self):
        self.task = asyncio.create_task(self._run(), name="telegram-collector")

    async def stop(self):
        for task in (self.qr_task, self.task):
            if task:
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
        if self.client:
            if self.account:
                self._save_session()
            await self.client.disconnect()
        self.client = None

    async def reset(self, *, logout=False):
        async with self.auth_lock:
            if self.qr_task:
                self.qr_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await self.qr_task
                self.qr_task = None
            if self.client:
                if logout and self.account:
                    await asyncio.wait_for(self.client.log_out(), 20)
                await self.client.disconnect()
            if logout:
                self.state.store.set("telegram_session", "")
                self.state.audit("telegram_logout")
            self.client = None
            self.account = None
            self.phone = self.phone_hash = None
            self.login = {"phase": "idle", "error": ""}
            self.account_retry_at = 0
            self.entities.clear()

    async def _client(self):
        if self.client:
            if not self.client.is_connected():
                await asyncio.wait_for(self.client.connect(), 25)
            return self.client
        get = self.state.store.get
        api_id, api_hash = get("telegram_api_id"), get("telegram_api_hash")
        if not api_id or not api_hash:
            raise ValueError("请先在设置中填写 API ID 和 API Hash")
        proxy = None
        if get("telegram_proxy_enabled") == "true":
            proxy = proxy_options(get("telegram_proxy_url") or "")
        client = TelegramClient(StringSession(get("telegram_session") or ""), int(api_id), api_hash,
                                proxy=proxy, flood_sleep_threshold=0, request_retries=1,
                                connection_retries=2, retry_delay=2, timeout=15,
                                device_model="TG Chinese Search", system_version="WebUI")
        client.add_event_handler(self._new_or_edit, events.NewMessage())
        client.add_event_handler(self._new_or_edit, events.MessageEdited())
        client.add_event_handler(self._deleted, events.MessageDeleted())
        try:
            await asyncio.wait_for(client.connect(), 25)
        except BaseException:
            await client.disconnect()
            raise
        self.client = client
        return client

    def _save_session(self):
        self.state.store.set("telegram_session", self.client.session.save())

    async def _authorized(self):
        me = await self.client.get_me()
        if not me or me.bot:
            raise ValueError("必须登录 Telegram 用户账号，不能使用 Bot 身份")
        self.account = {"id": me.id, "name": " ".join(filter(None, [me.first_name, me.last_name])),
                        "username": me.username or ""}
        self.login = {"phase": "authorized", "error": ""}
        self.phone = self.phone_hash = None
        self._save_session()
        self.state.audit("telegram_login")

    async def send_code(self, phone: str):
        async with self.auth_lock:
            self._guard_login()
            client = await self._client()
            result = await client.send_code_request(phone)
            self.phone, self.phone_hash = phone, result.phone_code_hash
            self.login = {"phase": "code", "error": "", "expires_at": int(time.time()) + 300}
        return self.status()

    def _guard_login(self):
        if self.account:
            raise ValueError("当前账号已登录；切换账号请先退出")
        if self.qr_task and not self.qr_task.done():
            raise ValueError("扫码登录进行中，请先取消")

    async def submit_code(self, code: str):
        async with self.auth_lock:
            if self.login["phase"] != "code" or time.time() > self.login.get("expires_at", 0):
                raise ValueError("验证码流程已过期，请重新发送")
            try:
                await self.client.sign_in(self.phone, code, phone_code_hash=self.phone_hash)
            except SessionPasswordNeededError:
                self.login = {"phase": "password", "error": "", "expires_at": int(time.time()) + 300}
            except PhoneCodeExpiredError:
                self.phone = self.phone_hash = None
                self.login = {"phase": "idle", "error": "验证码已过期，请重新发送"}
            else:
                await self._authorized()
        return self.status()

    async def submit_password(self, password: str):
        async with self.auth_lock:
            if self.login["phase"] != "password" or time.time() > self.login.get("expires_at", 0):
                raise ValueError("两步验证流程已过期，请重新登录")
            await self.client.sign_in(password=password)
            await self._authorized()
        return self.status()

    async def start_qr(self):
        async with self.auth_lock:
            self._guard_login()
            client = await self._client()
            qr = await client.qr_login()
            image = qrcode.make(qr.url, image_factory=qrcode.image.svg.SvgPathImage)
            output = io.BytesIO()
            image.save(output)
            self.login = {"phase": "qr", "error": "", "qr_svg": output.getvalue().decode(),
                          "expires_at": int(qr.expires.timestamp())}

            async def wait():
                try:
                    await qr.wait(timeout=65)
                    await self._authorized()
                except SessionPasswordNeededError:
                    self.login = {"phase": "password", "error": "", "expires_at": int(time.time()) + 300}
                except asyncio.TimeoutError:
                    self.login = {"phase": "idle", "error": "二维码已过期，请刷新"}
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self.login = {"phase": "idle", "error": self.safe_error(exc)}
            self.qr_task = asyncio.create_task(wait(), name="telegram-qr-login")
        return self.status()

    @staticmethod
    def safe_error(exc: Exception) -> str:
        if isinstance(exc, FloodWaitError):
            return f"Telegram 限流，请等待 {exc.seconds} 秒"
        if isinstance(exc, (asyncio.TimeoutError, TimeoutError)):
            return "Telegram 连接超时，请检查网络或代理设置"
        if isinstance(exc, ValueError):
            # Only our own validation messages; never echo arbitrary Telegram inputs.
            return "操作参数无效或找不到频道，请检查账号权限及配置"
        return f"Telegram 操作失败（{type(exc).__name__}）"

    async def joined_channels(self):
        if not self.account:
            raise ValueError("请先登录采集账号")
        result = []
        async for dialog in self.client.iter_dialogs():
            entity = dialog.entity
            if isinstance(entity, types.Channel) and entity.broadcast:
                result.append({"chat_id": utils.get_peer_id(entity), "title": entity.title,
                               "username": entity.username or ""})
        return result

    async def test_connection(self):
        async with self.auth_lock:
            client = await self._client()
            await client(functions.help.GetConfigRequest())
        return {"ok": True, "message": "Telegram 连接正常"}

    async def add_channel(self, reference: str, start_time: int = 0):
        if not self.account:
            raise ValueError("请先登录采集账号")
        ref = reference.strip()
        if ref.startswith(("https://", "http://")):
            parsed = urlparse(ref)
            if parsed.hostname not in {"t.me", "telegram.me"}:
                raise ValueError("仅支持 Telegram 频道链接")
            parts = parsed.path.strip("/").split("/")
            if parts[0] == "s" and len(parts) > 1:
                ref = parts[1]
            elif parts[0] == "c" and len(parts) > 1 and parts[1].isdigit():
                ref = int("-100" + parts[1])
            elif parts[0].startswith("+") or parts[0] == "joinchat":
                raise ValueError("请先用 Telegram 加入私有频道，再从已加入频道中选择")
            else:
                ref = parts[0]
        elif ref.lstrip("-").isdigit():
            ref = int(ref)
        # Resolve only; never join/import invitations automatically.
        entity = await self.client.get_entity(ref)
        if not isinstance(entity, types.Channel) or not entity.broadcast:
            raise ValueError("仅支持广播频道，不支持群聊或私聊")
        chat_id = utils.get_peer_id(entity)
        messages = await self.client.get_messages(entity, limit=1)
        latest = messages[0].id if messages else 0
        with self.state.conn:
            self.state.conn.execute("""INSERT INTO managed_channels
                (chat_id,title,username,access_hash,latest_id,start_time,created_at) VALUES (?,?,?,?,?,?,?)
                ON CONFLICT(chat_id) DO UPDATE SET title=excluded.title,username=excluded.username,
                    access_hash=excluded.access_hash,collecting=1,status='pending',error='',retry_at=0""",
                (chat_id, entity.title, entity.username, str(entity.access_hash), latest, start_time, int(time.time())))
            if entity.username:
                self.state.conn.execute("DELETE FROM channel_alias WHERE username=? AND chat_id!=?", (entity.username, chat_id))
                self.state.conn.execute("INSERT INTO channel_alias VALUES (?,?) ON CONFLICT(chat_id) DO UPDATE SET username=excluded.username",
                                        (chat_id, entity.username))
        self.state.audit("channel_add", str(chat_id))
        return chat_id

    def _active(self, chat_id):
        return self.state.conn.execute("SELECT * FROM managed_channels WHERE chat_id=? AND collecting=1", (chat_id,)).fetchone()

    def _ingest(self, message, channel):
        if not message.date or int(message.date.timestamp()) < channel["start_time"]:
            return
        text = (message.message or "").strip()
        if not text and not message.edit_date:
            return
        username = channel["username"]
        chat_id = channel["chat_id"]
        internal_id = utils.resolve_id(chat_id)[0]
        link = f"https://t.me/{username}/{message.id}" if username else f"https://t.me/c/{internal_id}/{message.id}"
        msg = NormalizedMessage(message.id, chat_id, text, int(message.date.timestamp()),
                                int(message.edit_date.timestamp()) if message.edit_date else None,
                                "mtproto", username, link)
        self.state.repo.upsert_message(msg, self.state.tokenizer.tokenize(text), commit=False)

    async def _new_or_edit(self, event):
        # Check persisted allow-list BEFORE retrieving any message/chat metadata.
        channel = self._active(event.chat_id)
        if not channel:
            return
        try:
            with self.state.conn:
                self._ingest(event.message, channel)
            # Live events never advance the contiguous incremental checkpoint.
        except Exception as exc:
            logger.warning("Live indexing failed for %s (%s)", event.chat_id, type(exc).__name__)

    async def _deleted(self, event):
        if not event.chat_id or not self._active(event.chat_id):
            return
        with self.state.conn:
            self.state.conn.executemany("""INSERT INTO message_controls(chat_id,message_id,deleted) VALUES (?,?,1)
                ON CONFLICT(chat_id,message_id) DO UPDATE SET deleted=1""",
                [(event.chat_id, message_id) for message_id in event.deleted_ids])

    async def _increment(self, channel):
        messages = [m async for m in self.client.iter_messages(channel["chat_id"], min_id=channel["latest_id"], reverse=True, limit=100)]
        active = self._active(channel["chat_id"])
        if not active or active["created_at"] != channel["created_at"]:
            return
        with self.state.conn:
            for message in messages:
                self._ingest(message, channel)
            checkpoint = max([channel["latest_id"], *[m.id for m in messages]])
            self.state.conn.execute("UPDATE managed_channels SET latest_id=?,last_sync=?,error='',status='syncing',retry_at=0 WHERE chat_id=?",
                                    (checkpoint, int(time.time()), channel["chat_id"]))

    async def _history(self, channel):
        messages = await self.client.get_messages(channel["chat_id"], offset_id=channel["history_before"] or 0, limit=100)
        active = self._active(channel["chat_id"])
        if not active or active["created_at"] != channel["created_at"]:
            return
        finished = not messages or len(messages) < 100
        with self.state.conn:
            for message in messages:
                if message.date and int(message.date.timestamp()) < channel["start_time"]:
                    finished = True
                self._ingest(message, channel)
            self.state.conn.execute("""UPDATE managed_channels SET history_before=?,history_done=?,processed=processed+?,
                last_sync=?,status=?,error='',retry_at=0 WHERE chat_id=?""",
                (min(m.id for m in messages) if messages else channel["history_before"], int(finished), len(messages),
                 int(time.time()), "watching" if finished else "backfilling", channel["chat_id"]))

    async def _reconcile(self, channel):
        """Bounded recent-window check; absence in a partial history page is NOT deletion."""
        chat_id = channel["chat_id"]
        rows = self.state.conn.execute("SELECT message_id FROM channel_messages WHERE chat_id=? ORDER BY message_id DESC LIMIT 100", (chat_id,)).fetchall()
        ids = [r[0] for r in rows]
        if ids:
            messages = await self.client.get_messages(chat_id, ids=ids)
            if not self._active(chat_id):
                return
            with self.state.conn:
                for message_id, message in zip(ids, messages):
                    if message is None:
                        self.state.conn.execute("""INSERT INTO message_controls(chat_id,message_id,deleted) VALUES (?,?,1)
                            ON CONFLICT(chat_id,message_id) DO UPDATE SET deleted=1""", (chat_id, message_id))
                    else:
                        self._ingest(message, channel)
        self.reconcile_at[chat_id] = time.time() + 1800

    async def _run(self):
        while True:
            try:
                if self.account_retry_at > time.time():
                    await asyncio.sleep(3)
                    continue
                if not self.state.store.get("telegram_api_hash"):
                    await asyncio.sleep(3)
                    continue
                if not self.account:
                    if self.login["phase"] in {"code", "password", "qr"}:
                        if time.time() > self.login.get("expires_at", 0) + 5:
                            self.phone = self.phone_hash = None
                            self.login = {"phase": "idle", "error": "登录流程已过期"}
                        await asyncio.sleep(3)
                        continue
                    async with self.auth_lock:
                        client = await self._client()
                        if await client.is_user_authorized():
                            await self._authorized()
                    await asyncio.sleep(3)
                    continue
                await self._client()
                for row in self.state.channels():
                    if not row["collecting"] or row["retry_at"] > time.time():
                        continue
                    chat_id = row["chat_id"]
                    try:
                        # Resolve/access hash after process restart and refresh names.
                        cached = self.entities.get(chat_id)
                        if cached and cached[1] > time.time():
                            entity = cached[0]
                        else:
                            if row["access_hash"]:
                                ref = types.InputPeerChannel(utils.resolve_id(chat_id)[0], int(row["access_hash"]))
                            elif row["username"]:
                                ref = row["username"]
                            else:
                                # Imported private channels must first be selected in the WebUI.
                                raise ValueError("频道需要重新验证")
                            entity = await self.client.get_entity(ref)
                            self.entities[chat_id] = (entity, time.time() + 1800)
                        if not isinstance(entity, types.Channel) or not entity.broadcast:
                            raise ChannelPrivateError(None)
                        if not self._active(chat_id):
                            continue
                        with self.state.conn:
                            self.state.conn.execute("UPDATE managed_channels SET title=?,username=? WHERE chat_id=?", (entity.title, entity.username, chat_id))
                        row = dict(self._active(chat_id))
                        await self._increment(row)
                        if not row["history_done"]:
                            await self._history(row)
                        elif self.reconcile_at.get(chat_id, 0) < time.time():
                            await self._reconcile(row)
                        if row["history_done"]:
                            with self.state.conn:
                                self.state.conn.execute("UPDATE managed_channels SET status='watching',retry_at=? WHERE chat_id=? AND collecting=1", (int(time.time()) + 30, chat_id))
                    except FloodWaitError as exc:
                        retry_at = int(time.time()) + exc.seconds + 1
                        self.account_retry_at = retry_at
                        with self.state.conn:
                            self.state.conn.execute("UPDATE managed_channels SET status='rate_limited',retry_at=?,error=? WHERE chat_id=?",
                                                    (retry_at, self.safe_error(exc), chat_id))
                        break
                    except (AuthKeyUnregisteredError, UserDeactivatedError):
                        raise
                    except ChannelPrivateError:
                        with self.state.conn:
                            self.state.conn.execute("UPDATE managed_channels SET collecting=0,status='forbidden',error='账号无权访问频道，请确认已加入后重试' WHERE chat_id=?", (chat_id,))
                    except Exception as exc:
                        with self.state.conn:
                            self.state.conn.execute("UPDATE managed_channels SET status='error',error=?,retry_at=? WHERE chat_id=?",
                                                    (self.safe_error(exc), int(time.time()) + 60, chat_id))
                await asyncio.sleep(3)
            except asyncio.CancelledError:
                raise
            except (AuthKeyUnregisteredError, UserDeactivatedError):
                self.account = None
                self.state.store.set("telegram_session", "")
                if self.client:
                    await self.client.disconnect()
                self.client = None
                self.login = {"phase": "idle", "error": "Telegram 授权已失效，请重新登录"}
                await asyncio.sleep(10)
            except FloodWaitError as exc:
                self.account_retry_at = int(time.time()) + exc.seconds + 1
                self.login["error"] = self.safe_error(exc)
            except Exception as exc:
                self.login["error"] = self.safe_error(exc)
                await asyncio.sleep(15)
