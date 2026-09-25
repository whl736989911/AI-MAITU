"""Memory maintenance must serialize live SQLite access and preserve owner ACLs."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest
from langgraph.checkpoint.base import empty_checkpoint
from langgraph.checkpoint.sqlite import SqliteSaver

from octop.infra.agents.memory_slim import MemorySlimCoordinator


def _live_memory(tmp_path: Path):
    from harness_memory import Memory

    path = tmp_path / "memory.sqlite"
    conn = sqlite3.connect(path)
    saver = SqliteSaver(conn)
    checkpoint = empty_checkpoint()
    checkpoint["channel_values"] = {"memory_contents": {"USER.md": "preserve"}}
    config = saver.put(
        {"configurable": {"thread_id": "t", "checkpoint_ns": ""}}, checkpoint, {}, {}
    )
    conn.close()
    memory = Memory("test_slim", backend_config={"db_path": str(path)})
    return memory, path, config, checkpoint


@pytest.mark.asyncio
async def test_live_maintenance_waits_for_turn_then_releases_admission(tmp_path, monkeypatch):
    from harness_memory.application import checkpoint_maintenance

    memory, path, config, expected = _live_memory(tmp_path)
    active = {"agent": True}
    reservations: set[str] = set()
    agent = SimpleNamespace(_memory_runtime=SimpleNamespace(memory=memory))

    def begin(agent_id: str) -> bool:
        if active.get(agent_id) or agent_id in reservations:
            return False
        reservations.add(agent_id)
        return True

    registry = SimpleNamespace(
        get_agent=lambda _: agent,
        try_begin_history_backfill=begin,
        end_history_backfill=lambda aid: reservations.discard(aid),
    )
    coordinator = MemorySlimCoordinator(registry)
    entered, release = threading.Event(), threading.Event()
    actual = checkpoint_maintenance.slim_live_checkpoints

    def slow(*args, **kwargs):
        entered.set()
        assert release.wait(5)
        return actual(*args, **kwargs)

    monkeypatch.setattr(checkpoint_maintenance, "slim_live_checkpoints", slow)
    try:
        coordinator.start("agent")
        await asyncio.sleep(0.05)
        assert coordinator.state["phase"] == "waiting"
        assert not entered.is_set()
        active.clear()
        assert await asyncio.to_thread(entered.wait, 5)
        assert reservations == {"agent"}
        release.set()
        await coordinator.task
        assert coordinator.state["phase"] == "done"
        assert not reservations
        assert memory.get_tuple(config).checkpoint == expected
        assert path.exists()
    finally:
        release.set()
        await coordinator.close()
        memory._checkpointer.conn.close()
        memory.backend.close()


def test_memory_api_rejects_sqlite_access_while_maintenance_is_mutating(monkeypatch):
    from octop.api.common import memory_client
    from octop.infra.errors import ErrorCode, OctopError

    monkeypatch.setattr(memory_client, "require_agent_owner_row", lambda *args, **kwargs: None)
    opened = []
    monkeypatch.setattr(memory_client, "_open_memory_for_agent", lambda *args: opened.append(True))
    coordinator = SimpleNamespace(status=lambda _: {"phase": "compacting", "kind": "memory_slim"})
    host = SimpleNamespace(
        app_runtime=SimpleNamespace(agent_registry=SimpleNamespace(memory_slim=coordinator))
    )

    with pytest.raises(OctopError) as error:
        memory_client.call_memory_rpc(
            agent_id="owned-agent",
            method="stats_counts",
            params={},
            user=None,
            as_user=None,
            server=host,
        )
    assert error.value.code == ErrorCode.AGENT_BUSY
    assert not opened


def test_cli_memory_slim_uses_running_host_and_streams_json(tmp_path, monkeypatch):
    from click.testing import CliRunner

    from octop.cli.commands import memory as command
    from octop.cli.main import cli

    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.setattr(command, "resolve_agent", lambda explicit: explicit)
    monkeypatch.setattr(command, "resolve_cli_locale", lambda: "en")
    requested = []

    def statuses(root, agent_id, *, locale):
        requested.append((root, agent_id, locale))
        yield {"phase": "backing_up", "elapsed_seconds": 0}
        yield {
            "phase": "done",
            "report": {
                "before": {"file_bytes": 2},
                "after": {"file_bytes": 1},
                "backup_path": "backup.bak",
            },
        }

    monkeypatch.setattr(command, "request_memory_slim", statuses)
    result = CliRunner().invoke(cli, ["--json", "memory", "slim", "--agent", "agent"])
    assert result.exit_code == 0, result.output
    assert requested == [(tmp_path, "agent", "en")]
    assert [__import__("json").loads(line)["phase"] for line in result.output.splitlines()] == [
        "backing_up",
        "done",
    ]


def test_memory_slim_chat_rechecks_current_owner_before_scheduling():
    rows = {"agent": SimpleNamespace(agent_id="agent", user_id=2, name="Agent")}
    registry = SimpleNamespace(
        get_row=rows.get,
        list_agents=lambda user_id: [row for row in rows.values() if row.user_id == user_id],
        list_rows=lambda: [],
    )
    coordinator = MemorySlimCoordinator(registry)
    with pytest.raises(ValueError):
        coordinator._assert_owner("agent", 1, "en")
    assert coordinator.task is None
