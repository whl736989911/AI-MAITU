"""CLI business commands stay denied while a managed license is locked."""

from __future__ import annotations

from typing import Any

import click
from click.testing import CliRunner

from octop.cli.commands import license as license_command
from octop.cli.main import cli
from octop.infra.errors import ErrorCode, OctopError


class _LockedLicense:
    """Minimal locked-manager stand-in: business access always denied."""

    def __init__(self) -> None:
        self.refreshed = False

    def require_business_access(self) -> None:
        raise OctopError(
            ErrorCode.INSTANCE_LICENSE_LOCKED,
            "instance license is not active",
            details={"reason": "unactivated"},
        )

    def snapshot(self) -> dict[str, Any]:
        return {
            "managed": True,
            "state": None,
            "business_allowed": False,
            "reason": "unactivated",
            "instance_id": "instance",
            "license_id": None,
            "license_version": 0,
            "license_expires_at": None,
            "credential_type": None,
            "expiring_soon": False,
            "last_sync_at": None,
            "sync_error": None,
        }

    async def refresh(self) -> dict[str, Any]:
        self.refreshed = True
        return self.snapshot()


def test_offline_business_command_is_denied_when_instance_is_locked(monkeypatch) -> None:
    from octop.infra.licensing import service as licensing_module

    monkeypatch.setattr(licensing_module, "_CURRENT", _LockedLicense())

    result = CliRunner().invoke(cli, ["agent", "list"], standalone_mode=False)

    assert isinstance(result.exception, click.ClickException)
    assert isinstance(result.exception.__cause__, OctopError)
    assert result.exception.__cause__.code == ErrorCode.INSTANCE_LICENSE_LOCKED


def test_license_status_exits_nonzero_when_managed_and_locked(monkeypatch) -> None:
    manager = _LockedLicense()
    monkeypatch.setattr(license_command, "_manager", lambda: manager)

    result = CliRunner().invoke(license_command.license, ["status"], standalone_mode=False)

    assert result.exit_code == 1
    assert "business_allowed: False" in result.output


def test_nested_business_help_is_available_without_authorization(monkeypatch) -> None:
    from octop.infra.licensing import service as licensing_module

    monkeypatch.setattr(licensing_module, "_CURRENT", _LockedLicense())

    result = CliRunner().invoke(cli, ["agent", "list", "--help"])

    assert result.exit_code == 0, result.output
