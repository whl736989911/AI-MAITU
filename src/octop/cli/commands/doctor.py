"""Audit runtime dependencies for the selected installation capabilities."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys

import click

from octop.cli.support.db import resolve_cli_locale
from octop.i18n import tr
from octop.infra import deps


@click.command("doctor")
@click.option("--require", "required_extras", default="", help="Comma-separated extras to require.")
@click.option("--fix", is_flag=True, help="Install missing OS packages with apt when available.")
@click.option("--json", "json_output", is_flag=True, help="Print machine-readable JSON.")
def doctor(required_extras: str, fix: bool, json_output: bool) -> None:
    """Check runtime dependencies for required capabilities."""
    locale = resolve_cli_locale()
    extras = {item.strip() for item in required_extras.split(",") if item.strip()}
    invalid = sorted(extras.difference(deps.EXTRAS))
    if invalid:
        raise click.UsageError(
            tr(
                "deps.unknown_extra",
                locale,
                extras=", ".join(invalid),
                available=", ".join(deps.EXTRAS),
            )
        )

    results = deps.check(extras)
    before = results
    attempted: list[str] = []
    if fix:
        packages = deps.missing_apt_packages(results)
        if packages:
            if sys.platform.startswith("linux") and shutil.which("apt-get"):
                command = ["apt-get", "install", "-y", "--no-install-recommends", *packages]
                if os.geteuid() != 0:
                    if shutil.which("sudo"):
                        command.insert(0, "sudo")
                        command.insert(1, "-n")
                        attempted = packages
                        subprocess.run(command, check=False)
                    else:
                        click.echo(tr("deps.apt_command", locale, packages=" ".join(packages)))
                else:
                    attempted = packages
                    subprocess.run(command, check=False)
                results = deps.check(extras)
            else:
                click.echo(tr("deps.install_hint", locale, packages=" ".join(packages)))

    if json_output:
        click.echo(
            json.dumps(
                {
                    "components": [
                        {
                            "key": result.component.key,
                            "label": tr(f"deps.{result.component.key}", locale),
                            "required": result.required,
                            "ok": result.ok,
                            "missing": [
                                {"kind": item.kind, "name": item.name, "detail": item.detail}
                                for item in result.missing
                            ],
                        }
                        for result in results
                    ],
                    "healthy": deps.healthy(results),
                    "attempted_apt_packages": attempted,
                    "changed_components": [
                        result.component.key
                        for old, result in zip(before, results, strict=True)
                        if old.ok != result.ok
                    ],
                },
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for result in results:
            label = tr(f"deps.{result.component.key}", locale)
            status = tr("deps.ok" if result.ok else "deps.missing", locale)
            requirement = tr("deps.required" if result.required else "deps.optional", locale)
            click.echo(f"{label} ({result.component.key})\t{requirement}\t{status}")
            for item in result.missing:
                click.echo(f"  {item.kind}: {item.name}: {item.detail}")
        click.echo(
            tr(
                "deps.summary",
                locale,
                ok=sum(result.ok for result in results),
                missing=sum(bool(result.missing) for result in results),
            )
        )
        if fix and attempted:
            changed = [
                result.component.key
                for old, result in zip(before, results, strict=True)
                if old.ok != result.ok
            ]
            click.echo(tr("deps.rerun", locale, components=", ".join(changed)))
    if not deps.healthy(results):
        raise click.exceptions.Exit(1)
