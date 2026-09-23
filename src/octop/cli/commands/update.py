"""`octop update` — manual source-upgrade guidance."""

from __future__ import annotations

import click

from octop.cli.support.db import resolve_cli_locale
from octop.i18n import tr
from octop.infra.setup.self_update import get_local_version

UPGRADE_URL = "https://github.com/whl736989911/AI-MAITU"


@click.command("update")
@click.option("--check", is_flag=True, default=False, help="Show manual upgrade instructions only.")
@click.option(
    "--yes",
    "-y",
    is_flag=True,
    default=False,
    help="(Unsupported) automatic upgrades are disabled.",
)
@click.option(
    "--verbose",
    "-v",
    is_flag=True,
    default=False,
    help="(Unsupported) automatic upgrades are disabled.",
)
@click.option(
    "--allow-prerelease",
    "allow_prerelease",
    is_flag=True,
    default=False,
    help="(Unsupported) automatic upgrades are disabled.",
)
def update(check: bool, yes: bool, verbose: bool, allow_prerelease: bool) -> None:
    """Show manual upgrade instructions; automatic package upgrades are disabled."""
    del yes, verbose, allow_prerelease
    locale = resolve_cli_locale()
    click.echo(tr("cli.update.installed", locale, version=get_local_version()))
    click.echo(tr("cli.update.source", locale))
    click.echo(UPGRADE_URL)
    click.echo(tr("cli.update.instruction", locale))
    if not check:
        raise click.ClickException(tr("cli.update.automatic_disabled", locale))
