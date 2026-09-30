"""Unit tests for the module permission catalog."""

from __future__ import annotations

from dataclasses import dataclass

import pytest
from harness_gateway.channels import SUPPORTED_CHANNEL_KINDS

from octop.infra.users.permissions import (
    ALL_PERMISSION_KEYS,
    BASELINE_PERMISSIONS,
    CHANNEL_PERMISSION_KEYS,
    PERMISSIONS,
    channel_permission_key,
    effective_permissions,
    user_has_permission,
    validate_permission_keys,
)


@dataclass
class _FakeUser:
    is_admin: bool
    permissions: list[str] | None = None


def test_catalog_has_no_empty_keys_and_no_deferred_dead_keys() -> None:
    assert PERMISSIONS
    for key in PERMISSIONS:
        assert key == PERMISSIONS[key].key
        assert PERMISSIONS[key].label_zh and PERMISSIONS[key].label_en
    # Design §2.2 catalogs ACP together with the other remote capabilities; the
    # old assertion that it was *absent* guarded the opposite (a key no route
    # checked). It is not a dead key any more: it carries a label, a category and
    # a grant surface like every other one, and stage 4 wires it to the routes.
    assert "acp" in PERMISSIONS
    assert PERMISSIONS["acp"].category == "control"


def test_functional_modules_and_channel_types_are_catalogued() -> None:
    """The keys design §2.2 names, generated channel types included."""
    for key in ("mbti", "experts", "teams", "features", "acp", "channel_feishu"):
        assert key in PERMISSIONS
    # experts and features are two independent keys, never one "agents" key.
    assert "agents" not in PERMISSIONS
    # One key per channel kind the gateway supports, and the catalog follows the
    # gateway's list rather than a second hand-kept one (design §4.5: 通道类型目录
    # 应集中维护).
    assert set(CHANNEL_PERMISSION_KEYS) == {f"channel_{kind}" for kind in SUPPORTED_CHANNEL_KINDS}
    for kind in SUPPORTED_CHANNEL_KINDS:
        key = channel_permission_key(kind)
        assert PERMISSIONS[key].dynamic is True
        # Baseline like ``channels`` itself: every kind is usable today, so a new
        # account keeps them and the v30 migration hands them to the old ones.
        assert key in BASELINE_PERMISSIONS
    # The functional modules keep the same pre-permission behaviour, while ACP
    # does not: before the catalog its entry was open to nobody but an admin.
    for key in ("mbti", "experts", "features"):
        assert key in BASELINE_PERMISSIONS
    assert "teams" in PERMISSIONS
    assert PERMISSIONS["teams"].category == "settings"
    assert "teams" not in BASELINE_PERMISSIONS
    assert "acp" not in BASELINE_PERMISSIONS


def test_user_has_permission_admin_bypass() -> None:
    admin = _FakeUser(is_admin=True, permissions=[])
    for key in ALL_PERMISSION_KEYS:
        assert user_has_permission(admin, key) is True


def test_user_has_permission_normal_hit_and_miss() -> None:
    u = _FakeUser(is_admin=False, permissions=["browser", "users"])
    assert user_has_permission(u, "browser") is True
    assert user_has_permission(u, "providers") is False
    assert user_has_permission(u, "unknown_key") is False


def test_baseline_is_settings_group_except_new_teams_key() -> None:
    assert {
        key for key, p in PERMISSIONS.items() if p.category == "settings"
    } == BASELINE_PERMISSIONS | {"teams"}
    assert BASELINE_PERMISSIONS
    assert all(PERMISSIONS[k].category == "settings" for k in BASELINE_PERMISSIONS)


def test_categories_match_nav_groups() -> None:
    """Every key sits in the group whose picker section it belongs to.

    ``settings`` is the functional-module group and stays the one
    :data:`BASELINE_PERMISSIONS` pre-checks for a new account: design §2.2's
    functional modules (MBTI / experts / features) and the channel types join the
    four that were already there, because every signed-in account could reach
    them before they were catalogued. ``control`` gained ``acp`` — a remote
    capability, not a baseline: before the catalog its entry was admin-only.
    """
    allowed = {"settings", "control", "admin"}
    for p in PERMISSIONS.values():
        assert p.category in allowed
    settings = {k for k, p in PERMISSIONS.items() if p.category == "settings"}
    assert settings == {
        "channels",
        "connectors",
        "skill_packages",
        "knowledge_bases",
        "mbti",
        "experts",
        "teams",
        "features",
        *CHANNEL_PERMISSION_KEYS,
    }
    assert {k for k, p in PERMISSIONS.items() if p.category == "control"} == {
        "terminal",
        "browser",
        "desktop",
        "mobile",
        "acp",
    }
    assert PERMISSIONS["envs"].page == "advanced"
    assert PERMISSIONS["voice"].page == "models"
    assert PERMISSIONS["search"].page == "models"
    assert PERMISSIONS["knowledge_settings"].page == "advanced"
    assert PERMISSIONS["knowledge_settings"].category == "admin"
    assert "knowledge_settings" not in BASELINE_PERMISSIONS
    assert PERMISSIONS["users"].page == "users"
    assert PERMISSIONS["plugins"].page == "plugins"
    assert not PERMISSIONS["plugins"].extra_tabs
    assert "agents" not in PERMISSIONS


def test_validate_permission_keys_rejects_unknown() -> None:
    assert validate_permission_keys(["browser", "users"]) == ["browser", "users"]
    with pytest.raises(ValueError):
        validate_permission_keys(["browser", "not_a_real_key"])


def test_effective_permissions_admin_gets_catalog() -> None:
    admin = _FakeUser(is_admin=True, permissions=[])
    assert effective_permissions(admin) == sorted(ALL_PERMISSION_KEYS)
