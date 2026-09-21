"""Connector runtime scope and knowledge-base runtime scope are one rule.

``ConnectorRepo.list_visible`` used to answer "may this user use it?" with its
own copy of the ACL rules (a SQL statement in ``ResourceAclRepo``) while the
knowledge-base runtime scope filtered ACL entries through ``sharing.can_access``.
Both agreed — by hand, which is the kind of agreement that lasts until the next
rule. The connector list now resolves through ``sharing.allowed_resource_ids``,
the function the composer, cron and the permission checks call too.

These tests pin that: for every access shape and every viewer, the connector
list, the entry-level knowledge-base check and the list-level knowledge-base
scope must return the same verdict. Changing one path alone fails here.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.connectors import ConnectorRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge.scope import may_read_knowledge_base
from octop.infra.sharing import AclEntry, allowed_resource_ids, can_access, user_scope

# Unit that the "deleted-unit" shape snapshots, deleted after seeding.
DELETED_UNIT = "legacy"


@dataclass(frozen=True)
class Shape:
    """One access shape, applied unchanged to a connector and a base."""

    name: str
    visibility: str = "private"
    unit_key: str | None = None
    grants: tuple[tuple[str, str], ...] = ()
    system_owned: bool = False


@dataclass(frozen=True)
class World:
    acl: ResourceAclRepo
    connectors: ConnectorRepo
    users: UserRepo
    viewers: dict[str, int]
    shapes: tuple[Shape, ...]

    def connector_id(self, shape: str) -> str:
        return f"CONN-{shape}"

    def kb_id(self, shape: str) -> str:
        return f"KB-{shape}"

    def entries(self, resource_type: str) -> dict[str, AclEntry]:
        return {e.resource_id: e for e in self.acl.list_for_type(resource_type)}

    def visible_connectors(self, user_id: int) -> set[str]:
        return {row.instance_id for row in self.connectors.list_visible(user_id)}

    def scope(self, user_id: int) -> tuple[str, str | None]:
        """The runtime resolution of an actor's scope (``users`` row)."""
        return user_scope(self.users.get(user_id))


@pytest.fixture
def world(tmp_path: Path) -> World:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    units = OrgUnitRepo(pool)
    units.create(key="sales", label_zh="销售", label_en="Sales")
    units.create(key="eng", label_zh="研发", label_en="Engineering")
    units.create(key=DELETED_UNIT, label_zh="旧部门", label_en="Legacy")

    users = UserRepo(pool)
    owner = users.create(username="owner", password_hash="h", role="user", org_unit="sales")
    sales_peer = users.create(
        username="peer_sales", password_hash="h", role="user", org_unit="sales"
    )
    eng_peer = users.create(username="peer_eng", password_hash="h", role="user", org_unit="eng")
    unassigned = users.create(username="peer_none", password_hash="h", role="user")
    admin = users.create(username="admin", password_hash="h", role="admin")

    shapes = (
        Shape("private"),
        Shape("public", visibility="public"),
        Shape("unit", visibility="unit", unit_key="sales"),
        Shape("unit-grant", visibility="unit", unit_key="sales", grants=(("unit", "eng"),)),
        Shape("user-grant", grants=(("user", str(eng_peer)),)),
        Shape("role-grant", grants=(("role", "user"),)),
        Shape("deleted-unit", visibility="unit", unit_key=DELETED_UNIT),
        Shape("system-private", system_owned=True),
        Shape("system-public", visibility="public", system_owned=True),
    )
    acl = ResourceAclRepo(pool)
    connectors = ConnectorRepo(pool)
    for shape in shapes:
        connectors.create(
            instance_id=f"CONN-{shape.name}",
            user_id=owner,
            kind="qq-mail",
            display_name=f"conn {shape.name}",
            mcp_server_name=f"mcp-{shape.name}",
        )
        for resource_type, resource_id in (
            ("connector", f"CONN-{shape.name}"),
            ("knowledge_base", f"KB-{shape.name}"),
        ):
            acl.upsert(
                AclEntry(
                    resource_type=resource_type,
                    resource_id=resource_id,
                    owner_user_id=None if shape.system_owned else owner,
                    visibility=shape.visibility,
                    unit_key=shape.unit_key,
                    version=1,
                    grants=shape.grants,
                )
            )

    # Deleting the unit leaves ``unit_key`` NULL (``ON DELETE SET NULL``): the
    # shape must fall back to owner/admin instead of matching unassigned users.
    units.delete(DELETED_UNIT)

    return World(
        acl=acl,
        connectors=connectors,
        users=users,
        viewers={
            "owner": owner,
            "peer_sales": sales_peer,
            "peer_eng": eng_peer,
            "peer_none": unassigned,
            "admin": admin,
        },
        shapes=shapes,
    )


def test_connector_list_and_knowledge_base_scope_resolve_the_same_rule(world: World) -> None:
    connector_entries = world.entries("connector")
    kb_entries = world.entries("knowledge_base")

    for name, user_id in world.viewers.items():
        role, unit_key = world.scope(user_id)
        assert world.acl.scope_for_user(user_id) == (role, unit_key)
        visible = world.visible_connectors(user_id)
        # The list is what the rule says, entry by entry — not a second copy of it.
        assert visible == {
            entry.resource_id
            for entry in connector_entries.values()
            if can_access(entry, user_id=user_id, role=role, unit_key=unit_key)
        }, f"connector list disagrees with sharing.can_access for {name}"

        kb_allowed = allowed_resource_ids(
            kb_entries.values(), user_id=user_id, role=role, unit_key=unit_key
        )
        for shape in world.shapes:
            seen = world.connector_id(shape.name) in visible
            entry = kb_entries[world.kb_id(shape.name)]
            assert seen == (entry.resource_id in kb_allowed), f"{name} / {shape.name}: kb list"
            assert seen == may_read_knowledge_base(
                entry, user_id=user_id, role=role, unit_key=unit_key
            ), f"{name} / {shape.name}: kb read check"


def test_connector_list_is_what_the_rule_permits(world: World) -> None:
    """Named cells, so a rule change has to be a decision here as well."""
    visible = {name: world.visible_connectors(user_id) for name, user_id in world.viewers.items()}
    expected = {
        ("private", "owner"): True,
        ("private", "peer_sales"): False,
        ("private", "admin"): True,
        ("public", "peer_none"): True,
        ("unit", "peer_sales"): True,
        ("unit", "peer_eng"): False,
        ("unit", "peer_none"): False,
        ("unit-grant", "peer_eng"): True,
        ("user-grant", "peer_eng"): True,
        ("user-grant", "peer_sales"): False,
        ("role-grant", "peer_none"): True,
        ("deleted-unit", "owner"): True,
        ("deleted-unit", "admin"): True,
        ("deleted-unit", "peer_none"): False,
        ("deleted-unit", "peer_sales"): False,
        ("system-private", "peer_sales"): False,
        ("system-private", "admin"): True,
        ("system-public", "peer_none"): True,
    }
    for (shape, viewer), want in expected.items():
        got = world.connector_id(shape) in visible[viewer]
        assert got is want, f"{viewer} / {shape}: expected {want}, got {got}"


def test_unknown_user_sees_only_published_connectors(world: World) -> None:
    """No ``users`` row resolves to no role and no unit: public entries only."""
    visible = world.visible_connectors(999_999)

    assert visible == {"CONN-public", "CONN-system-public"}
