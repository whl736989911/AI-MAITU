"""Tests for offline CLI DB helpers."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from octop.cli.main import cli
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.agents import AgentRepo
from octop.infra.db.repos.threads import ThreadRepo
from octop.infra.utils.paths import PathLayout
from octop.infra.utils.ulid import new_ulid


@pytest.fixture
def fake_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path))
    return tmp_path


def _bootstrap(fake_home: Path) -> None:
    runner = CliRunner()
    result = runner.invoke(
        cli,
        ["init", "--admin-username", "alice", "--admin-password", "TestPass12", "--yes"],
    )
    assert result.exit_code == 0, result.output


def test_list_agents_offline(fake_home: Path) -> None:
    _bootstrap(fake_home)
    paths = PathLayout(fake_home / ".maitu")
    db = SqlitePool(paths.db)
    AgentRepo(db).create(agent_id="ag1", user_id=1, name="Bot")
    db.close()

    from octop.cli.support.db import list_agents_offline

    rows = list_agents_offline(as_user="alice")
    assert len(rows) == 1
    assert rows[0]["agent_id"] == "ag1"


def test_list_threads_offline(fake_home: Path) -> None:
    _bootstrap(fake_home)
    paths = PathLayout(fake_home / ".maitu")
    db = SqlitePool(paths.db)
    AgentRepo(db).create(agent_id="ag1", user_id=1, name="Bot")
    tid = new_ulid()
    ThreadRepo(db).insert(
        thread_id=tid,
        agent_id="ag1",
        user_id=1,
        channel_type="dashboard",
        session_key="ag1:dashboard:1:dm",
        title="hello",
    )
    db.close()

    from octop.cli.support.db import list_threads_offline

    rows = list_threads_offline(agent_id="ag1", as_user="alice", limit=10)
    assert len(rows) == 1
    assert rows[0]["thread_id"] == tid
    assert rows[0]["has_messages"] is None


def test_agent_list_offline_flag(fake_home: Path) -> None:
    _bootstrap(fake_home)
    paths = PathLayout(fake_home / ".maitu")
    db = SqlitePool(paths.db)
    AgentRepo(db).create(agent_id="ag1", user_id=1, name="Bot")
    db.close()

    runner = CliRunner()
    result = runner.invoke(cli, ["agent", "list", "--user", "alice"])
    assert result.exit_code == 0, result.output
    assert "ag1" in result.output


@pytest.mark.asyncio
@pytest.mark.parametrize("entrypoint", ["user", "init"])
async def test_cli_account_creation_uses_the_login_identifier(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, entrypoint: str
) -> None:
    from octop.cli.support.db import open_cli_services
    from octop.infra.users.manager import UserManager

    home = tmp_path / "app"
    monkeypatch.setenv("OCTOP_HOME", str(home))
    monkeypatch.setenv("MAITU_HOME", str(home))
    if entrypoint == "init":
        args = ["init", "--admin-username", " alice ", "--admin-password", "TestPass12", "--yes"]
    else:
        args = ["user", "create", " alice ", "--password", "TestPass12"]
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 0, result.output

    with open_cli_services(home) as services:
        manager = UserManager(services)
        await manager.boot()
        user = await manager.authenticate(" alice ", "TestPass12")
        stored = services.user_repo.get_by_username("alice")
        assert stored is not None
        assert user is not None and user.id == stored.id
        assert services.user_repo.get_by_username(" alice ") is None
