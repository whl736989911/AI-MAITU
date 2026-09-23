# tests/unit/db/test_agent_is_shared.py
from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.agents.kinds import KIND_FEATURE
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.sharing import AclEntry


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def test_set_shared_publishes_through_the_acl(db):
    UserRepo(db).create(username="u1", password_hash="h", role="user")
    UserRepo(db).create(username="u2", password_hash="h", role="user")
    repo = AgentRepo(db)
    acl = ResourceAclRepo(db)
    repo.create(agent_id="a1", user_id=1, name="mine")
    repo.create(agent_id="a2", user_id=2, name="theirs")

    repo.set_shared("a2", True)

    assert [r.agent_id for r in repo.list_visible(1, exclude_user_id=1)] == ["a2"]
    assert repo.public_agent_ids(["a2"]) == {"a2"}
    entry = acl.get("agent", "a2")
    assert entry is not None and entry.visibility == "public"

    repo.set_shared("a2", False)

    assert repo.list_visible(1, exclude_user_id=1) == []
    assert repo.public_agent_ids(["a2"]) == set()
    entry = acl.get("agent", "a2")
    assert entry is not None and entry.visibility == "private"


def test_list_visible_is_the_access_rule_both_ways(db):
    """``list_visible`` answers "which agents may this account use" — the same
    question ``sharing.can_access`` answers one row at a time — so the two agree
    *both* ways: everything listed is usable, and everything usable is listed.

    The published set is a subset of it (``public`` is one rule among several), and
    a directed grant, a unit share and a role share are the rest. That is the
    difference between this method and ``public_agent_ids``, which still answers
    "published" for the display flag: an endpoint that wants the published set must
    ask for it by name.
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
    # A feature's own agent: its access entry is filed under the *feature* id
    # (``resource_type='feature'``), one derivation from ``feat-<feature_id>``.
    repo.create(agent_id="feat-weekly", user_id=peer, name="Weekly", kind=KIND_FEATURE)
    repo.set_shared("published", True)
    repo.set_shared("disabled", True)
    repo.set_enabled("disabled", False)
    acl.upsert(AclEntry("agent", "unit-shared", peer, "unit", "sales", 1))
    acl.upsert(AclEntry("agent", "granted", peer, "private", None, 1, (("user", str(outsider)),)))
    acl.upsert(AclEntry("feature", "weekly", peer, "private", None, 1, (("user", str(outsider)),)))

    def listed(user_id: int) -> set[str]:
        return {row.agent_id for row in repo.list_visible(user_id)}

    # Spelled out per viewer rather than re-derived here: what the list shows *is*
    # the contract, and a test that rebuilt the union would only agree with itself.
    # ``disabled`` is absent everywhere — the disabled filter is the listing's own.
    assert listed(owner) == {"mine", "published", "unit-shared"}
    assert listed(peer) == {"published", "unit-shared", "granted", "feat-weekly"}
    # The outsider reaches the published agent, the one granted to them directly,
    # and the feature granted through the *feature* entry — nothing else.
    assert listed(outsider) == {"published", "granted", "feat-weekly"}
    assert listed(admin) == {"mine", "published", "unit-shared", "granted", "feat-weekly"}

    # "Published" is still its own question, and a feature entry can answer it too.
    assert repo.public_agent_ids() == {"published", "disabled"}
    acl.set_visibility("feature", "weekly", "public", owner_user_id=peer)
    assert repo.public_agent_ids(["feat-weekly"]) == {"feat-weekly"}
    assert repo.public_agent_ids(["feat-weekly", "mine"]) == {"feat-weekly"}


def test_deleting_a_feature_agent_takes_its_feature_entry(db):
    """The feature's second entry dies with the agent it names.

    Both entries are one resource (``feat-<feature_id>`` *is* the feature), so a
    row left behind would decide the next feature that reuses the id: its author
    would not own the row, and whoever the previous feature reached would keep
    reaching the new one.
    """
    user = UserRepo(db).create(username="solo", password_hash="h", role="user")
    repo = AgentRepo(db)
    acl = ResourceAclRepo(db)

    repo.create(agent_id="feat-gone", user_id=user, name="Gone", kind=KIND_FEATURE)
    acl.upsert(AclEntry("feature", "gone", user, "public", None, 1))
    repo.delete("feat-gone")

    assert acl.get("agent", "feat-gone") is None
    assert acl.get("feature", "gone") is None

    # An ordinary agent has no second entry, and deleting one still clears its own.
    repo.create(agent_id="plain", user_id=user, name="Plain")
    repo.delete("plain")
    assert acl.get("agent", "plain") is None
