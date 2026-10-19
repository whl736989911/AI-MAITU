"""`maitu license` — instance license management (embedded, works offline).

Talks directly to the local licensing state (same store as the server
process); ``activate``/``refresh`` additionally reach the license center over
the network. ``import`` accepts a pasted token or an offline license file and
is fully offline. Exit code is non-zero when the license blocks business.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

import click

from octop.cli.support.errors import fail_octop
from octop.infra.errors import OctopError
from octop.infra.licensing import LicenseManager
from octop.infra.server import OctopServer


def _manager() -> LicenseManager:
    """Build the configured manager for this install (does not start monitoring)."""
    from octop.infra.licensing import install_license_manager
    from octop.infra.licensing.settings import load_license_settings
    from octop.infra.utils.paths import PathLayout

    paths = PathLayout.from_env()
    try:
        settings = load_license_settings(
            paths,
            default_state_dir=OctopServer.default_license_state_dir(paths.root),
        )
        manager = LicenseManager(
            settings.state_dir,
            settings.trusted_keys_file,
            settings.center_url,
            settings.allow_loopback,
            settings.ca_file,
        )
    except (OSError, ValueError) as exc:
        raise click.ClickException(f"invalid license configuration: {exc}") from exc
    install_license_manager(manager)
    return manager


def _emit(snapshot: dict[str, Any], as_json: bool) -> None:
    if as_json:
        click.echo(json.dumps(snapshot, ensure_ascii=False, indent=2))
        return
    for key in (
        "managed",
        "state",
        "business_allowed",
        "reason",
        "instance_id",
        "license_id",
        "license_version",
        "license_expires_at",
        "credential_type",
        "expiring_soon",
        "last_sync_at",
        "sync_error",
    ):
        click.echo(f"{key}: {snapshot.get(key)}")


def _read_token_argument(value: str) -> str:
    """Accept a pasted token, a file path, or '-' for stdin."""
    if value == "-":
        return sys.stdin.read().strip()
    candidate = Path(value)
    if candidate.is_file():
        return candidate.read_text(encoding="utf-8").strip()
    return value.strip()


def _requires_managed(snapshot: dict[str, Any]) -> bool:
    return bool(snapshot.get("managed"))


@click.group("license")
def license() -> None:
    """Instance license status and activation (status/request/import/activate/refresh)."""


@license.command("status")
@click.option("--json", "json_out", is_flag=True, default=False, help="Machine-readable JSON.")
def license_status(json_out: bool) -> None:
    """Show the current license snapshot (offline, no network)."""
    manager = _manager()
    snapshot = manager.snapshot()
    _emit(snapshot, json_out)
    if _requires_managed(snapshot) and not snapshot.get("business_allowed"):
        sys.exit(1)


@license.command("request")
@click.option("--json", "json_out", is_flag=True, default=False, help="Machine-readable JSON.")
@click.option(
    "--out",
    "out_path",
    type=click.Path(dir_okay=False, writable=True),
    default=None,
    help="Write the request JSON to a file instead of stdout.",
)
def license_request(json_out: bool, out_path: str | None) -> None:
    """Export the signed offline license request for the license center."""
    manager = _manager()
    payload = manager.export_request()
    text = json.dumps(payload, ensure_ascii=False, indent=2)
    if out_path:
        Path(out_path).write_text(text + "\n", encoding="utf-8")
        click.echo(f"offline license request written to {out_path}")
        return
    if json_out:
        click.echo(text)
        return
    for key in ("instance_id", "key_fingerprint"):
        click.echo(f"{key}: {payload.get(key)}")
    click.echo("public_key_pem:")
    click.echo(payload.get("public_key_pem", "").rstrip())
    click.echo("proof: <signed JWT — submit the full JSON to the license center>")


@license.command("import")
@click.argument("token_or_file")
@click.option("--json", "json_out", is_flag=True, default=False, help="Machine-readable JSON.")
def license_import(token_or_file: str, json_out: bool) -> None:
    """Import a signed offline license (file path, '-' for stdin, or pasted token)."""
    token = _read_token_argument(token_or_file)
    if not token:
        raise click.ClickException("empty offline license token")

    async def _run() -> dict[str, Any]:
        return await _manager().import_offline(token)

    try:
        snapshot = asyncio.run(_run())
    except OctopError as exc:
        fail_octop(exc)
        return
    click.echo("offline license imported")
    _emit(snapshot, json_out)
    if _requires_managed(snapshot) and not snapshot.get("business_allowed"):
        sys.exit(1)


@license.command("activate")
@click.argument("code")
@click.option("--json", "json_out", is_flag=True, default=False, help="Machine-readable JSON.")
def license_activate(code: str, json_out: bool) -> None:
    """Activate with a signed code from the license center (needs network)."""

    async def _run() -> dict[str, Any]:
        return await _manager().activate(code.strip())

    try:
        snapshot = asyncio.run(_run())
    except OctopError as exc:
        fail_octop(exc)
        return
    click.echo("activation applied")
    _emit(snapshot, json_out)
    if _requires_managed(snapshot) and not snapshot.get("business_allowed"):
        sys.exit(1)


@license.command("refresh")
@click.option("--json", "json_out", is_flag=True, default=False, help="Machine-readable JSON.")
def license_refresh(json_out: bool) -> None:
    """Refresh the online license credential from the license center."""

    async def _run() -> dict[str, Any]:
        return await _manager().refresh()

    try:
        snapshot = asyncio.run(_run())
    except OctopError as exc:
        fail_octop(exc)
        return
    click.echo("license refreshed")
    _emit(snapshot, json_out)
    if _requires_managed(snapshot) and not snapshot.get("business_allowed"):
        sys.exit(1)
