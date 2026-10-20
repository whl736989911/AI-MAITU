"""Pure configuration rendering for the native updater installer."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest


def _load() -> Any:
    name = "install_native_updater"
    if name in sys.modules:
        return sys.modules[name]
    path = Path(__file__).resolve().parents[2] / "scripts" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def installer() -> Any:
    return _load()


def test_environment_file_quotes_values_and_rejects_newlines(installer: Any) -> None:
    rendered = installer.render_update_env(
        {"MAITU_UPDATE_CENTER_URL": "https://center.example/a b", "A": "x'y"}
    )
    assert "MAITU_UPDATE_CENTER_URL='https://center.example/a b'" in rendered
    assert "A='x'\"'\"'y'" in rendered
    with pytest.raises(installer.InstallError, match="newline"):
        installer.render_update_env({"MAITU_UPDATE_CLIENT_TOKEN": "a\nb"})


def test_release_environment_is_path_only(installer: Any, tmp_path: Path) -> None:
    rendered = installer.render_release_env(tmp_path / "source", tmp_path / "venv")
    assert f"MAITU_RELEASE_ROOT={(tmp_path / 'source').as_posix()}" in rendered
    assert f"MAITU_RELEASE_VENV={(tmp_path / 'venv').as_posix()}" in rendered


def test_release_paths_reject_values_that_updater_cannot_parse(
    installer: Any, tmp_path: Path
) -> None:
    with pytest.raises(installer.InstallError, match="whitespace"):
        installer.render_release_env(tmp_path / "with space", tmp_path / "venv")
    with pytest.raises(installer.InstallError, match="whitespace"):
        installer.render_release_env(Path("relative"), tmp_path / "venv")


def test_launcher_uses_release_environment_and_fixed_entrypoint(installer: Any) -> None:
    launcher = installer.render_launcher()
    assert launcher.startswith("#!/bin/sh\nset -eu\n")
    assert installer.RELEASE_ENV.as_posix() in launcher
    assert 'exec "$MAITU_RELEASE_VENV/bin/octop" run' in launcher
    assert 'exec "$MAITU_RELEASE_VENV/bin/maitu" run' in launcher
    assert "exec " not in launcher.split("if ", 1)[0]


def test_app_and_updater_services_use_separate_environment_files(installer: Any) -> None:
    dropin = installer.render_app_dropin()
    unit = installer.render_updater_unit()
    assert f"EnvironmentFile={installer.UPDATE_ENV.as_posix()}" in dropin
    assert f"EnvironmentFile={installer.UPDATER_ENV.as_posix()}" in unit
    assert "ExecStart=/opt/maitu-updater/run-current" in dropin
    assert "--native" in unit
    assert "User=root" in unit


def test_token_file_must_be_a_single_long_secret(installer: Any, tmp_path: Path) -> None:
    token = tmp_path / "token"
    token.write_text("short\n", encoding="utf-8")
    with pytest.raises(installer.InstallError, match="at least 32"):
        installer._read_token(token)
    token.write_text("x" * 40 + "\n", encoding="utf-8")
    assert installer._read_token(token) == "x" * 40


def test_validate_rejects_missing_venv_entrypoint(installer: Any, tmp_path: Path) -> None:
    source = tmp_path / "source"
    source.mkdir()
    venv = tmp_path / "venv"
    (venv / "bin").mkdir(parents=True)
    (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    data = tmp_path / "data"
    data.mkdir()
    keys = tmp_path / "keys.json"
    keys.write_text("{}", encoding="utf-8")
    args = installer.parser().parse_args(
        [
            "--app-user",
            "maitu",
            "--source-dir",
            str(source),
            "--venv",
            str(venv),
            "--data-dir",
            str(data),
            "--queue-dir",
            str(tmp_path / "queue"),
            "--state-dir",
            str(tmp_path / "state"),
            "--health-url",
            "http://127.0.0.1:8088/api/health",
            "--center-url",
            "https://center.example",
            "--trusted-keys-file",
            str(keys),
            "--client-token-file",
            str(tmp_path / "token"),
        ]
    )
    with pytest.raises(installer.InstallError, match="entry point"):
        installer._validate(args)
