"""Tests for the CLI authorization-center update flow."""

from __future__ import annotations

from typing import Any

from click.testing import CliRunner


def test_update_check_reports_trusted_release_without_queue(monkeypatch: Any) -> None:
    from octop.cli.commands import update as command

    status = {
        "current_version": "1.2.3",
        "latest_version": "1.3.0",
        "has_update": True,
        "error": None,
        "install_available": True,
        "center_configured": True,
    }
    monkeypatch.setattr(command.update_client, "check", lambda *, force: status)
    queued: list[str] = []
    monkeypatch.setattr(
        command.update_client, "queue_upgrade", lambda target: queued.append(target)
    )
    monkeypatch.setattr(command, "resolve_cli_locale", lambda: "en")
    result = CliRunner().invoke(command.update, ["--check"])
    assert result.exit_code == 0
    assert "1.3.0" in result.output
    assert queued == []


def test_update_yes_queues_only_center_release(monkeypatch: Any) -> None:
    from octop.cli.commands import update as command

    status = {
        "current_version": "1.2.3",
        "latest_version": "1.3.0",
        "has_update": True,
        "error": None,
        "install_available": True,
        "center_configured": True,
    }
    queued: list[str] = []
    monkeypatch.setattr(command.update_client, "check", lambda *, force: status)
    monkeypatch.setattr(
        command.update_client,
        "queue_upgrade",
        lambda target: queued.append(target) or {"version": target},
    )
    monkeypatch.setattr(command, "resolve_cli_locale", lambda: "en")
    result = CliRunner().invoke(command.update, ["--yes"])
    assert result.exit_code == 0
    assert queued == ["1.3.0"]


def test_update_reports_missing_configuration_in_user_locale(monkeypatch: Any) -> None:
    from octop.cli.commands import update as command

    status = {
        "current_version": "0.1.0",
        "error": "internal configuration failure",
        "missing_configuration": ["MAITU_UPDATE_CLIENT_TOKEN"],
    }
    monkeypatch.setattr(command.update_client, "check", lambda *, force: status)
    monkeypatch.setattr(command, "resolve_cli_locale", lambda: "zh")
    result = CliRunner().invoke(command.update, ["--check"])
    assert result.exit_code != 0
    assert "尚未配置更新检查" in result.output
    assert "MAITU_UPDATE_CLIENT_TOKEN" in result.output
    assert "internal configuration failure" not in result.output


def test_update_does_not_offer_upgrade_without_host_updater(monkeypatch: Any) -> None:
    from octop.cli.commands import update as command

    status = {
        "current_version": "1.2.3",
        "latest_version": "1.3.0",
        "has_update": True,
        "error": None,
        "install_available": False,
        "center_configured": True,
    }
    queued: list[str] = []
    monkeypatch.setattr(
        command.update_client, "queue_upgrade", lambda target: queued.append(target)
    )
    monkeypatch.setattr(command.update_client, "check", lambda *, force: status)
    monkeypatch.setattr(command, "resolve_cli_locale", lambda: "en")
    result = CliRunner().invoke(command.update, ["--yes"])
    assert result.exit_code != 0
    assert queued == []
