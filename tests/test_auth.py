import sqlite3
from pathlib import Path

import pytest

from bid_assistant.auth import MAX_FAILED_ATTEMPTS, UserStore


ADMIN_PASSWORD = "SecurePass2026!"
USER_PASSWORD = "ProjectPass2026!"


def _create_admin(store: UserStore) -> dict:
    return store.create_user("admin", "系统管理员", ADMIN_PASSWORD, role="admin")


def test_first_account_must_be_admin_and_password_is_only_hashed(tmp_path: Path) -> None:
    database = tmp_path / "security" / "auth.db"
    store = UserStore(database)

    with pytest.raises(ValueError, match="首个账号必须是管理员"):
        store.create_user("member", "普通账号", USER_PASSWORD)

    admin = _create_admin(store)
    with sqlite3.connect(database) as connection:
        stored_hash = connection.execute(
            "SELECT password_hash FROM users WHERE id=?", (admin["id"],)
        ).fetchone()[0]

    assert stored_hash.startswith("scrypt$")
    assert ADMIN_PASSWORD not in stored_hash
    assert "password_hash" not in admin


def test_only_active_admin_can_create_and_manage_accounts(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "auth.db")
    admin = _create_admin(store)
    member = store.create_user(
        "member",
        "项目成员",
        USER_PASSWORD,
        actor=admin,
        must_change_password=True,
    )

    with pytest.raises(ValueError, match="只有有效管理员"):
        store.create_user("second", "第二账号", "SecondPass2026!", actor=member)
    with pytest.raises(ValueError, match="只有有效管理员"):
        store.reset_password(member["id"], "ResetPass2026!", actor=member)
    with pytest.raises(ValueError, match="只有有效管理员"):
        store.set_active(member["id"], False, actor=member)


def test_login_failures_lock_account_and_success_resets_counter(tmp_path: Path) -> None:
    database = tmp_path / "auth.db"
    store = UserStore(database)
    admin = _create_admin(store)

    for _ in range(MAX_FAILED_ATTEMPTS - 1):
        result = store.authenticate("ADMIN", "wrong-password")
        assert result["ok"] is False
        assert "用户名或密码错误" in result["message"]

    locked = store.authenticate("admin", "wrong-password")
    assert locked["ok"] is False
    assert "已锁定" in locked["message"]
    assert store.authenticate("admin", ADMIN_PASSWORD)["ok"] is False

    with sqlite3.connect(database) as connection:
        connection.execute(
            "UPDATE users SET locked_until='2000-01-01T00:00:00+00:00' WHERE id=?",
            (admin["id"],),
        )

    authenticated = store.authenticate("admin", ADMIN_PASSWORD)
    assert authenticated["ok"] is True
    assert authenticated["user"]["failed_attempts"] == 0
    assert authenticated["user"]["locked_until"] is None


def test_password_change_and_admin_reset_require_a_new_password(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "auth.db")
    admin = _create_admin(store)
    member = store.create_user("member", "项目成员", USER_PASSWORD, actor=admin)

    with pytest.raises(ValueError, match="当前密码不正确"):
        store.change_password(member["id"], "incorrect", "UpdatedPass2026!")

    store.change_password(member["id"], USER_PASSWORD, "UpdatedPass2026!")
    assert store.authenticate("member", USER_PASSWORD)["ok"] is False
    assert store.authenticate("member", "UpdatedPass2026!")["ok"] is True

    store.reset_password(member["id"], "TemporaryPass2026!", actor=admin)
    reset_user = store.authenticate("member", "TemporaryPass2026!")["user"]
    assert reset_user["must_change_password"] == 1

    store.change_password(member["id"], "TemporaryPass2026!", "FinalSecurePass2026!")
    assert store.get_user(member["id"])["must_change_password"] == 0


def test_account_activation_and_last_admin_protection(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "auth.db")
    admin = _create_admin(store)
    member = store.create_user("member", "项目成员", USER_PASSWORD, actor=admin)

    store.set_active(member["id"], False, actor=admin)
    assert store.authenticate("member", USER_PASSWORD)["ok"] is False
    assert "已停用" in store.authenticate("member", USER_PASSWORD)["message"]

    store.set_active(member["id"], True, actor=admin)
    assert store.authenticate("member", USER_PASSWORD)["ok"] is True

    with pytest.raises(ValueError, match="最后一个有效管理员"):
        store.set_active(admin["id"], False, actor=admin)


def test_security_events_are_audited_without_passwords(tmp_path: Path) -> None:
    store = UserStore(tmp_path / "auth.db")
    admin = _create_admin(store)
    member = store.create_user("member", "项目成员", USER_PASSWORD, actor=admin)
    store.reset_password(member["id"], "TemporaryPass2026!", actor=admin)

    events = store.list_audit_events()
    serialized = str(events)

    assert any(item["event"] == "user_created" for item in events)
    assert any(item["event"] == "password_reset" for item in events)
    assert USER_PASSWORD not in serialized
    assert "TemporaryPass2026!" not in serialized
