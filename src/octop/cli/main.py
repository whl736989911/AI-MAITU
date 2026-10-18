"""octop CLI entry point with lazy command loading."""

from __future__ import annotations

import importlib
import sys
from functools import wraps
from typing import Any, ClassVar

import click

from octop.branding import platform_name
from octop.cli.registry import COMMANDS


def _ensure_utf8_stdio(
    streams: tuple[Any, ...] | None = None,
) -> None:
    """Force UTF-8 output so emoji (✅/❌/QR art) can never crash the CLI.

    On Windows the ANSI code page is often GBK (cp936); Python then encodes
    piped/redirected stdout and stderr with it, so any non-GBK character
    (e.g. the success checkmark printed by ``octop init``) raises
    ``UnicodeEncodeError`` and kills the process after the work was already
    done. Output is re-encoded to UTF-8 (``errors="replace"`` as a last
    resort) instead.

    ``streams`` is overridable for tests; the CLI entry uses ``sys.stdout``
    and ``sys.stderr``.
    """
    for stream in (sys.stdout, sys.stderr) if streams is None else streams:
        if stream is None:
            continue
        encoding = getattr(stream, "encoding", None) or ""
        if encoding.lower().replace("_", "-") == "utf-8":
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError, OSError, UnicodeError):
            continue


def _get_version() -> str:
    try:
        from importlib.metadata import version as pkg_version

        return pkg_version("octop")
    except Exception:
        return "unknown"


def _print_version(ctx: click.Context, _param: click.Parameter, value: bool) -> None:
    if not value or ctx.resilient_parsing:
        return
    from octop.branding import platform_name

    click.echo(f"{platform_name()} CLI v{_get_version()}")
    ctx.exit()


class _LazyCLI(click.Group):
    """Click group that imports command modules only when invoked."""

    _registry: ClassVar[dict[str, tuple[str, str, str]]] = COMMANDS
    _LOCKED_ALLOWED_COMMANDS: ClassVar[frozenset[str]] = frozenset(
        {
            "license",
            "init",
            "run",
            "service",
            "captcha",
            "completion",
            "version",
            "update",
        }
    )

    def list_commands(self, ctx: click.Context) -> list[str]:
        return sorted(self._registry)

    def get_command(self, ctx: click.Context, name: str) -> click.Command | None:
        if name not in self._registry:
            return None
        module_path, attr, _help = self._registry[name]
        mod = importlib.import_module(module_path, package=__package__)
        result = getattr(mod, attr)
        if not isinstance(result, click.Command):
            return None
        if name not in self._LOCKED_ALLOWED_COMMANDS:
            self._guard_business_callbacks(result)
        return result

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        """Override to avoid importing modules just to read their help text."""
        rows = [(name, self._registry[name][2]) for name in self.list_commands(ctx)]
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)

    @staticmethod
    def _guard_business_callbacks(command: click.Command) -> None:
        """Check at execution, after Click has handled even nested help options."""
        if isinstance(command, click.Group):
            for child in command.commands.values():
                _LazyCLI._guard_business_callbacks(child)
        callback = command.callback
        if callback is None or getattr(callback, "_instance_license_guarded", False):
            return

        @wraps(callback)
        def guarded(*args: Any, **kwargs: Any) -> Any:
            ctx = click.get_current_context()
            if not isinstance(command, click.Group) or ctx.invoked_subcommand is None:
                from octop.i18n import license_reason_message
                from octop.infra.errors import OctopError
                from octop.infra.licensing import current_license

                try:
                    current_license().require_business_access()
                except OctopError as exc:
                    reason = (exc.details or {}).get("reason") if exc.details else None
                    raise click.ClickException(license_reason_message(reason, "en")) from exc
            return callback(*args, **kwargs)

        guarded._instance_license_guarded = True  # type: ignore[attr-defined]
        command.callback = guarded


@click.group(
    cls=_LazyCLI,
    context_settings={"help_option_names": ["-h", "--help"]},
    help=f"{platform_name()} command-line interface.",
)
@click.option(
    "-v",
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=_print_version,
    help="Show the installed platform version.",
)
@click.option(
    "--user",
    "as_user",
    default=None,
    envvar="OCTOP_USER",
    help="Default --user for subcommands (admin acting on behalf of a user).",
)
@click.option(
    "--agent",
    "agent_id",
    default=None,
    envvar="OCTOP_AGENT",
    help="Default --agent for subcommands.",
)
@click.option(
    "--json",
    "json_out",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON for list-style commands.",
)
@click.pass_context
def cli(
    ctx: click.Context,
    as_user: str | None,
    agent_id: str | None,
    json_out: bool,
) -> None:
    """Octop command-line interface."""
    _ensure_utf8_stdio()
    ctx.ensure_object(dict)
    ctx.obj["as_user"] = as_user
    ctx.obj["agent_id"] = agent_id
    ctx.obj["json_out"] = json_out


if __name__ == "__main__":
    cli()
