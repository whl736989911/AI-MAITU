"""Tests for the `octop chats` group."""

from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

from octop.cli.main import cli


def test_chats_list_requires_agent(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    runner = CliRunner()
    result = runner.invoke(cli, ["chats", "list"])
    assert result.exit_code == 2
