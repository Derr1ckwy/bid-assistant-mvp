from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sqlite3
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 128
MAX_FAILED_ATTEMPTS = 5
LOCKOUT_MINUTES = 10
_USERNAME_PATTERN = re.compile(r"[a-z0-9._-]{3,32}")
_ROLES = {"admin", "user"}
_COMMON_PASSWORDS = {
    "1234567890",
    "admin12345",
    "password123",
    "qwerty12345",
    "12345678a",
}
_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def normalize_username(value: str) -> str:
    return unicodedata.normalize("NFKC", value or "").strip().lower()


def password_policy_errors(password: str, *, username: str = "") -> list[str]:
    errors: list[str] = []
    if len(password) < MIN_PASSWORD_LENGTH:
        errors.append(f"密码至少需要 {MIN_PASSWORD_LENGTH} 个字符。")
    if len(password) > MAX_PASSWORD_LENGTH:
        errors.append(f"密码不能超过 {MAX_PASSWORD_LENGTH} 个字符。")
    if not any(character.isalpha() for character in password):
        errors.append("密码至少需要包含一个字母或汉字。")
    if not any(character.isdigit() for character in password):
        errors.append("密码至少需要包含一个数字。")
    normalized_password = unicodedata.normalize("NFKC", password).lower()
    normalized_username = normalize_username(username)
    if normalized_username and normalized_username in normalized_password:
        errors.append("密码不能包含完整用户名。")
    if normalized_password in _COMMON_PASSWORDS:
        errors.append("该密码过于常见，请更换。")
    return errors


def _encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode("ascii").rstrip("=")


def _decode(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        maxmem=64 * 1024 * 1024,
        dklen=_SCRYPT_DKLEN,
    )
    return f"scrypt${_SCRYPT_N}${_SCRYPT_R}${_SCRYPT_P}${_encode(salt)}${_encode(digest)}"


def _verify_password(password: str, encoded: str) -> bool:
    try:
        algorithm, n_value, r_value, p_value, salt_value, digest_value = encoded.split("$", 5)
        if algorithm != "scrypt":
            return False
        expected = _decode(digest_value)
        actual = hashlib.scrypt(
            password.encode("utf-8"),
            salt=_decode(salt_value),
            n=int(n_value),
            r=int(r_value),
            p=int(p_value),
            maxmem=64 * 1024 * 1024,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(actual, expected)


class UserStore:
    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.database_path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")
        return connection

    def _init_db(self) -> None:
        with self._connect() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY,
                    username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    display_name TEXT NOT NULL,
                    password_hash TEXT NOT NULL,
                    role TEXT NOT NULL,
                    active INTEGER NOT NULL DEFAULT 1,
                    must_change_password INTEGER NOT NULL DEFAULT 0,
                    failed_attempts INTEGER NOT NULL DEFAULT 0,
                    locked_until TEXT,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    last_login_at TEXT
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS audit_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at TEXT NOT NULL,
                    user_id TEXT,
                    username TEXT NOT NULL,
                    event TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '{}'
                )
                """
            )

    def has_users(self) -> bool:
        with self._connect() as connection:
            row = connection.execute("SELECT 1 FROM users LIMIT 1").fetchone()
        return row is not None

    @staticmethod
    def _public_user(row: sqlite3.Row | None) -> dict | None:
        if row is None:
            return None
        return {
            key: row[key]
            for key in (
                "id",
                "username",
                "display_name",
                "role",
                "active",
                "must_change_password",
                "created_at",
                "updated_at",
                "last_login_at",
                "locked_until",
                "failed_attempts",
            )
        }

    def get_user(self, user_id: str) -> dict | None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        return self._public_user(row)

    def get_user_by_username(self, username: str) -> dict | None:
        normalized = normalize_username(username)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()
        return self._public_user(row)

    def list_users(self) -> list[dict]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM users ORDER BY active DESC, role ASC, username ASC"
            ).fetchall()
        return [self._public_user(row) for row in rows]

    def _require_active_admin(self, actor: dict | None) -> dict:
        actor_id = str(actor.get("id", "")) if actor else ""
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (actor_id,)).fetchone()
        if row is None or not row["active"] or row["role"] != "admin":
            raise ValueError("只有有效管理员可以执行该操作。")
        return self._public_user(row)

    def create_user(
        self,
        username: str,
        display_name: str,
        password: str,
        *,
        role: str = "user",
        must_change_password: bool = False,
        actor: dict | None = None,
    ) -> dict:
        normalized = normalize_username(username)
        has_existing_users = self.has_users()
        if has_existing_users:
            actor = self._require_active_admin(actor)
        if not _USERNAME_PATTERN.fullmatch(normalized):
            raise ValueError("用户名需要使用 3-32 位小写字母、数字、点、短横线或下划线。")
        if role not in _ROLES:
            raise ValueError("账号角色无效。")
        if not has_existing_users and role != "admin":
            raise ValueError("首个账号必须是管理员。")
        errors = password_policy_errors(password, username=normalized)
        if errors:
            raise ValueError(" ".join(errors))
        name = unicodedata.normalize("NFKC", display_name or "").strip()[:60] or normalized
        timestamp = _now()
        user_id = f"user_{uuid4().hex[:16]}"
        try:
            with self._connect() as connection:
                connection.execute(
                    """
                    INSERT INTO users
                        (id, username, display_name, password_hash, role, active,
                         must_change_password, created_at, updated_at)
                    VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?)
                    """,
                    (
                        user_id,
                        normalized,
                        name,
                        _hash_password(password),
                        role,
                        int(must_change_password),
                        timestamp,
                        timestamp,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError("用户名已存在。") from exc
        user = self.get_user(user_id)
        self.record_event(
            "user_created",
            actor=actor or user,
            detail={"target_user_id": user_id, "target_username": normalized, "role": role},
        )
        return user

    def authenticate(self, username: str, password: str) -> dict:
        normalized = normalize_username(username)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE username=?", (normalized,)).fetchone()

        generic_failure = {"ok": False, "message": "用户名或密码错误。", "user": None}
        if row is None:
            _verify_password(password, _hash_password("invalid-password-123"))
            self.record_event("login_failed", username=normalized or "(empty)")
            return generic_failure
        if not row["active"]:
            self.record_event("login_disabled", user_id=row["id"], username=row["username"])
            return {"ok": False, "message": "账号已停用，请联系管理员。", "user": None}

        locked_until = None
        if row["locked_until"]:
            try:
                locked_until = datetime.fromisoformat(row["locked_until"])
            except ValueError:
                locked_until = None
        now = datetime.now(timezone.utc)
        if locked_until and locked_until > now:
            remaining = max(1, int((locked_until - now).total_seconds() // 60) + 1)
            self.record_event("login_locked", user_id=row["id"], username=row["username"])
            return {
                "ok": False,
                "message": f"账号已暂时锁定，请约 {remaining} 分钟后重试。",
                "user": None,
            }

        if not _verify_password(password, row["password_hash"]):
            failed_attempts = int(row["failed_attempts"]) + 1
            new_locked_until = None
            message = generic_failure["message"]
            if failed_attempts >= MAX_FAILED_ATTEMPTS:
                new_locked_until = (now + timedelta(minutes=LOCKOUT_MINUTES)).isoformat(timespec="seconds")
                message = f"连续登录失败，账号已锁定 {LOCKOUT_MINUTES} 分钟。"
            with self._connect() as connection:
                connection.execute(
                    "UPDATE users SET failed_attempts=?, locked_until=?, updated_at=? WHERE id=?",
                    (failed_attempts, new_locked_until, _now(), row["id"]),
                )
            self.record_event(
                "login_failed",
                user_id=row["id"],
                username=row["username"],
                detail={"failed_attempts": failed_attempts},
            )
            return {"ok": False, "message": message, "user": None}

        timestamp = _now()
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET failed_attempts=0, locked_until=NULL, last_login_at=?, updated_at=?
                WHERE id=?
                """,
                (timestamp, timestamp, row["id"]),
            )
        user = self.get_user(row["id"])
        self.record_event("login_success", actor=user)
        return {"ok": True, "message": "登录成功。", "user": user}

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None or not row["active"]:
            raise ValueError("账号不存在或已停用。")
        if not _verify_password(current_password, row["password_hash"]):
            self.record_event("password_change_failed", user_id=row["id"], username=row["username"])
            raise ValueError("当前密码不正确。")
        errors = password_policy_errors(new_password, username=row["username"])
        if errors:
            raise ValueError(" ".join(errors))
        if _verify_password(new_password, row["password_hash"]):
            raise ValueError("新密码不能与当前密码相同。")
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_hash=?, must_change_password=0, failed_attempts=0,
                    locked_until=NULL, updated_at=?
                WHERE id=?
                """,
                (_hash_password(new_password), _now(), user_id),
            )
        self.record_event("password_changed", actor=self.get_user(user_id))

    def reset_password(self, user_id: str, new_password: str, *, actor: dict) -> None:
        actor = self._require_active_admin(actor)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise ValueError("账号不存在。")
        errors = password_policy_errors(new_password, username=row["username"])
        if errors:
            raise ValueError(" ".join(errors))
        with self._connect() as connection:
            connection.execute(
                """
                UPDATE users
                SET password_hash=?, must_change_password=1, failed_attempts=0,
                    locked_until=NULL, updated_at=?
                WHERE id=?
                """,
                (_hash_password(new_password), _now(), user_id),
            )
        self.record_event(
            "password_reset",
            actor=actor,
            detail={"target_user_id": user_id, "target_username": row["username"]},
        )

    def set_active(self, user_id: str, active: bool, *, actor: dict) -> None:
        actor = self._require_active_admin(actor)
        with self._connect() as connection:
            row = connection.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
            if row is None:
                raise ValueError("账号不存在。")
            if not active and row["role"] == "admin":
                admin_count = connection.execute(
                    "SELECT COUNT(*) FROM users WHERE role='admin' AND active=1"
                ).fetchone()[0]
                if admin_count <= 1:
                    raise ValueError("不能停用最后一个有效管理员账号。")
            connection.execute(
                "UPDATE users SET active=?, updated_at=? WHERE id=?",
                (int(active), _now(), user_id),
            )
        self.record_event(
            "user_enabled" if active else "user_disabled",
            actor=actor,
            detail={"target_user_id": user_id, "target_username": row["username"]},
        )

    def record_event(
        self,
        event: str,
        *,
        actor: dict | None = None,
        user_id: str | None = None,
        username: str = "system",
        detail: dict | None = None,
    ) -> None:
        actor_id = actor.get("id") if actor else user_id
        actor_username = actor.get("username") if actor else username
        with self._connect() as connection:
            connection.execute(
                """
                INSERT INTO audit_events (created_at, user_id, username, event, detail)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    _now(),
                    actor_id,
                    actor_username or "system",
                    event[:80],
                    json.dumps(detail or {}, ensure_ascii=False, separators=(",", ":")),
                ),
            )

    def list_audit_events(self, limit: int = 100) -> list[dict]:
        safe_limit = min(max(int(limit), 1), 500)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT * FROM audit_events ORDER BY id DESC LIMIT ?", (safe_limit,)
            ).fetchall()
        result: list[dict] = []
        for row in rows:
            item = dict(row)
            try:
                item["detail"] = json.loads(item["detail"])
            except json.JSONDecodeError:
                item["detail"] = {}
            result.append(item)
        return result
