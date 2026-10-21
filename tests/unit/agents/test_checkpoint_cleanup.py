from __future__ import annotations

import sqlite3
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.sqlite import SqliteSaver

from octop.config import OctopConfig
from octop.infra.agents.checkpoint_cleanup import (
    delete_thread_checkpoints,
    delete_user_thread_checkpoints,
)
from octop.infra.errors import ErrorCode, OctopError


def _seed_checkpoint_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    saver = SqliteSaver(connection)
    saver.setup()
    connection.executemany(
        "INSERT INTO checkpoints(thread_id, checkpoint_ns, checkpoint_id) VALUES (?, '', ?)",
        [("thread-owned", "cp-1"), ("other-thread", "cp-2")],
    )
    connection.executemany(
        "INSERT INTO writes(thread_id, checkpoint_ns, checkpoint_id, task_id, idx, channel) "
        "VALUES (?, '', ?, 'task', 0, 'messages')",
        [("thread-owned", "cp-1"), ("other-thread", "cp-2")],
    )
    connection.commit()
    connection.close()


def test_stopped_agent_checkpoint_delete_uses_persistent_sqlite_saver(tmp_path: Path) -> None:
    workspace = tmp_path / "agent"
    system_dir = workspace / ".maitu"
    system_dir.mkdir(parents=True)
    checkpoints = system_dir / "checkpoints.sqlite"
    _seed_checkpoint_db(checkpoints)
    keep = workspace / "README.txt"
    keep.write_text("workspace content remains", encoding="utf-8")

    deleted = delete_thread_checkpoints(
        agent_id="AGENT1",
        thread_id="thread-owned",
        cfg={"system_files_path": ".maitu"},
        octop_config=OctopConfig(),
        workspace_dir=workspace,
    )

    assert deleted is True
    connection = sqlite3.connect(checkpoints)
    assert connection.execute(
        "SELECT thread_id FROM checkpoints ORDER BY thread_id"
    ).fetchall() == [("other-thread",)]
    assert connection.execute("SELECT thread_id FROM writes").fetchall() == [("other-thread",)]
    connection.close()
    assert keep.read_text(encoding="utf-8") == "workspace content remains"


def test_missing_stopped_agent_store_is_empty_not_failure(tmp_path: Path) -> None:
    assert (
        delete_thread_checkpoints(
            agent_id="AGENT1",
            thread_id="thread-missing",
            cfg={"system_files_path": ".maitu"},
            octop_config=OctopConfig(),
            workspace_dir=tmp_path / "agent",
        )
        is False
    )


@pytest.mark.parametrize("memory_enabled", [None, True, False])
def test_memory_sqlite_checkpoint_is_deleted_even_after_disabling_memory(
    tmp_path: Path, memory_enabled: bool | None
) -> None:
    workspace = tmp_path / "agent"
    system_dir = workspace / ".maitu"
    system_dir.mkdir(parents=True)
    memory_path = system_dir / "memory.sqlite"
    _seed_checkpoint_db(memory_path)

    deleted = delete_thread_checkpoints(
        agent_id="AGENT1",
        thread_id="thread-owned",
        cfg={
            "system_files_path": ".maitu",
            "memory": {
                **({"memory_enabled": memory_enabled} if memory_enabled is not None else {}),
                "backend": {"type": "sqlite", "db_path": str(memory_path)},
            },
        },
        octop_config=OctopConfig(),
        workspace_dir=workspace,
    )

    assert deleted is True
    connection = sqlite3.connect(memory_path)
    assert connection.execute(
        "SELECT thread_id FROM checkpoints ORDER BY thread_id"
    ).fetchall() == [("other-thread",)]
    assert connection.execute("SELECT thread_id FROM writes").fetchall() == [("other-thread",)]
    connection.close()


def test_checkpoint_storage_error_is_retryable_error(tmp_path: Path) -> None:
    workspace = tmp_path / "agent"
    system_dir = workspace / ".maitu"
    system_dir.mkdir(parents=True)
    (system_dir / "checkpoints.sqlite").write_text("not a sqlite database", encoding="utf-8")

    with pytest.raises(OctopError) as exc_info:
        delete_thread_checkpoints(
            agent_id="AGENT1",
            thread_id="thread-owned",
            cfg={"system_files_path": ".maitu"},
            octop_config=OctopConfig(),
            workspace_dir=workspace,
        )

    assert exc_info.value.code is ErrorCode.CHECKPOINT_DELETE_FAILED


def test_user_checkpoint_cleanup_scopes_to_user_threads_only(tmp_path: Path) -> None:
    workspace = tmp_path / "agent"
    system_dir = workspace / ".maitu"
    system_dir.mkdir(parents=True)
    checkpoints = system_dir / "checkpoints.sqlite"
    _seed_checkpoint_db(checkpoints)
    agent = SimpleNamespace(agent_id="AGENT1", config_json='{"system_files_path":".maitu"}')
    services = SimpleNamespace(
        thread_repo=SimpleNamespace(list_for_user=lambda user_id: [("AGENT1", "thread-owned")]),
        agent_repo=SimpleNamespace(get=lambda agent_id: agent),
        config=OctopConfig(),
        paths=SimpleNamespace(agent_workspace=lambda agent_id: workspace),
    )

    delete_user_thread_checkpoints(services, user_id=7)

    connection = sqlite3.connect(checkpoints)
    assert connection.execute(
        "SELECT thread_id FROM checkpoints ORDER BY thread_id"
    ).fetchall() == [("other-thread",)]
    connection.close()
