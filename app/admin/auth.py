from __future__ import annotations

import time

import bcrypt

from app.storage.repository import MessageRepository


class AdminAuthService:
    def __init__(
        self,
        repo: MessageRepository,
        admin_ids: set[int],
        password_hash: str,
        session_ttl_seconds: int,
        max_failed_attempts: int,
        lockout_seconds: int,
    ) -> None:
        self.repo = repo
        self.admin_ids = admin_ids
        self.password_hash = password_hash.encode("utf-8")
        self.session_ttl_seconds = session_ttl_seconds
        self.max_failed_attempts = max_failed_attempts
        self.lockout_seconds = lockout_seconds

    def is_whitelisted(self, user_id: int) -> bool:
        return user_id in self.admin_ids

    def is_locked(self, user_id: int) -> bool:
        row = self.repo.conn.execute(
            "SELECT locked_until FROM admin_login_attempt WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row or row["locked_until"] is None:
            return False
        return int(row["locked_until"]) > int(time.time())

    def verify_password(self, password: str) -> bool:
        if not self.password_hash:
            return False
        try:
            return bcrypt.checkpw(password.encode("utf-8"), self.password_hash)
        except ValueError:
            return False

    def login(self, user_id: int, password: str) -> tuple[bool, str]:
        if not self.is_whitelisted(user_id):
            return False, "forbidden"
        if self.is_locked(user_id):
            return False, "locked"
        if not self.verify_password(password):
            self._record_failed_attempt(user_id)
            return False, "invalid_password"
        self._clear_failed_attempt(user_id)
        now = int(time.time())
        with self.repo.conn:
            self.repo.conn.execute(
                """
                INSERT INTO admin_session(user_id, expires_at, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET expires_at=excluded.expires_at
                """,
                (user_id, now + self.session_ttl_seconds, now),
            )
        return True, "ok"

    def logout(self, user_id: int) -> None:
        with self.repo.conn:
            self.repo.conn.execute("DELETE FROM admin_session WHERE user_id=?", (user_id,))

    def is_authenticated(self, user_id: int) -> bool:
        row = self.repo.conn.execute(
            "SELECT expires_at FROM admin_session WHERE user_id=?",
            (user_id,),
        ).fetchone()
        if not row:
            return False
        now = int(time.time())
        if int(row["expires_at"]) < now:
            self.logout(user_id)
            return False
        return True

    def _record_failed_attempt(self, user_id: int) -> None:
        row = self.repo.conn.execute(
            "SELECT failed_attempts FROM admin_login_attempt WHERE user_id=?",
            (user_id,),
        ).fetchone()
        failed_attempts = int(row["failed_attempts"]) + 1 if row else 1
        locked_until = None
        if failed_attempts >= self.max_failed_attempts:
            locked_until = int(time.time()) + self.lockout_seconds
            failed_attempts = 0
        with self.repo.conn:
            self.repo.conn.execute(
                """
                INSERT INTO admin_login_attempt(user_id, failed_attempts, locked_until)
                VALUES (?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    failed_attempts=excluded.failed_attempts,
                    locked_until=excluded.locked_until
                """,
                (user_id, failed_attempts, locked_until),
            )

    def _clear_failed_attempt(self, user_id: int) -> None:
        with self.repo.conn:
            self.repo.conn.execute("DELETE FROM admin_login_attempt WHERE user_id=?", (user_id,))

