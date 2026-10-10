"""Local customer commands for instance license status and synchronization."""

from __future__ import annotations

import asyncio
import json

import click

from octop.i18n import license_reason_message
from octop.infra.errors import OctopError
from octop.infra.licensing import LicenseService, current_license
from octop.infra.utils.locale import DEFAULT_LOCALE


@click.group("license")
def license() -> None:
    """Inspect, activate, or refresh this Octop instance license."""


def _service() -> LicenseService:
    service = current_license()
    service.load_cached()
    return service


def _emit(payload: dict[str, object]) -> None:
    click.echo(json.dumps(payload, ensure_ascii=False, indent=2))


@license.command("status")
def status() -> None:
    """Show the local cached license status without contacting the server."""
    data = _service().status()
    _emit(data)
    reason = data.get("reason")
    if not data.get("allowed"):
        click.echo(license_reason_message(reason, DEFAULT_LOCALE), err=True)


@license.command("activate")
@click.option("--code", "activation_code", default=None, help="One-time activation code.")
def activate(activation_code: str | None) -> None:
    """Activate the local instance using an issued activation code."""
    code = activation_code or click.prompt("Activation code", hide_input=True)
    try:
        result = asyncio.run(_service().activate(code))
    except OctopError as exc:
        click.echo(f"error: {exc.to_envelope(locale=DEFAULT_LOCALE)['error']['message']}", err=True)
        raise SystemExit(1) from exc
    _emit(result)


@license.command("refresh")
def refresh() -> None:
    """Synchronize this instance license with the configured license center."""
    try:
        result = asyncio.run(_service().refresh())
    except OctopError as exc:
        click.echo(f"error: {exc.to_envelope(locale=DEFAULT_LOCALE)['error']['message']}", err=True)
        raise SystemExit(1) from exc
    _emit(result)
