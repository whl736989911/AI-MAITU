"""tests/unit/sharing/test_acl_rules.py"""

from __future__ import annotations

import pytest

from octop.infra.sharing import (
    AclEntry,
    allowed_resource_ids,
    can_access,
    can_write,
    impact_scope,
    requires_approval,
)


def _entry(
    *,
    visibility: str = "private",
    unit_key: str | None = None,
    owner_user_id: int | None = 7,
    grants: tuple[tuple[str, str], ...] = (),
    permission: str = "read",
    resource_type: str = "agent",
    resource_id: str = "ag1",
    version: int = 1,
) -> AclEntry:
    return AclEntry(
        resource_type=resource_type,
        resource_id=resource_id,
        owner_user_id=owner_user_id,
        visibility=visibility,
        unit_key=unit_key,
        version=version,
        grants=grants,
        permission=permission,
    )


# --- impact_scope ---------------------------------------------------------


@pytest.mark.parametrize("before_visibility", ["private", "unit"])
def test_impact_scope_widening_to_public_is_org(before_visibility: str) -> None:
    before = _entry(visibility=before_visibility, unit_key="sales")
    assert impact_scope(before, _entry(visibility="public")) == "org"


def test_impact_scope_already_public_is_self() -> None:
    assert impact_scope(_entry(visibility="public"), _entry(visibility="public")) == "self"


def test_impact_scope_private_to_unit_is_unit() -> None:
    before = _entry(unit_key="sales")
    after = _entry(visibility="unit", unit_key="sales")
    assert impact_scope(before, after) == "unit"


@pytest.mark.parametrize("grant", [("unit", "sales"), ("role", "user")])
def test_impact_scope_new_unit_or_role_grant_is_unit(grant: tuple[str, str]) -> None:
    before = _entry(grants=(("user", "9"),))
    after = _entry(grants=(("user", "9"), grant))
    assert impact_scope(before, after) == "unit"


def test_impact_scope_extra_user_grant_is_self() -> None:
    assert impact_scope(_entry(), _entry(grants=(("user", "9"),))) == "self"


def test_impact_scope_narrowing_is_self() -> None:
    before = _entry(visibility="unit", unit_key="sales", grants=(("unit", "support"),))
    assert impact_scope(before, _entry(unit_key="sales")) == "self"


@pytest.mark.parametrize(
    ("before", "after", "expected"),
    [
        (_entry(), _entry(visibility="public"), True),
        (_entry(visibility="unit", unit_key="sales"), _entry(visibility="public"), True),
        (_entry(unit_key="sales"), _entry(visibility="unit", unit_key="sales"), False),
        (_entry(), _entry(grants=(("unit", "sales"),)), False),
        (_entry(), _entry(grants=(("role", "user"),)), False),
        (_entry(), _entry(), False),
        (_entry(visibility="public"), _entry(), False),
    ],
)
def test_requires_approval_only_for_org(before: AclEntry, after: AclEntry, expected: bool) -> None:
    assert requires_approval(before, after) is expected


# --- can_access -----------------------------------------------------------


def test_can_access_rule_1_admin_bypasses_private_resources() -> None:
    entry = _entry(visibility="private", owner_user_id=7)
    assert can_access(entry, user_id=99, role="admin", unit_keys=()) is True


def test_can_access_rule_2_owner_keeps_own_private_resource() -> None:
    entry = _entry(visibility="private", owner_user_id=7, unit_key="sales")
    assert can_access(entry, user_id=7, role="user", unit_keys=("support",)) is True


def test_can_access_rule_3_public_reaches_every_signed_in_user() -> None:
    entry = _entry(visibility="public", owner_user_id=7, unit_key=None)
    assert can_access(entry, user_id=99, role="user", unit_keys=("support",)) is True


def test_can_access_rule_4_unit_members_only() -> None:
    entry = _entry(visibility="unit", unit_key="sales", owner_user_id=7)
    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is True
    assert can_access(entry, user_id=99, role="user", unit_keys=("support",)) is False
    assert can_access(entry, user_id=99, role="user", unit_keys=()) is False


def test_can_access_rule_4_needs_unit_visibility_not_just_a_unit_key() -> None:
    entry = _entry(visibility="private", unit_key="sales", owner_user_id=7)
    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is False


@pytest.mark.parametrize(
    ("grants", "user_id", "role", "unit_keys"),
    [
        ((("user", "99"),), 99, "user", ("support",)),
        ((("unit", "sales"),), 99, "user", ("sales",)),
        ((("role", "user"),), 99, "user", ()),
        ((("role", "user"),), 99, "user", ("support",)),
    ],
)
def test_can_access_rule_5_grants_widen(
    grants: tuple[tuple[str, str], ...],
    user_id: int,
    role: str,
    unit_keys: tuple[str, ...],
) -> None:
    entry = _entry(owner_user_id=7, grants=grants)
    assert can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys) is True


def test_can_access_rule_5_grants_for_other_subjects_do_not_leak() -> None:
    entry = _entry(
        owner_user_id=7,
        grants=(("user", "8"), ("unit", "sales"), ("role", "admin")),
    )
    assert can_access(entry, user_id=99, role="user", unit_keys=("support",)) is False


def test_can_access_rule_6_defaults_to_denied() -> None:
    entry = _entry(visibility="private", owner_user_id=7)
    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is False


def test_can_access_unit_scope_uses_the_snapshot_not_the_owners_new_unit() -> None:
    """Owner moved from sales to support: the share stays with sales."""
    entry = _entry(visibility="unit", unit_key="sales", owner_user_id=7)

    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is True
    assert can_access(entry, user_id=99, role="user", unit_keys=("support",)) is False


def test_can_access_unit_entry_without_unit_key_is_owner_only() -> None:
    """A deleted unit (``ON DELETE SET NULL``) must not match unassigned users."""
    entry = _entry(visibility="unit", unit_key=None, owner_user_id=7)

    assert can_access(entry, user_id=99, role="user", unit_keys=()) is False
    assert can_access(entry, user_id=7, role="user", unit_keys=()) is True


def test_can_access_system_owned_private_row_is_admin_only() -> None:
    """No owner to match: a private system row must not leak to ordinary users."""
    entry = _entry(visibility="private", owner_user_id=None)

    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is False
    assert can_access(entry, user_id=99, role="admin", unit_keys=()) is True


def test_can_access_system_owned_public_row_reaches_users() -> None:
    entry = _entry(visibility="public", owner_user_id=None)

    assert can_access(entry, user_id=99, role="user", unit_keys=()) is True


# --- allowed_resource_ids -------------------------------------------------


def test_allowed_resource_ids_admin_reaches_every_entry_including_private_ones() -> None:
    entries = [
        _entry(resource_id="r1", owner_user_id=1),
        _entry(resource_id="r2", owner_user_id=2),
    ]

    allowed = allowed_resource_ids(entries, user_id=99, role="admin", unit_keys=())

    assert allowed == {"r1", "r2"}


def test_allowed_resource_ids_keeps_the_actors_own_entry_only() -> None:
    entries = [
        _entry(resource_id="mine", owner_user_id=7),
        _entry(resource_id="theirs", owner_user_id=8),
        _entry(resource_id="system", owner_user_id=None),
    ]

    allowed = allowed_resource_ids(entries, user_id=7, role="user", unit_keys=("sales",))

    assert allowed == {"mine"}


def test_allowed_resource_ids_unit_entry_needs_the_same_unit() -> None:
    entries = [_entry(resource_id="r1", owner_user_id=7, visibility="unit", unit_key="sales")]

    same_unit = allowed_resource_ids(entries, user_id=99, role="user", unit_keys=("sales",))
    other_unit = allowed_resource_ids(entries, user_id=99, role="user", unit_keys=("support",))
    unassigned = allowed_resource_ids(entries, user_id=99, role="user", unit_keys=())

    assert same_unit == {"r1"}
    assert other_unit == set()
    assert unassigned == set()


def test_allowed_resource_ids_without_entries_is_never_open_access() -> None:
    assert allowed_resource_ids([], user_id=7, role="user", unit_keys=("sales",)) == set()


def test_allowed_resource_ids_grants_widen_a_private_entry() -> None:
    """Rule 5 must come from ``can_access``, not from a local re-implementation."""
    entries = [_entry(resource_id="r1", owner_user_id=7, grants=(("role", "user"),))]

    granted = allowed_resource_ids(entries, user_id=99, role="user", unit_keys=())
    other_role = allowed_resource_ids(entries, user_id=99, role="unit_admin", unit_keys=())

    assert granted == {"r1"}
    assert other_role == set()


def test_allowed_resource_ids_public_entry_reaches_a_user_without_a_unit() -> None:
    entries = [_entry(resource_id="r1", owner_user_id=7, visibility="public")]

    assert allowed_resource_ids(entries, user_id=99, role="user", unit_keys=()) == {"r1"}


# --- unit scope inherits, and write is the entry's other half ---------------


def test_can_access_unit_scope_reaches_a_sub_department() -> None:
    """A share with ``sales`` must reach ``sales-cn``, whose member's chain holds it."""
    entry = _entry(visibility="unit", unit_key="sales", owner_user_id=7)

    assert can_access(entry, user_id=99, role="user", unit_keys=("sales-cn", "sales")) is True


def test_can_access_unit_scope_does_not_reach_the_parent_unit() -> None:
    """The chain holds a viewer's ancestors: a grant below them never widens up."""
    entry = _entry(visibility="unit", unit_key="sales-cn", owner_user_id=7)

    assert can_access(entry, user_id=99, role="user", unit_keys=("sales",)) is False


def test_can_access_unit_grant_reaches_a_sub_department() -> None:
    entry = _entry(owner_user_id=7, grants=(("unit", "sales"),))

    assert can_access(entry, user_id=99, role="user", unit_keys=("sales-cn", "sales")) is True


def test_can_access_read_level_entry_is_unchanged_reach() -> None:
    """The default level decides nothing about reaching: that is ``can_access``."""
    entry = _entry(visibility="public", owner_user_id=7, permission="read")

    assert can_access(entry, user_id=99, role="user", unit_keys=()) is True


def test_can_write_owner_and_admin_always_write() -> None:
    private = _entry(visibility="private", owner_user_id=7, permission="read")

    assert can_write(private, user_id=7, role="user", unit_keys=()) is True
    assert can_write(private, user_id=99, role="admin", unit_keys=()) is True


def test_can_write_read_entry_reaches_without_maintaining() -> None:
    """A share a viewer can reach but the entry pins to ``read`` is not writable."""
    entry = _entry(visibility="public", owner_user_id=7, permission="read")

    assert can_access(entry, user_id=99, role="user", unit_keys=()) is True
    assert can_write(entry, user_id=99, role="user", unit_keys=()) is False


@pytest.mark.parametrize(
    ("shape", "unit_keys"),
    [
        ({"visibility": "public"}, ()),
        ({"visibility": "unit", "unit_key": "sales"}, ("sales-cn", "sales")),
        ({"grants": (("user", "99"),)}, ()),
        ({"grants": (("unit", "sales"),)}, ("sales",)),
        ({"grants": (("role", "user"),)}, ()),
    ],
)
def test_can_write_write_entry_maintains_through_every_rule(
    shape: dict[str, object], unit_keys: tuple[str, ...]
) -> None:
    """``write`` extends exactly the reach ``can_access`` already granted."""
    entry = _entry(owner_user_id=7, permission="write", **shape)  # type: ignore[arg-type]

    assert can_access(entry, user_id=99, role="user", unit_keys=unit_keys) is True
    assert can_write(entry, user_id=99, role="user", unit_keys=unit_keys) is True


def test_can_write_write_entry_does_not_widen_who_is_reached() -> None:
    """A ``write`` row is still scoped: someone it does not reach cannot write either."""
    entry = _entry(visibility="unit", unit_key="sales", owner_user_id=7, permission="write")

    assert can_write(entry, user_id=99, role="user", unit_keys=("eng",)) is False


def test_impact_scope_public_row_gaining_write_is_org() -> None:
    """Everyone could already read it; handing everyone write reaches the whole org."""
    before = _entry(visibility="public", owner_user_id=7)
    after = _entry(visibility="public", owner_user_id=7, permission="write")

    assert impact_scope(before, after) == "org"
    assert requires_approval(before, after) is True


def test_impact_scope_public_write_row_that_stays_write_is_self() -> None:
    before = _entry(visibility="public", owner_user_id=7, permission="write")
    after = _entry(visibility="public", owner_user_id=7, permission="write")

    assert impact_scope(before, after) == "self"


def test_impact_scope_narrowing_a_public_write_row_back_to_read_is_self() -> None:
    before = _entry(visibility="public", owner_user_id=7, permission="write")
    after = _entry(visibility="public", owner_user_id=7)

    assert impact_scope(before, after) == "self"
