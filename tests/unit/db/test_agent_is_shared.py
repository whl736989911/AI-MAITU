# tests/unit/db/test_agent_is_shared.py
from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import AclEntry, can_access


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def test_set_shared_publishes_through_the_acl(db):
    run_migrations(db)  # or use fixture that already migrated
    UserRepo(db).create(username="u1", password_hash="h", role="user")
    UserRepo(db).create(username="u2", password_hash="h", role="user")
    repo = AgentRepo(db)
    acl = ResourceAclRepo(db)
    repo.create(agent_id="a1", user_id=1, name="mine")
    repo.create(agent_id="a2", user_id=2, name="theirs")

    repo.set_shared("a2", True)

    assert [r.agent_id for r in repo.list_shared(exclude_user_id=1)] == ["a2"]
    assert repo.public_agent_ids(["a2"]) == {"a2"}
    entry = acl.get("agent", "a2")
    assert entry is not None and entry.visibility == "public"

    repo.set_shared("a2", False)

    assert repo.list_shared(exclude_user_id=1) == []
    assert repo.public_agent_ids(["a2"]) == set()
    entry = acl.get("agent", "a2")
    assert entry is not None and entry.visibility == "private"


def test_list_shared_is_the_published_set_and_never_wider_than_the_rule(db):
    """``list_shared`` answers "which agents are published" — not "which may this
    user use" (``sharing.can_access``), so the two agree only one way: everything
    listed is accessible, not everything accessible is listed.

    That asymmetry is the endpoint's (``owned ∪ shared``), not this method's: see
    the docstring, which also records why the ``enabled`` filter cannot come from
    an access rule.
    """
    units = OrgUnitRepo(db)
    units.create(key="sales", label_zh="销售", label_en="Sales")
    units.create(key="eng", label_zh="研发", label_en="Engineering")
    users = UserRepo(db)
    owner = users.create(username="owner", password_hash="h", role="user", org_unit="sales")
    peer = users.create(username="peer", password_hash="h", role="user", org_unit="sales")
    outsider = users.create(username="outsider", password_hash="h", role="user", org_unit="eng")
    admin = users.create(username="admin", password_hash="h", role="admin")
    repo = AgentRepo(db)
    acl = ResourceAclRepo(db)

    repo.create(agent_id="mine", user_id=owner, name="Mine")
    repo.create(agent_id="published", user_id=peer, name="Published")
    repo.create(agent_id="disabled", user_id=peer, name="Disabled")
    repo.create(agent_id="unit-shared", user_id=peer, name="Unit")
    repo.create(agent_id="granted", user_id=peer, name="Granted")
    repo.set_shared("published", True)
    repo.set_shared("disabled", True)
    repo.set_enabled("disabled", False)
    acl.upsert(AclEntry("agent", "unit-shared", peer, "unit", "sales", 1))
    acl.upsert(AclEntry("agent", "granted", peer, "private", None, 1, (("user", str(outsider)),)))

    assert {row.agent_id for row in repo.list_shared()} == {"published"}
    assert {row.agent_id for row in repo.list_shared(exclude_user_id=peer)} == set()

    entries = {entry.resource_id: entry for entry in acl.list_for_type("agent")}
    assert set(entries) == {"mine", "published", "disabled", "unit-shared", "granted"}
    listed = {row.agent_id for row in repo.list_shared()}
    allowed_by_viewer: dict[str, set[str]] = {}
    for name, user_id in {
        "owner": owner,
        "peer": peer,
        "outsider": outsider,
        "admin": admin,
    }.items():
        role, unit_key = acl.scope_for_user(user_id)
        allowed_by_viewer[name] = {
            resource_id
            for resource_id, entry in entries.items()
            if can_access(entry, user_id=user_id, role=role, unit_key=unit_key)
        }

    for name, allowed in allowed_by_viewer.items():
        # Listed ⇒ usable: the listing never hands out an agent the rule denies.
        assert listed <= allowed, f"{name}: listed an agent the rule denies"

    cells = {
        ("published", "outsider"): True,
        ("disabled", "outsider"): True,
        ("unit-shared", "owner"): True,
        ("unit-shared", "outsider"): False,
        ("granted", "outsider"): True,
        ("mine", "owner"): True,
        ("mine", "outsider"): False,
    }
    for (agent_id, viewer), want in cells.items():
        got = agent_id in allowed_by_viewer[viewer]
        assert got is want, f"{agent_id}/{viewer}: expected {want}, got {got}"

    # The deliberate difference, pinned: usable but unpublished, so this method
    # leaves it out and an "owned ∪ shared" endpoint never shows it. That gap is
    # the endpoint's to close — not a reason to fold this method into the rule.
    assert "unit-shared" not in listed
    assert "unit-shared" in allowed_by_viewer["owner"]
