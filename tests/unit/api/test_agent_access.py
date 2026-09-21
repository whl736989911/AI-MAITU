"""Agent access decisions must come from ``resource_acl``.

A share applied through ``SharingService`` only ever lands in the ACL, so the
old "read the boolean off the agent row" reader produced "visible in the list,
403 on open" — and schema v21 removed that boolean entirely.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.api.common.agent import (
    assert_agent_access_row,
    assert_agent_owner,
    user_owns_agent,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import CHANGE_PENDING, ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.sharing import AclEntry
from octop.infra.sharing.service import SharingService


def _user(user_id: int, *, role: str = "user", org_unit: str | None = None) -> SimpleNamespace:
    return SimpleNamespace(id=user_id, role=role, org_unit=org_unit, is_admin=role == "admin")


@pytest.fixture
def world(tmp_path: Path) -> SimpleNamespace:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    units = OrgUnitRepo(pool)
    units.create(key="sales", label_zh="销售", label_en="Sales")
    units.create(key="eng", label_zh="研发", label_en="Engineering")
    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user")
    peer = users.create(username="peer", password_hash="h", role="user")
    admin = users.create(username="admin", password_hash="h", role="admin")
    repo = AgentRepo(pool)
    agent_id = repo.create(agent_id="AG1", user_id=owner, name="Bot")
    return SimpleNamespace(
        pool=pool,
        repo=repo,
        acl=ResourceAclRepo(pool),
        sharing=SharingService(pool),
        row=repo.get(agent_id),
        owner=owner,
        peer=peer,
        admin=admin,
    )


def _publish(world: SimpleNamespace, agent_id: str) -> None:
    """Share through the real pipeline (approval included)."""
    pending = world.sharing.apply_change(
        world.owner,
        "agent",
        agent_id,
        AclEntry(
            resource_type="agent",
            resource_id=agent_id,
            owner_user_id=world.owner,
            visibility="public",
            unit_key=None,
            version=0,
        ),
    )
    assert pending.status == CHANGE_PENDING
    world.sharing.approve(pending.change_id, world.admin)


def test_published_agent_is_reachable_to_a_non_owner(
    world: SimpleNamespace,
) -> None:
    row = world.row
    assert row is not None

    _publish(world, row.agent_id)

    entry = world.acl.get("agent", row.agent_id)
    assert entry is not None and entry.visibility == "public"
    assert_agent_access_row(row, _user(world.peer), acl=world.acl)


def test_private_agent_is_forbidden_to_a_non_owner(world: SimpleNamespace) -> None:
    row = world.row
    assert row is not None

    with pytest.raises(OctopError) as ei:
        assert_agent_access_row(row, _user(world.peer), acl=world.acl)
    assert ei.value.code == ErrorCode.FORBIDDEN


def test_owner_and_admin_bypass_keep_working(world: SimpleNamespace) -> None:
    row = world.row
    assert row is not None

    assert_agent_access_row(row, _user(world.owner), acl=world.acl)
    assert_agent_access_row(row, _user(world.admin, role="admin"), acl=world.acl)


def test_unit_share_reaches_the_unit(world: SimpleNamespace) -> None:
    row = world.row
    assert row is not None

    world.sharing.apply_change(
        world.owner,
        "agent",
        row.agent_id,
        AclEntry(
            resource_type="agent",
            resource_id=row.agent_id,
            owner_user_id=world.owner,
            visibility="unit",
            unit_key="sales",
            version=0,
        ),
    )

    assert_agent_access_row(row, _user(world.peer, org_unit="sales"), acl=world.acl)
    with pytest.raises(OctopError):
        assert_agent_access_row(row, _user(world.peer, org_unit="eng"), acl=world.acl)


def test_agent_without_an_acl_row_is_denied_even_to_its_owner(world: SimpleNamespace) -> None:
    row = world.row
    assert row is not None
    world.acl.delete("agent", row.agent_id)

    with pytest.raises(OctopError):
        assert_agent_access_row(row, _user(world.owner), acl=world.acl)


def test_owner_assert_still_rejects_a_shared_non_owner(world: SimpleNamespace) -> None:
    row = world.row
    assert row is not None
    _publish(world, row.agent_id)

    with pytest.raises(OctopError):
        assert_agent_owner(row, _user(world.peer))


def test_display_flag_reads_the_acl(world: SimpleNamespace) -> None:
    """``public_agent_ids`` (the list/get display source) follows the ACL."""
    row = world.row
    assert row is not None
    _publish(world, row.agent_id)

    assert world.repo.public_agent_ids([row.agent_id]) == {row.agent_id}


def test_user_owns_agent_only_for_matching_owner() -> None:
    assert user_owns_agent(SimpleNamespace(user_id=1), SimpleNamespace(id=1)) is True
    assert user_owns_agent(SimpleNamespace(user_id=1), SimpleNamespace(id=2)) is False
    assert user_owns_agent(SimpleNamespace(user_id=None), SimpleNamespace(id=2)) is False
