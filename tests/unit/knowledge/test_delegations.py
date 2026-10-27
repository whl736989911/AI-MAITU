"""Document-scoped delegation graph lifecycle and durable revocation events."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.knowledge.delegations import KnowledgeDelegationService
from octop.infra.sharing import AclEntry
from octop.infra.sharing.service import SharingService


@pytest.fixture
def graph(tmp_path: Path):
    pool = SqlitePool(tmp_path / "delegations.db")
    run_migrations(pool)
    users = UserRepo(pool)
    knowledge = KnowledgeRepo(pool)
    acl = ResourceAclRepo(pool)
    owner = users.create(username="owner", password_hash="x", role="user")
    alice = users.create(username="alice", password_hash="x", role="user")
    bob = users.create(username="bob", password_hash="x", role="user")
    carol = users.create(username="carol", password_hash="x", role="user")
    kb = knowledge.create_base(owner_user_id=owner, name="Private")
    sharing = SharingService(pool)
    sharing.apply_change(
        owner,
        "knowledge_base",
        kb.id,
        AclEntry(
            "knowledge_base",
            kb.id,
            owner,
            "private",
            None,
            0,
            grants=tuple(("user", str(user_id)) for user_id in (alice, bob, carol)),
            permission="write",
        ),
    )
    document = knowledge.create_document(
        kb_id=kb.id, filename="report.md", content_type="text/markdown", byte_size=8
    )
    sharing.apply_change(
        owner,
        "knowledge_document",
        document.id,
        AclEntry(
            "knowledge_document",
            document.id,
            owner,
            "private",
            None,
            0,
            grants=(("user", str(alice)),),
            permission="write",
            grant_delegation=(("user", str(alice)),),
        ),
    )
    service = KnowledgeDelegationService(pool)
    try:
        yield SimpleNamespace(
            pool=pool,
            users=users,
            knowledge=knowledge,
            acl=acl,
            sharing=sharing,
            service=service,
            owner=owner,
            alice=alice,
            bob=bob,
            carol=carol,
            kb=kb,
            document=document,
        )
    finally:
        pool.close()


def test_root_defaults_to_no_delegation_and_grant_is_individual(graph):
    entry = graph.acl.get("knowledge_document", graph.document.id)
    assert entry is not None
    assert entry.grant_delegation == (("user", str(graph.alice)),)
    assert (
        graph.service.effective_rights(
            graph.kb.id, graph.document.id, actor_user_id=graph.bob
        ).can_delegate
        is False
    )


def test_multilevel_delegation_rejects_self_and_cycles(graph):
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=True,
    )
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.bob,
        grantee_user_id=graph.carol,
        can_delegate=True,
    )
    assert graph.service.effective_rights(
        graph.kb.id, graph.document.id, actor_user_id=graph.carol
    ).read
    with pytest.raises(OctopError):
        graph.service.upsert_delegation(
            graph.kb.id,
            graph.document.id,
            actor_user_id=graph.carol,
            grantee_user_id=graph.alice,
            can_delegate=False,
        )
    with pytest.raises(OctopError):
        graph.service.upsert_delegation(
            graph.kb.id,
            graph.document.id,
            actor_user_id=graph.alice,
            grantee_user_id=graph.alice,
            can_delegate=False,
        )


def test_delegation_never_exceeds_current_source_permission(graph):
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=True,
    )
    assert graph.service.effective_rights(
        graph.kb.id, graph.document.id, actor_user_id=graph.bob
    ).write
    entry = graph.acl.get("knowledge_document", graph.document.id)
    assert entry is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_document",
        graph.document.id,
        AclEntry(
            "knowledge_document",
            graph.document.id,
            graph.owner,
            "private",
            None,
            0,
            grants=entry.grants,
            permission="read",
            grant_delegation=entry.grant_delegation,
        ),
    )
    right = graph.service.effective_rights(graph.kb.id, graph.document.id, actor_user_id=graph.bob)
    assert right.read and not right.write


def test_revoking_source_cascades_and_notifies_each_affected_user(graph):
    first = graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=True,
    )["grants"]
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.bob,
        grantee_user_id=graph.carol,
        can_delegate=True,
    )
    graph.service.revoke_delegation(
        graph.kb.id, graph.document.id, first[0]["id"], actor_user_id=graph.alice
    )
    for user_id in (graph.bob, graph.carol):
        right = graph.service.effective_rights(
            graph.kb.id, graph.document.id, actor_user_id=user_id
        )
        assert not right.read and not right.write and not right.can_delegate
    bob_events = graph.service.list_notifications(actor_user_id=graph.bob)["items"]
    carol_events = graph.service.list_notifications(actor_user_id=graph.carol)["items"]
    assert len(bob_events) == len(carol_events) == 1
    assert all(item["resource_id"] == graph.document.id for item in bob_events + carol_events)
    assert all(not item["access_retained"] for item in bob_events + carol_events)


def test_disabling_root_cascades_without_resurrection(graph):
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=True,
    )
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.bob,
        grantee_user_id=graph.carol,
        can_delegate=True,
    )
    entry = graph.acl.get("knowledge_document", graph.document.id)
    assert entry is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_document",
        graph.document.id,
        AclEntry(
            "knowledge_document",
            graph.document.id,
            graph.owner,
            "private",
            None,
            0,
            grants=entry.grants,
            permission=entry.permission,
        ),
    )
    assert (
        graph.service.list_delegations(graph.kb.id, graph.document.id, actor_user_id=graph.owner)[
            "grants"
        ]
        == []
    )
    entry = graph.acl.get("knowledge_document", graph.document.id)
    assert entry is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_document",
        graph.document.id,
        AclEntry(
            "knowledge_document",
            graph.document.id,
            graph.owner,
            "private",
            None,
            0,
            grants=entry.grants,
            permission=entry.permission,
            grant_delegation=(("user", str(graph.alice)),),
        ),
    )
    assert (
        graph.service.list_delegations(graph.kb.id, graph.document.id, actor_user_id=graph.owner)[
            "grants"
        ]
        == []
    )
    assert graph.service.list_notifications(actor_user_id=graph.alice)["items"] == []


def test_direct_root_removal_notifies_without_any_delegation_edges(graph):
    before = graph.acl.get("knowledge_document", graph.document.id)
    assert before is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_document",
        graph.document.id,
        AclEntry("knowledge_document", graph.document.id, graph.owner, "private", None, 0),
    )
    events = graph.service.list_notifications(actor_user_id=graph.alice)["items"]
    assert len(events) == 1
    assert events[0]["resource_id"] == graph.document.id


def test_notification_failure_rolls_back_delegation_revocation(graph, monkeypatch):
    grant = graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=False,
    )["grants"][0]

    def fail_insert(**_kwargs):
        raise RuntimeError("notification storage unavailable")

    monkeypatch.setattr(graph.service._notifications, "insert", fail_insert)
    with pytest.raises(RuntimeError, match="notification storage unavailable"):
        graph.service.revoke_delegation(
            graph.kb.id, graph.document.id, grant["id"], actor_user_id=graph.alice
        )
    assert graph.service._delegations.get_by_delegation_id(grant["id"]) is not None
    assert graph.service.list_notifications(actor_user_id=graph.bob)["items"] == []


def test_delegations_are_document_scoped_and_require_active_kb_readers(graph):
    other = graph.knowledge.create_document(
        kb_id=graph.kb.id, filename="other.md", content_type="text/markdown", byte_size=4
    )
    grant = graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=False,
    )["grants"][0]
    with pytest.raises(LookupError):
        graph.service.revoke_delegation(
            graph.kb.id, other.id, grant["id"], actor_user_id=graph.alice
        )
    foreign = graph.users.create(username="foreign", password_hash="x", role="user")
    with pytest.raises(LookupError):
        graph.service.upsert_delegation(
            graph.kb.id,
            graph.document.id,
            actor_user_id=graph.alice,
            grantee_user_id=foreign,
            can_delegate=False,
        )


def test_base_delegation_does_not_bypass_a_document_denial(graph):
    base_entry = graph.acl.get("knowledge_base", graph.kb.id)
    assert base_entry is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_base",
        graph.kb.id,
        AclEntry(
            "knowledge_base",
            graph.kb.id,
            graph.owner,
            "private",
            None,
            base_entry.version,
            grants=base_entry.grants,
            permission="write",
            grant_delegation=(("user", str(graph.alice)),),
        ),
    )
    current = graph.acl.get("knowledge_document", graph.document.id)
    assert current is not None
    graph.sharing.apply_change(
        graph.owner,
        "knowledge_document",
        graph.document.id,
        AclEntry(
            "knowledge_document",
            graph.document.id,
            graph.owner,
            "private",
            None,
            current.version,
            grants=(("user", str(graph.bob)),),
        ),
    )
    rights = graph.service.effective_rights(
        graph.kb.id, graph.document.id, actor_user_id=graph.alice
    )
    assert not rights.read and not rights.write and not rights.can_delegate
    with pytest.raises(OctopError):
        graph.service.upsert_delegation(
            graph.kb.id,
            graph.document.id,
            actor_user_id=graph.alice,
            grantee_user_id=graph.carol,
            can_delegate=False,
        )


def test_stale_delegation_does_not_reveal_a_base_after_department_access_is_lost(graph):
    units = OrgUnitRepo(graph.pool)
    for key in ("permitted", "other"):
        units.create(key=key, label_zh=key, label_en=key)
    with graph.pool.transaction() as conn:
        conn.execute(
            "UPDATE users SET org_unit = ? WHERE id IN (?, ?)",
            ("permitted", graph.alice, graph.bob),
        )
    graph.acl.upsert(
        AclEntry(
            "knowledge_base",
            graph.kb.id,
            graph.owner,
            "unit",
            "permitted",
            1,
            permission="write",
        )
    )
    graph.service.upsert_delegation(
        graph.kb.id,
        graph.document.id,
        actor_user_id=graph.alice,
        grantee_user_id=graph.bob,
        can_delegate=False,
    )
    assert graph.kb.id in {base.id for base in graph.knowledge.list_visible(graph.bob)}
    with graph.pool.transaction() as conn:
        conn.execute("UPDATE users SET org_unit = ? WHERE id = ?", ("other", graph.bob))
    assert not graph.service.effective_rights(
        graph.kb.id, graph.document.id, actor_user_id=graph.bob
    ).read
    assert graph.kb.id not in {base.id for base in graph.knowledge.list_visible(graph.bob)}
