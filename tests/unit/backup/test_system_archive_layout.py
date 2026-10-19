"""Backup coverage for both workspace system-directory layouts.

The system directory was renamed (``.octop`` -> ``.maitu``). A backup that says
it skips chat history must honour that promise for a workspace on the current
layout and for one that has not been migrated yet.
"""

from __future__ import annotations

import json
import tarfile
from pathlib import Path

import pytest

from octop.config import DatabaseConfig
from octop.infra.backup.system_archive import create_system_backup
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.utils.paths import PathLayout


def _prepare_workspace(
    tmp_path: Path,
    *,
    system_dirname: str,
) -> tuple[PathLayout, SqlitePool, Path]:
    layout = PathLayout(tmp_path / "source")
    layout.root.mkdir()
    pool = SqlitePool(layout.db)
    run_migrations(pool)
    with pool.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at)"
            " VALUES (1, 'alice', 'hash', 'admin', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, created_at, updated_at)"
            " VALUES ('agent01', 1, 'Agent', 1, 1)"
        )
    ws = layout.ensure_agent_workspace("agent01")
    (ws / "SOUL.md").write_text("# source", encoding="utf-8")
    sessions = ws / system_dirname / "sessions"
    sessions.mkdir(parents=True)
    (sessions / "history.jsonl").write_text("chat", encoding="utf-8")
    (ws / system_dirname / "checkpoints.sqlite").write_bytes(b"chat-db")
    return layout, pool, ws


def _archive_names(archive: Path) -> set[str]:
    with tarfile.open(archive, mode="r:gz") as tf:
        names = set(tf.getnames())
        manifest = json.loads(tf.extractfile("manifest.json").read().decode("utf-8"))
    assert manifest["includes_chats"] is False
    return names


@pytest.mark.parametrize("system_dirname", [".maitu", ".octop"])
def test_backup_omits_chat_state_under_either_system_directory(
    tmp_path: Path, system_dirname: str
) -> None:
    layout, pool, _ws = _prepare_workspace(tmp_path, system_dirname=system_dirname)

    class Row:
        agent_id = "agent01"
        name = "Agent"
        config_json = json.dumps({"system_files_path": system_dirname})

    archive = tmp_path / "backup.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    names = _archive_names(archive)
    assert "workspaces/agent01/SOUL.md" in names
    assert f"workspaces/agent01/{system_dirname}/sessions/history.jsonl" not in names
    assert f"workspaces/agent01/{system_dirname}/checkpoints.sqlite" not in names


def test_legacy_config_keeps_skipping_a_legacy_workspace(tmp_path: Path) -> None:
    """A row still carrying ``.octop`` must not start packing chats by accident."""
    layout, pool, _ws = _prepare_workspace(tmp_path, system_dirname=".octop")

    class Row:
        agent_id = "agent01"
        name = "Agent"
        config_json = '{"system_files_path": ".octop"}'

    archive = tmp_path / "backup.tar.gz"
    create_system_backup(
        paths=layout,
        agent_rows=[Row()],
        pool=pool,
        db_config=DatabaseConfig(),
        dest=archive,
    )
    pool.close()

    names = _archive_names(archive)
    assert "workspaces/agent01/.octop/sessions/history.jsonl" not in names
