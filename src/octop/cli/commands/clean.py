"""`octop clean` — remove CLI state and (optionally) all Octop data."""

from __future__ import annotations

import shutil
from pathlib import Path

import click

from octop.infra.utils.paths import PathLayout


def _cli_targets() -> list[Path]:
    paths = PathLayout.from_env()
    return [paths.root / "cli_state.json"]


def _all_targets() -> list[Path]:
    return [PathLayout.from_env().root]


@click.command("clean")
@click.option("--all", "wipe_all", is_flag=True, default=False, help="Wipe ALL of ~/.octop.")
@click.option("--yes", is_flag=True, default=False, help="Skip confirmation.")
@click.option("--dry-run", is_flag=True, default=False, help="Show targets without deleting.")
def clean(wipe_all: bool, yes: bool, dry_run: bool) -> None:
    """Remove the CLI state file (default) or wipe everything (--all)."""
    if wipe_all:
        root = PathLayout.from_env().root
        targets = (
            [child for child in root.iterdir() if child.name != "license"] if root.is_dir() else []
        )
    else:
        targets = _cli_targets()
    for t in targets:
        click.echo(("would remove " if dry_run else "removing ") + str(t))
    if dry_run:
        return
    if wipe_all and not yes:
        from octop.branding import platform_name

        click.confirm(
            f"This deletes all {platform_name()} data (DB, configs, logs). Continue?",
            abort=True,
        )
    elif not yes:
        click.confirm("Remove CLI state?", abort=True)
    for t in targets:
        if not t.exists():
            continue
        if t.is_dir():
            shutil.rmtree(t)
        else:
            t.unlink()
    click.echo("done")
