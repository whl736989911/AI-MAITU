"""Read-only storage status CLI."""

from __future__ import annotations

import json
import locale
import os
from pathlib import Path

import click

from octop.i18n import tr
from octop.infra.storage import storage_report
from octop.infra.utils.paths import PathLayout


def _locale() -> str:
    language = (
        os.environ.get("LC_ALL") or os.environ.get("LC_MESSAGES") or os.environ.get("LANG") or ""
    )
    if not language:
        language = locale.getlocale()[0] or ""
    if language.lower().startswith(("zh", "chinese")):
        return "zh"
    return "en"


@click.group()
def storage() -> None:
    """Inspect storage usage without starting the server."""


@storage.command("status")
@click.option(
    "--home", type=click.Path(path_type=Path), default=None, help="Storage root to inspect."
)
@click.option("--json", "as_json", is_flag=True, help="Emit machine-readable JSON.")
def status(home: Path | None, as_json: bool) -> None:
    """Show a read-only inventory of stored data."""
    report = storage_report(PathLayout(home) if home is not None else PathLayout.from_env())
    if as_json:
        click.echo(json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
        return
    loc = _locale()
    click.echo(tr("storage.title", loc, root=report["root"]))
    click.echo(tr("storage.locale_note", loc))
    disk = report["disk"]
    if disk:
        click.echo(tr("storage.disk", loc, total=disk["total_bytes"], free=disk["free_bytes"]))
    for category, item in report["categories"].items():
        click.echo(
            tr(f"storage.category.{category}", loc, bytes=item["bytes"], files=item["files"])
        )
    scope_kind = report["database_scope"]["kind"]
    click.echo(tr(f"storage.scope.{scope_kind}", loc))
    click.echo(tr("storage.inventory_scope", loc))
    click.echo(tr("storage.retention", loc))
    click.echo(tr("storage.reclamation", loc))
    for error in report["errors"]:
        click.echo(tr("storage.error", loc, error=error), err=True)
