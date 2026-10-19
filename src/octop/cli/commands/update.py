"""`maitu update` — check and queue trusted source releases."""

from __future__ import annotations

import click

from octop.cli.support.db import resolve_cli_locale
from octop.i18n import tr
from octop.infra.setup.update_client import UpdateClientError, update_client


@click.command("update")
@click.option(
    "--check", is_flag=True, default=False, help="Check the configured authorization center only."
)
@click.option(
    "--yes", "-y", is_flag=True, default=False, help="Confirm and queue an available upgrade."
)
def update(check: bool, yes: bool) -> None:
    """Check signed authorization-center releases and queue a host upgrade."""
    locale = resolve_cli_locale()
    status = update_client.check(force=True)
    click.echo(tr("cli.update.installed", locale, version=status["current_version"]))
    if status["error"]:
        raise click.ClickException(status["error"])
    if not status["center_configured"]:
        raise click.ClickException(tr("cli.update.center_unconfigured", locale))
    if not status["latest_version"]:
        click.echo(tr("cli.update.none", locale))
        return
    click.echo(tr("cli.update.latest", locale, version=status["latest_version"]))
    if not status["has_update"]:
        return
    if check:
        return
    if not status["install_available"]:
        raise click.ClickException(tr("cli.update.updater_unavailable", locale))
    if not yes and not click.confirm(
        tr("cli.update.confirm", locale, version=status["latest_version"])
    ):
        return
    try:
        result = update_client.queue_upgrade(status["latest_version"])
    except UpdateClientError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(tr("cli.update.queued", locale, version=result["version"]))
