"""Schema v30: the module keys added by the permission catalog are backfilled.

Design §6: 新增权限没有历史数据时，按升级前行为进行安全回填，再由管理员调整 — an
account that could reach MBTI, the expert catalog and the features pages before
they were catalogued keeps them; a channel manager keeps every channel type.
"""

from __future__ import annotations

import json
from pathlib import Path

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.users.permissions import CHANNEL_PERMISSION_KEYS


def _seed_user(pool: SqlitePool, username: str, *, role: str, permissions: list[str]) -> int:
    with pool.transaction() as conn:
        cur = conn.execute(
            "INSERT INTO users(username, password_hash, role, display_name, created_at, "
            "permissions, denied_permissions) VALUES (?, 'h', ?, ?, 0, ?, '[]')",
            (username, role, username, json.dumps(permissions)),
        )
        return int(cur.lastrowid if cur.lastrowid is not None else 0)


def _permissions(pool: SqlitePool, username: str) -> set[str]:
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT permissions FROM users WHERE username = ?", (username,)
        ).fetchone()
    return set(json.loads(str(row["permissions"])))


def _upgrade_from_v29(pool: SqlitePool) -> None:
    """Rewind the watermark and run the chain again — how an upgrade looks."""
    with pool.transaction() as conn:
        conn.execute("UPDATE _schema_version SET version = 29")
    run_migrations(pool)


def test_backfill_grants_the_new_modules_and_keeps_them_revocable(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    _seed_user(pool, "employee", role="user", permissions=["browser"])
    _seed_user(pool, "channel-operator", role="user", permissions=["channels"])
    _seed_user(pool, "admin", role="admin", permissions=[])

    _upgrade_from_v29(pool)

    # Reachable by every signed-in account before the keys existed, so they are
    # granted explicitly — which is also what lets an admin revoke one.
    assert {"mbti", "experts", "features"} <= _permissions(pool, "employee")
    assert "browser" in _permissions(pool, "employee")

    # Channel management was gated by ``channels`` alone.
    held = _permissions(pool, "channel-operator")
    assert set(CHANNEL_PERMISSION_KEYS) <= held
    # A key nobody held is not invented: this account never had channels.
    assert not (set(CHANNEL_PERMISSION_KEYS) & _permissions(pool, "employee"))

    # A system administrator needs no stored key: its access is the catalog-wide
    # bypass, and a stored row would outlive a later demotion.
    assert _permissions(pool, "admin") == set()
    # ``acp`` was admin-only before the catalog; nobody gains it.
    assert "acp" not in _permissions(pool, "channel-operator")


def test_backfill_grants_channel_types_to_departments_that_had_channels(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    units = OrgUnitRepo(pool)
    units.create(key="ops", label_zh="运维", label_en="Ops")
    units.create(key="sales", label_zh="销售", label_en="Sales")
    units.set_grants("ops", ["channels"])

    _upgrade_from_v29(pool)

    assert set(CHANNEL_PERMISSION_KEYS) <= set(units.list_unit_permissions("ops"))
    assert "channels" in units.list_unit_permissions("ops")
    # A department that never had the module keeps its own set untouched.
    assert units.list_unit_permissions("sales") == []


def test_backfill_leaves_denies_and_hand_edited_rows_alone(tmp_path: Path) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    # Denied keys stay denied: a deny is an explicit decision, and writing the
    # grant underneath it would report a permission the account does not have.
    denied_id = _seed_user(pool, "denied", role="user", permissions=["channels"])
    with pool.transaction() as conn:
        conn.execute(
            "UPDATE users SET denied_permissions = ? WHERE id = ?",
            (json.dumps(["experts", "channels"]), denied_id),
        )
    _seed_user(pool, "broken", role="user", permissions=[])
    with pool.transaction() as conn:
        conn.execute("UPDATE users SET permissions = 'not json' WHERE username = 'broken'")

    _upgrade_from_v29(pool)

    after = _permissions(pool, "denied")
    # A denied key stays denied: writing the grant underneath it would report a
    # permission the account does not have.
    assert "experts" not in after
    # A denied ``channels`` means no channel access at all, so no type keys.
    assert not (set(CHANNEL_PERMISSION_KEYS) & after)
    # The keys the deny does not name are still backfilled.
    assert {"features", "mbti", "channels"} <= after
    # A hand-edited column is left exactly as it was rather than replaced with a
    # parsed-empty list.
    with pool.connect() as conn:
        row = conn.execute("SELECT permissions FROM users WHERE username = 'broken'").fetchone()
    assert str(row["permissions"]) == "not json"


def test_backfill_does_not_run_twice(tmp_path: Path) -> None:
    """The watermark is what keeps it historical: a later boot grants nothing."""
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    _seed_user(pool, "employee", role="user", permissions=[])
    _upgrade_from_v29(pool)
    assert "experts" in _permissions(pool, "employee")

    # An administrator revokes the key afterwards; the next boot must not hand it
    # back — the migration already ran.
    with pool.transaction() as conn:
        conn.execute("UPDATE users SET permissions = '[]' WHERE username = 'employee'")
    run_migrations(pool)
    assert _permissions(pool, "employee") == set()
