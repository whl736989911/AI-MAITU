"""The v35 data migration: legacy user knowledge bases fold into the space.

design §13. The helper and the two SQL records are one implementation per
dialect; what these tests pin is the outcome a deployment ends up with — where
the documents, their bytes, their audience and the agent bindings land, and that
a second boot changes none of it.

The migration is exercised through ``run_migrations`` rather than by calling the
helper, because that is the path a real deployment takes: the base is created
after the first boot, and the *next* boot is what folds it in.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.knowledge import service as service_module
from octop.infra.knowledge.files import document_path
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.utils.ulid import new_short_id


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    services = SimpleNamespace(
        db=pool,
        knowledge_repo=KnowledgeRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
    )
    monkeypatch.setattr(service_module, "assert_knowledge_usable", lambda *_args: None)
    return SimpleNamespace(
        pool=pool,
        services=services,
        knowledge=KnowledgeService(services),
        agents=AgentRepo(pool),
    )


def _upgrade(env: SimpleNamespace) -> None:
    """Boot again with the watermark one below 35, so v35 runs as it would.

    That is the whole path a real upgrade takes: the boot that crosses the
    version folds the legacy base in, and no later boot repeats it. Rewinding the
    watermark is how these tests pin that wiring rather than calling the helper
    directly.
    """
    with env.pool.connect() as conn:
        conn.execute("UPDATE _schema_version SET version = 34")
    run_migrations(env.pool)


def _space_id(pool: SqlitePool) -> str:
    with pool.connect() as conn:
        row = conn.execute(
            "SELECT knowledge_base_id FROM knowledge_bases WHERE is_enterprise = 1"
        ).fetchone()
    return str(row["knowledge_base_id"])


def _base_ids(pool: SqlitePool) -> list[str]:
    with pool.connect() as conn:
        rows = conn.execute(
            "SELECT knowledge_base_id FROM knowledge_bases ORDER BY knowledge_base_id"
        ).fetchall()
    return [str(row["knowledge_base_id"]) for row in rows]


def _legacy_base(
    env: SimpleNamespace, *, owner: int, shared: bool = False, name: str = "Legacy"
) -> str:
    # One owner cannot hold two bases of the same name (``UNIQUE(owner_user_id,
    # name)``), so a test that needs two names them.
    return env.services.knowledge_repo.create_base(owner_user_id=owner, name=name, shared=shared).id


def _uploaded(
    env: SimpleNamespace, kb_id: str, *, owner: int, filename: str, body: str = "# Notes"
) -> str:
    return env.knowledge.upload_document(
        kb_id,
        actor_user_id=owner,
        filename=filename,
        content_type="text/markdown",
        content=body.encode("utf-8"),
    ).id


def test_a_legacy_base_and_its_files_move_into_the_space(env: SimpleNamespace) -> None:
    """design §13: the deployment ends with one library, files included."""
    owner = env.services.user_repo.create(username="owner", password_hash="h", role="user")
    legacy = _legacy_base(env, owner=owner, shared=True)
    document_id = _uploaded(
        env, legacy, owner=owner, filename="policy.md", body="# Policy\n\nRefunds take five days."
    )
    assert document_path(legacy, document_id, "policy.md").is_file()

    _upgrade(env)

    space = _space_id(env.pool)
    assert _base_ids(env.pool) == [space]
    moved = env.services.knowledge_repo.get_document(document_id)
    assert moved is not None and moved.kb_id == space
    # The bytes followed the row: a document the space lists and cannot open
    # would be worse than one it never listed.
    assert document_path(space, document_id, "policy.md").is_file()
    preview = env.knowledge.preview_document(space, document_id, actor_user_id=owner)
    assert "Refunds take five days." in preview["text"]
    assert env.services.knowledge_repo.get_base(space).doc_count == 1


def test_the_moved_documents_keep_the_audience_their_base_had(env: SimpleNamespace) -> None:
    """design §13/§14: the space is public, so the narrow rule has to travel."""
    users = env.services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    stranger = users.create(username="stranger", password_hash="h", role="user")
    legacy = _legacy_base(env, owner=owner)
    document_id = _uploaded(env, legacy, owner=owner, filename="minutes.md")

    _upgrade(env)

    space = _space_id(env.pool)
    assert [row.id for row in env.knowledge.list_documents(space, actor_user_id=owner)] == [
        document_id
    ]
    assert env.knowledge.list_documents(space, actor_user_id=stranger) == []
    with pytest.raises(PermissionError, match="document read"):
        env.knowledge.preview_document(space, document_id, actor_user_id=stranger)
    entry = env.services.knowledge_repo.document_acl_entries()[document_id]
    assert entry.owner_user_id == owner
    assert entry.visibility == "private"


def test_grants_travel_with_the_documents_they_reached(env: SimpleNamespace) -> None:
    """A base granted to one person still reaches that person, and only them."""
    users = env.services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    viewer = users.create(username="viewer", password_hash="h", role="user")
    other = users.create(username="other", password_hash="h", role="user")
    legacy = _legacy_base(env, owner=owner)
    document_id = _uploaded(env, legacy, owner=owner, filename="contract.md")
    ResourceAclRepo(env.pool).upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=legacy,
            owner_user_id=owner,
            visibility="private",
            unit_key=None,
            version=2,
            grants=(("user", str(viewer)),),
        )
    )

    _upgrade(env)

    space = _space_id(env.pool)
    entry = env.services.knowledge_repo.document_acl_entries()[document_id]
    assert entry.grants == (("user", str(viewer)),)
    assert [row.id for row in env.knowledge.list_documents(space, actor_user_id=viewer)] == [
        document_id
    ]
    assert env.knowledge.list_documents(space, actor_user_id=other) == []


def test_a_base_without_an_acl_row_stays_administrators_only(env: SimpleNamespace) -> None:
    """A database that lost the base's row was admin-only; it must stay so.

    The space reaches everyone, so inheriting its rule here would publish what
    the missing row had made unreachable.
    """
    users = env.services.user_repo
    owner = users.create(username="owner", password_hash="h", role="user")
    admin = users.create(username="root", password_hash="h", role="admin")
    legacy = _legacy_base(env, owner=owner, shared=True)
    document_id = _uploaded(env, legacy, owner=owner, filename="notes.md")
    ResourceAclRepo(env.pool).delete("knowledge_base", legacy)

    _upgrade(env)

    space = _space_id(env.pool)
    assert env.knowledge.list_documents(space, actor_user_id=owner) == []
    assert (
        env.knowledge.preview_document(space, document_id, actor_user_id=admin, is_admin=True)["id"]
        == document_id
    )
    entry = env.services.knowledge_repo.document_acl_entries()[document_id]
    assert entry.owner_user_id is None
    assert entry.visibility == "private"


def test_an_agent_that_named_legacy_bases_names_the_space(env: SimpleNamespace) -> None:
    """The bindings move too: a live reference must not answer with a dead id."""
    owner = env.services.user_repo.create(username="owner", password_hash="h", role="user")
    first = _legacy_base(env, owner=owner, shared=True, name="First")
    second = _legacy_base(env, owner=owner, shared=True, name="Second")
    agent_id = new_short_id()
    env.agents.create(
        agent_id=agent_id,
        user_id=owner,
        name="Helper",
        knowledge_base_ids=json.dumps([first, second]),
    )

    _upgrade(env)

    row = env.agents.get(agent_id)
    assert row is not None
    # Both ids named the same documents, so the space appears once.
    assert json.loads(str(row.knowledge_base_ids)) == [_space_id(env.pool)]


def test_a_second_boot_changes_nothing(env: SimpleNamespace) -> None:
    """Idempotent by construction: every step is guarded on the state it makes."""
    owner = env.services.user_repo.create(username="owner", password_hash="h", role="user")
    viewer = env.services.user_repo.create(username="viewer", password_hash="h", role="user")
    legacy = _legacy_base(env, owner=owner)
    document_id = _uploaded(env, legacy, owner=owner, filename="policy.md")
    ResourceAclRepo(env.pool).upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=legacy,
            owner_user_id=owner,
            visibility="private",
            unit_key=None,
            version=2,
            grants=(("user", str(viewer)),),
        )
    )

    _upgrade(env)
    space = _space_id(env.pool)
    first = env.services.knowledge_repo.document_acl_entries()[document_id]
    _upgrade(env)

    again = env.services.knowledge_repo.document_acl_entries()[document_id]
    assert again == first
    assert _base_ids(env.pool) == [space]
    assert env.services.knowledge_repo.get_base(space).doc_count == 1
    with env.pool.connect() as conn:
        grants = conn.execute(
            "SELECT COUNT(*) AS n FROM resource_acl_grants "
            "WHERE resource_type = 'knowledge_document' AND resource_id = ?",
            (document_id,),
        ).fetchone()
    assert int(grants["n"]) == 1


def test_the_index_travels_with_the_documents(env: SimpleNamespace) -> None:
    """A moved document keeps its chunks: they are what search reads.

    The chunks live in a per-base sidecar file, so a row that moved without them
    would be a document the space lists and cannot find (design §9, §13).
    """
    owner = env.services.user_repo.create(username="owner", password_hash="h", role="user")
    legacy = _legacy_base(env, owner=owner, shared=True)
    document_id = _uploaded(env, legacy, owner=owner, filename="policy.md")
    KnowledgeIndex(legacy).replace_doc_chunks(document_id, ["refunds take five days"], [[1.0, 0.0]])

    _upgrade(env)

    space = _space_id(env.pool)
    chunks = KnowledgeIndex(space).doc_chunks(document_id)
    assert [text for text, _vector, _meta in chunks] == ["refunds take five days"]
    # Moved, not copied: the bytes live under the space now. (The emptied base
    # directory is removed best-effort — on Windows an open SQLite handle can
    # keep it — which is why the invariant asserted here is the file, not the
    # directory.)
    assert not document_path(legacy, document_id, "policy.md").exists()
    assert document_path(space, document_id, "policy.md").is_file()
