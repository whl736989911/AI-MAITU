"""Tests for manual-only `octop update` behavior."""

from __future__ import annotations

from typing import Any

import pytest
from click.testing import CliRunner

from octop.infra.setup import self_update

PROJECT_URL = "https://github.com/whl736989911/AI-MAITU"


def _block_package_updater(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def unexpected(*args: Any, **kwargs: Any) -> None:
        calls.append("called")
        pytest.fail("octop update must not access PyPI or install packages")

    monkeypatch.setattr(self_update, "fetch_pypi_info", unexpected)
    monkeypatch.setattr(self_update, "run_upgrade", unexpected)
    monkeypatch.setattr(self_update, "get_local_version", lambda: "1.2.3")

    from octop.cli.commands import update as update_cmd

    monkeypatch.setattr(update_cmd, "fetch_pypi_info", unexpected, raising=False)
    monkeypatch.setattr(update_cmd, "run_upgrade", unexpected, raising=False)
    monkeypatch.setattr(update_cmd, "get_local_version", lambda: "1.2.3")
    return calls


def test_update_check_prints_manual_source_without_checking_pypi(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _block_package_updater(monkeypatch)
    from octop.cli.commands.update import update

    monkeypatch.setattr("octop.cli.commands.update.resolve_cli_locale", lambda: "zh")

    result = CliRunner().invoke(update, ["--check"])

    assert result.exit_code == 0
    assert "当前安装版本：1.2.3" in result.output
    assert PROJECT_URL in result.output
    assert calls == []


def test_update_manual_invocation_fails_without_installing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = _block_package_updater(monkeypatch)
    from octop.cli.commands.update import update

    monkeypatch.setattr("octop.cli.commands.update.resolve_cli_locale", lambda: "en")
    result = CliRunner().invoke(update, ["--yes"])

    assert result.exit_code != 0
    assert PROJECT_URL in result.output
    assert "Automatic upgrades are disabled" in result.output
    assert calls == []
