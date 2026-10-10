from __future__ import annotations

import click
from click.testing import CliRunner

from octop.cli.commands import license as license_command
from octop.cli.main import cli
from octop.infra.errors import ErrorCode, OctopError


class _LockedLicense:
    def __init__(self) -> None:
        self.refreshed = False

    def load_cached(self) -> dict[str, object]:
        return {}

    def require_business_access(self) -> None:
        raise OctopError(ErrorCode.INSTANCE_LICENSE_LOCKED, "locked")

    def status(self) -> dict[str, object]:
        return {"state": "LOCKED", "allowed": False, "reason": "unactivated"}

    async def refresh(self) -> dict[str, object]:
        self.refreshed = True
        return {"state": "LOCKED", "allowed": False, "reason": "unactivated"}


def test_offline_business_command_is_denied_when_instance_is_locked(monkeypatch) -> None:
    service = _LockedLicense()
    monkeypatch.setattr("octop.infra.licensing.current_license", lambda: service)
    monkeypatch.setattr(license_command, "current_license", lambda: service)

    result = CliRunner().invoke(cli, ["agent", "list"], standalone_mode=False)

    assert isinstance(result.exception, click.ClickException)
    assert isinstance(result.exception.__cause__, OctopError)
    assert result.exception.__cause__.code == ErrorCode.INSTANCE_LICENSE_LOCKED


def test_nested_business_help_is_available_without_authorization(monkeypatch) -> None:
    service = _LockedLicense()
    monkeypatch.setattr("octop.infra.licensing.current_license", lambda: service)
    result = CliRunner().invoke(cli, ["agent", "list", "--help"])
    assert result.exit_code == 0, result.output
