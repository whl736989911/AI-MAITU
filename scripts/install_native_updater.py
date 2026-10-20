#!/usr/bin/env python3
"""Configure signed source auto-updates for a native systemd MAITU install.

This installer is deliberately explicit: it does not guess the application's
virtualenv, data directory, unit name, or release-reader token.  Run it once as
root, inspect the planned changes, then let the updater service perform upgrades.
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path

UPDATER_NAME = "maitu-updater.service"
UPDATE_ENV = Path("/etc/maitu/update.env")
UPDATER_ENV = Path("/etc/maitu/updater.env")
RELEASE_ENV = Path("/etc/maitu/current-release.env")
LAUNCHER = Path("/opt/maitu-updater/run-current")
DEFAULT_ROOT = Path("/opt/maitu-updater")
SYSTEMD_DIR = Path("/etc/systemd/system")
UNIT_NAME = "maitu.service"


class InstallError(RuntimeError):
    """The requested native installation cannot be configured safely."""


def _shell_assignment(name: str, value: str) -> str:
    if not name.replace("_", "").isalnum() or not name[0].isalpha():
        raise InstallError(f"invalid environment name: {name!r}")
    if "\n" in value or "\r" in value or "\x00" in value:
        raise InstallError(f"environment value for {name} contains a newline")
    return f"{name}={shlex.quote(value)}"


def render_update_env(values: dict[str, str]) -> str:
    return "\n".join(_shell_assignment(key, value) for key, value in values.items()) + "\n"


def _release_assignment(name: str, path: Path) -> str:
    value = path.as_posix()
    if (
        not path.is_absolute()
        or not value
        or any(char.isspace() for char in value)
        or any(char in value for char in "'\"\\$`;&|<>()*?[]{}\x00\r\n")
    ):
        raise InstallError(f"release path for {name} must be absolute without whitespace")
    return f"{name}={value}"


def render_release_env(root: Path, venv: Path) -> str:
    # The updater parses this file without shell evaluation, while the fixed
    # launcher sources it. Absolute paths without whitespace satisfy both.
    return (
        _release_assignment("MAITU_RELEASE_ROOT", root)
        + "\n"
        + _release_assignment("MAITU_RELEASE_VENV", venv)
        + "\n"
    )


def render_launcher() -> str:
    # The release env is replaced atomically by the updater.  The launcher
    # never executes a release-controlled script, only a fixed console entry.
    return (
        "#!/bin/sh\n"
        "set -eu\n"
        f". {RELEASE_ENV.as_posix()}\n"
        'if [ -x "$MAITU_RELEASE_VENV/bin/octop" ]; then\n'
        '  exec "$MAITU_RELEASE_VENV/bin/octop" run\n'
        "fi\n"
        'exec "$MAITU_RELEASE_VENV/bin/maitu" run\n'
    )


def render_app_dropin(launcher: Path = LAUNCHER, env_file: Path = UPDATE_ENV) -> str:
    return (
        "[Service]\n"
        "ExecStart=\n"
        f"ExecStart={launcher.as_posix()}\n"
        f"EnvironmentFile={env_file.as_posix()}\n"
    )


def render_updater_unit(
    root: Path = DEFAULT_ROOT,
    env_file: Path = UPDATER_ENV,
    app_unit: str = UNIT_NAME,
) -> str:
    return (
        "[Unit]\n"
        "Description=MAITU signed source update worker\n"
        f"After=network-online.target {app_unit}\n"
        "Wants=network-online.target\n"
        "\n"
        "[Service]\n"
        "Type=simple\n"
        "User=root\n"
        f"EnvironmentFile={env_file.as_posix()}\n"
        f"ExecStart={root.as_posix()}/.venv/bin/python {root.as_posix()}/maitu_updater.py --native\n"
        "Restart=on-failure\n"
        "RestartSec=5\n"
        # Release trees must be readable/executable by the app service user;
        # secrets live in env/state files, not in release sources.
        "UMask=0022\n"
        "PrivateTmp=true\n"
        "NoNewPrivileges=true\n"
        "\n"
        "[Install]\n"
        "WantedBy=multi-user.target\n"
    )


def _run(arguments: list[str], *, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(arguments, check=check, text=True, capture_output=True)


def _require_root() -> None:
    if os.name != "posix" or os.geteuid() != 0:
        raise InstallError("run this installer as root on the Linux host")


def _unit_exists(unit: str) -> bool:
    if not unit.endswith(".service") or "/" in unit or ".." in unit:
        raise InstallError("unit must be a .service name without paths")
    result = _run(["systemctl", "show", unit, "-p", "LoadState", "--value"], check=False)
    return result.returncode == 0 and result.stdout.strip() == "loaded"


def _write(path: Path, content: str, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.chmod(temporary, mode)
    os.replace(temporary, path)


def _copy_updater(root: Path, source_root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True, mode=0o755)
    os.chmod(root, 0o755)
    for filename in ("maitu_updater.py", "maitu-updater-requirements.txt"):
        source = source_root / filename
        if not source.is_file():
            raise InstallError(f"missing updater input: {source}")
        destination = root / filename
        shutil.copyfile(source, destination)
        os.chmod(destination, 0o600)
    venv = root / ".venv"
    if not venv.is_dir():
        _run([sys.executable, "-m", "venv", str(venv)])
    python = venv / "bin" / "python"
    if not python.is_file():
        raise InstallError("updater virtualenv was not created")
    _run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "-r",
            str(root / "maitu-updater-requirements.txt"),
        ]
    )


def _queue_dir_owner(queue_dir: Path, owner: str) -> None:
    import pwd

    account = pwd.getpwnam(owner)
    queue_dir.mkdir(parents=True, exist_ok=True)
    os.chown(queue_dir, account.pw_uid, account.pw_gid)
    os.chmod(queue_dir, 0o750)


def _read_token(path: Path) -> str:
    try:
        token = path.read_text(encoding="utf-8").strip()
    except OSError as exc:
        raise InstallError(f"cannot read release-reader token file: {exc}") from exc
    if len(token) < 32 or "\n" in token:
        raise InstallError("release-reader token file must contain one token of at least 32 chars")
    return token


def _release_configuration(
    args: argparse.Namespace,
) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    token = _read_token(args.client_token_file)
    app_values = {
        "MAITU_UPDATE_CENTER_URL": args.center_url.rstrip("/"),
        "MAITU_UPDATE_CLIENT_TOKEN": token,
        "MAITU_UPDATE_TRUSTED_KEYS_FILE": str(args.trusted_keys_file),
        "MAITU_UPDATE_QUEUE_DIR": str(args.queue_dir),
    }
    uv = shutil.which("uv")
    npm = shutil.which("npm")
    if uv is None or npm is None:
        raise InstallError("uv and npm must be available on PATH to build source releases")
    updater_values = {
        **app_values,
        "MAITU_UPDATE_MODE": "native",
        "MAITU_UPDATE_NATIVE_UV": uv,
        "MAITU_UPDATE_NATIVE_NPM": npm,
        "MAITU_UPDATE_STATE_DIR": str(args.state_dir),
        "MAITU_UPDATE_DATA_DIR": str(args.data_dir),
        "MAITU_UPDATE_HEALTH_URL": args.health_url,
        "MAITU_UPDATE_SYSTEMD_UNIT": args.unit,
        "MAITU_UPDATE_RELEASE_ENV_FILE": str(RELEASE_ENV),
        "MAITU_UPDATE_NATIVE_EXTRAS": ",".join(args.extra),
        "MAITU_UPDATE_ARCHITECTURE": args.architecture,
    }
    if args.postgres_dsn_file is not None:
        try:
            dsn = args.postgres_dsn_file.read_text(encoding="utf-8").strip()
        except OSError as exc:
            raise InstallError(f"cannot read PostgreSQL DSN file: {exc}") from exc
        if not dsn:
            raise InstallError("PostgreSQL DSN file is empty")
        updater_values["MAITU_UPDATE_POSTGRES_DSN"] = dsn
    return (
        app_values,
        updater_values,
        {
            "MAITU_RELEASE_ROOT": str(args.source_dir),
            "MAITU_RELEASE_VENV": str(args.venv),
        },
    )


def _validate(args: argparse.Namespace) -> None:
    if not args.source_dir.is_dir():
        raise InstallError(f"source directory does not exist: {args.source_dir}")
    if not (args.venv / "pyvenv.cfg").is_file():
        raise InstallError(f"virtualenv does not exist: {args.venv}")
    if not (args.venv / "bin" / "octop").is_file() and not (args.venv / "bin" / "maitu").is_file():
        raise InstallError(f"virtualenv has no octop/maitu entry point: {args.venv}")
    if not args.data_dir.is_dir():
        raise InstallError(f"data directory does not exist: {args.data_dir}")
    if not args.trusted_keys_file.is_file():
        raise InstallError(f"release trusted keys file does not exist: {args.trusted_keys_file}")
    if args.architecture not in {"amd64", "arm64"}:
        raise InstallError("architecture must be amd64 or arm64")


def install(args: argparse.Namespace) -> None:
    _require_root()
    _validate(args)
    if not _unit_exists(args.unit):
        raise InstallError(f"systemd unit is not loaded: {args.unit}")
    app_values, updater_values, release_values = _release_configuration(args)
    import pwd

    account = pwd.getpwnam(args.app_user)
    _queue_dir_owner(args.queue_dir, args.app_user)
    args.state_dir.mkdir(parents=True, exist_ok=True, mode=0o711)
    os.chmod(args.state_dir, 0o711)
    _copy_updater(args.updater_root, Path(__file__).resolve().parent)

    _write(UPDATE_ENV, render_update_env(app_values), 0o640)
    os.chown(UPDATE_ENV, 0, account.pw_gid)
    _write(UPDATER_ENV, render_update_env(updater_values), 0o600)
    _write(
        RELEASE_ENV,
        render_release_env(Path(release_values["MAITU_RELEASE_ROOT"]), args.venv),
        0o644,
    )
    _write(LAUNCHER, render_launcher(), 0o755)
    dropin = SYSTEMD_DIR / f"{args.unit}.d" / "90-maitu-updater.conf"
    _write(dropin, render_app_dropin(), 0o644)
    updater_unit = SYSTEMD_DIR / UPDATER_NAME
    _write(updater_unit, render_updater_unit(args.updater_root, UPDATER_ENV, args.unit), 0o644)
    _run(["systemctl", "daemon-reload"])
    _run(["systemctl", "enable", "--now", UPDATER_NAME])
    _run(["systemctl", "restart", args.unit])
    _wait_ready(args.queue_dir, args.health_url)


def _wait_ready(queue_dir: Path, health_url: str, timeout: int = 30) -> None:
    import json
    import urllib.request

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        heartbeat = queue_dir / "updater-ready.json"
        try:
            data = json.loads(heartbeat.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            data = None
        if isinstance(data, dict) and data.get("ready") is True:
            try:
                with urllib.request.urlopen(health_url, timeout=3) as response:
                    if json.load(response).get("ok") is True:
                        return
            except (OSError, ValueError):
                pass
        time.sleep(1)
    raise InstallError(
        "updater is running but did not become ready; inspect maitu-updater.service logs"
    )


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--unit", default=UNIT_NAME)
    result.add_argument("--app-user", required=True)
    result.add_argument("--source-dir", type=Path, required=True)
    result.add_argument("--venv", type=Path, required=True)
    result.add_argument("--data-dir", type=Path, required=True)
    result.add_argument("--queue-dir", type=Path, required=True)
    result.add_argument("--state-dir", type=Path, required=True)
    result.add_argument("--health-url", required=True)
    result.add_argument("--center-url", required=True)
    result.add_argument("--trusted-keys-file", type=Path, required=True)
    result.add_argument("--client-token-file", type=Path, required=True)
    result.add_argument("--postgres-dsn-file", type=Path)
    result.add_argument("--extra", action="append", default=[])
    result.add_argument("--architecture", choices=("amd64", "arm64"), default="amd64")
    result.add_argument("--updater-root", type=Path, default=DEFAULT_ROOT)
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    if not args.extra:
        args.extra = ["office", "knowledge-ocr"]
    try:
        install(args)
    except InstallError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
