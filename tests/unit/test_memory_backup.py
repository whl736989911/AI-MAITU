from __future__ import annotations

from pathlib import Path

import pytest
from harness_memory import Memory

from octop.infra.agents.memory_backup import (
    create_backup,
    restore_backup,
)


def _memory(namespace: str, db: Path) -> Memory:
    return Memory(namespace=namespace, backend="sqlite", backend_config={"db_path": str(db)})


def test_restore_replaces_only_selected_namespace_and_roundtrips_documents(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite"
    target = _memory("agent_target", db)
    other = _memory("agent_other", db)
    target.store("before restore", level="root")
    other.store("unrelated namespace", level="root")
    original_atom = target.create_atom("original canonical fact", entity_name="Restored topic")[0]
    original_tree = {node.id: node for node in target.get_tree()}
    docs = {"USER.md": "manual profile", "MEMORY.md": "manual facts"}
    created = create_backup(
        target,
        root=tmp_path / "managed" / "backups",
        agent_id="target",
        scope="shared",
        user_id=11,
        thread_ids=[],
        documents=docs,
        fallback_path=None,
        memory_enabled=True,
    )
    target.store("later change", level="root")
    later_atom = target.create_atom("later canonical fact", entity_name="Restored topic")[0]
    target.deprecate_atom(original_atom.id, actor="user", note="later change")

    result = restore_backup(
        target,
        root=tmp_path / "managed" / "backups",
        backup_id=created["backup_id"],
        agent_id="target",
        scope="shared",
        namespace="agent_target",
        backend=type(target.backend).__name__,
        user_id=11,
        allowed_threads=set(),
        restore_documents=lambda restored: docs.update(restored),
        fallback_path=None,
        memory_enabled=True,
    )

    assert result["restored_backup_id"] == created["backup_id"]
    assert {node.id: node for node in target.get_tree()} == original_tree
    assert target.get_atom(original_atom.id).deprecated_at is None
    assert target.get_atom(later_atom.id) is None
    assert [node.content for node in other.backend.get_tree()] == ["unrelated namespace"]
    assert docs == {"USER.md": "manual profile", "MEMORY.md": "manual facts"}


def test_restore_rejects_archive_for_different_agent(tmp_path: Path) -> None:
    db = tmp_path / "memory.sqlite"
    memory = _memory("agent_target", db)
    created = create_backup(
        memory,
        root=tmp_path / "backups",
        agent_id="target",
        scope="shared",
        user_id=11,
        thread_ids=[],
        documents={"USER.md": "", "MEMORY.md": ""},
        fallback_path=None,
        memory_enabled=True,
    )

    with pytest.raises(ValueError, match="identity or backend"):
        restore_backup(
            memory,
            root=tmp_path / "backups",
            backup_id=created["backup_id"],
            agent_id="different",
            scope="shared",
            namespace="agent_target",
            backend=type(memory.backend).__name__,
            user_id=11,
            allowed_threads=set(),
            restore_documents=lambda _: None,
            fallback_path=None,
            memory_enabled=True,
        )


def test_disabled_memory_uses_scoped_fallback_checkpointer(tmp_path: Path) -> None:
    import sqlite3
    from uuid import uuid4

    from langgraph.checkpoint.sqlite import SqliteSaver

    memory = _memory("agent_target", tmp_path / "memory.sqlite")
    fallback = tmp_path / ".maitu" / "checkpoints.sqlite"
    fallback.parent.mkdir()
    connection = sqlite3.connect(str(fallback))
    saver = SqliteSaver(connection)
    saver.setup()

    def put(thread_id: str, checkpoint_id: str) -> None:
        config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}
        checkpoint = {
            "v": 1,
            "id": checkpoint_id,
            "ts": "2025-01-01T00:00:00+00:00",
            "channel_values": {},
            "channel_versions": {},
            "versions_seen": {},
            "pending_sends": [],
        }
        saver.put(config, checkpoint, {}, {})

    own = "owned-thread"
    other = "other-thread"
    original_id = uuid4().hex
    other_id = uuid4().hex
    put(own, original_id)
    put(other, other_id)
    root = tmp_path / "backups"
    created = create_backup(
        memory,
        root=root,
        agent_id="target",
        scope="shared",
        user_id=11,
        thread_ids=[own],
        documents={"USER.md": "", "MEMORY.md": ""},
        fallback_path=fallback,
        memory_enabled=False,
    )

    put(own, uuid4().hex)
    result = restore_backup(
        memory,
        root=root,
        backup_id=created["backup_id"],
        agent_id="target",
        scope="shared",
        namespace="agent_target",
        backend=type(memory.backend).__name__,
        user_id=11,
        allowed_threads={own},
        restore_documents=lambda _: None,
        fallback_path=fallback,
        memory_enabled=False,
    )

    assert result["checkpoint_threads"] == 1
    assert next(saver.list({"configurable": {"thread_id": own}})).checkpoint["id"] == original_id
    assert next(saver.list({"configurable": {"thread_id": other}})).checkpoint["id"] == other_id
    connection.close()


def test_shared_archive_is_bound_to_authenticated_user(tmp_path: Path) -> None:
    from octop.infra.agents.memory_backup import backup_path, backup_root, list_backups

    user_a_root = backup_root(tmp_path, "agent", "shared", 11)
    user_b_root = backup_root(tmp_path, "agent", "shared", 22)
    assert user_a_root != user_b_root
    memory = _memory("agent_target", tmp_path / "memory.sqlite")
    created = create_backup(
        memory,
        root=user_a_root,
        agent_id="agent",
        scope="shared",
        user_id=11,
        thread_ids=[],
        documents={"USER.md": "", "MEMORY.md": ""},
        fallback_path=None,
        memory_enabled=True,
    )
    assert list_backups(user_a_root, agent_id="agent", scope="shared", user_id=22) == []
    with pytest.raises(ValueError, match="identity"):
        backup_path(user_a_root, created["backup_id"], agent_id="agent", scope="shared", user_id=22)


def test_failed_namespace_restore_keeps_current_data(tmp_path: Path, monkeypatch) -> None:
    from octop.infra.agents.memory_backend import close_memory

    memory = _memory("agent_target", tmp_path / "memory.sqlite")
    try:
        atom = memory.create_atom("original fact", entity_name="Budget")[0]
        root = tmp_path / "backups"
        created = create_backup(
            memory,
            root=root,
            agent_id="target",
            scope="shared",
            user_id=11,
            thread_ids=[],
            documents={"USER.md": "", "MEMORY.md": ""},
            fallback_path=None,
            memory_enabled=True,
        )
        memory.store("post-backup state must survive a failed restore", level="root")
        current_tree = {node.id: node for node in memory.get_tree()}

        def fail_save_node(_node):
            raise OSError("simulated storage write failure")

        monkeypatch.setattr(memory.backend, "save_node", fail_save_node)
        with pytest.raises((OSError, ValueError)):
            restore_backup(
                memory,
                root=root,
                backup_id=created["backup_id"],
                agent_id="target",
                scope="shared",
                namespace="agent_target",
                backend=type(memory.backend).__name__,
                user_id=11,
                allowed_threads=set(),
                restore_documents=lambda _: None,
                fallback_path=None,
                memory_enabled=True,
            )
        assert {node.id: node for node in memory.get_tree()} == current_tree
        assert memory.get_atom(atom.id).assertion == "original fact"
    finally:
        close_memory(memory)
