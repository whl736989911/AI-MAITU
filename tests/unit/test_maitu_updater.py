"""Source archive boundaries and customer-data rollback invariants."""

from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import os
import shutil
import stat
import sys
import time
import zipfile
from pathlib import Path
from typing import Any

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


def _load(name: str) -> Any:
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
def updater_module() -> Any:
    return _load("maitu_updater")


def _archive(
    path: Path, extra: dict[str, bytes] | None = None, *, version: str = "1.2.3"
) -> dict[str, Any]:
    manifest = {
        "product": "maitu",
        "version": "1.2.3",
        "package_type": "source",
        "platform": "linux",
        "architecture": "amd64",
    }
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("maitu-release.json", json.dumps(manifest))
        archive.writestr("pyproject.toml", f'[project]\nversion="{version}"\n')
        for required in (
            "uv.lock",
            "docker/Dockerfile",
            "dashboard/package.json",
            "dashboard/package-lock.json",
        ):
            archive.writestr(required, "")
        for name, value in (extra or {}).items():
            entry = zipfile.ZipInfo(name)
            # ZipInfo normalizes Windows separators; malicious archives need raw bytes.
            entry.filename = name
            entry.orig_filename = name
            archive.writestr(entry, value)
    return manifest


@pytest.mark.parametrize(
    "name",
    [
        "../outside.txt",
        "/absolute.txt",
        "src/../../outside.txt",
        "src\\outside.txt",
        "C:/outside.txt",
        "src/./outside.txt",
    ],
)
def test_archive_rejects_escaping_or_ambiguous_paths(
    tmp_path: Path, updater_module: Any, name: str
) -> None:
    archive = tmp_path / "release.zip"
    manifest = _archive(archive, {name: b"untrusted"})
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError):
        updater_module.validate_zip(archive, target, manifest)
    assert not (tmp_path / "outside.txt").exists()
    assert not (target / "pyproject.toml").exists()


def test_archive_rejects_symlink_before_extracting(tmp_path: Path, updater_module: Any) -> None:
    archive = tmp_path / "release.zip"
    manifest = _archive(archive)
    with zipfile.ZipFile(archive, "a") as source:
        link = zipfile.ZipInfo("src/link")
        link.create_system = 3
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        source.writestr(link, "../../outside")
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError, match="unsafe"):
        updater_module.validate_zip(archive, target, manifest)
    assert not (target / "src/link").exists()


def test_signed_version_must_match_installed_source(tmp_path: Path, updater_module: Any) -> None:
    archive = tmp_path / "release.zip"
    manifest = _archive(archive, version="9.9.9")
    target = tmp_path / "target"
    target.mkdir()
    with pytest.raises(ValueError, match="project version"):
        updater_module.validate_zip(archive, target, manifest)
    assert not (target / "pyproject.toml").exists()


def test_host_updater_verifies_release_by_kid_from_independent_keyring(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    project.mkdir()
    compose = project / "compose.yml"
    compose.write_text("services: {}", encoding="utf-8")
    data = tmp_path / "customer-data"
    data.mkdir()
    active_key = Ed25519PrivateKey.generate()
    old_key = Ed25519PrivateKey.generate()
    public_keys = {
        "release-old": old_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8"),
        "release-current": active_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8"),
    }
    keyring = tmp_path / "release-trusted-keys.json"
    keyring.write_text(json.dumps(public_keys), encoding="utf-8")
    settings = updater_module.Settings(
        "https://center.example",
        "test-token",
        keyring,
        tmp_path / "queue",
        project,
        data,
        tmp_path / "private-state",
        "http://127.0.0.1:8088/api/health",
        compose,
    )
    updater = updater_module.Updater(settings)
    claims = {
        "iss": "maitu-license-center",
        "aud": "maitu-updater",
        "product": "maitu",
        "protocol": 1,
        "release_id": "a" * 32,
        "version": "1.3.0",
        "filename": "maitu-1.3.0.zip",
        "sha256": "b" * 64,
        "size": 123,
        "package_type": "source",
        "platform": "linux",
        "architecture": "amd64",
        "iat": int(time.time()),
    }
    token = jwt.encode(claims, active_key, algorithm="EdDSA", headers={"kid": "release-current"})

    class Response:
        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_args: Any) -> None:
            return None

        def read(self, _size: int) -> bytes:
            return json.dumps({"manifest": token}).encode("utf-8")

    monkeypatch.setattr(updater, "http", lambda _suffix: Response())
    assert updater.manifest()["version"] == "1.3.0"


def test_rollback_restores_named_data_directory_and_preserves_failed_data(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    project = tmp_path / "checkout"
    project.mkdir()
    compose = project / "compose.yml"
    compose.write_text("services: {}", encoding="utf-8")
    data = tmp_path / "customer-persistent-data"
    data.mkdir()
    (data / "history.db").write_bytes(b"original customer history")
    (data / "config.json").write_text('{"timezone":"Asia/Shanghai"}', encoding="utf-8")
    private_key = Ed25519PrivateKey.generate()
    key = tmp_path / "release-trusted-keys.json"
    pem = (
        private_key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode("utf-8")
    )
    key.write_text(json.dumps({"maitu-release-1": pem}), encoding="utf-8")
    settings = updater_module.Settings(
        "https://center.example",
        "test-token",
        key,
        tmp_path / "queue",
        project,
        data,
        tmp_path / "private-state",
        "http://127.0.0.1:8088/api/health",
        compose,
    )
    updater = updater_module.Updater(settings)
    transaction = {
        "backup": str(settings.state_dir / "backup.tar.gz"),
        "pg_dump": str(settings.state_dir / "backup.pgdump"),
        "backup_complete": False,
        "old_image": "sha256:old-image",
        "old_version": "1.0.0",
    }
    updater.backup(transaction)
    (data / "history.db").write_bytes(b"partially migrated history")
    (data / "new-schema.txt").write_text("new schema", encoding="utf-8")
    monkeypatch.setattr(updater, "compose", lambda *args: "existing-container")
    monkeypatch.setattr(updater, "command", lambda *args, **kwargs: "sha256:old-image")
    monkeypatch.setattr(updater, "health", lambda *args, **kwargs: None)
    updater.recover(transaction)
    assert (data / "history.db").read_bytes() == b"original customer history"
    assert not (data / "new-schema.txt").exists()
    assert json.loads((data / "config.json").read_text("utf-8")) == {"timezone": "Asia/Shanghai"}
    failed = next(settings.state_dir.glob("failed-data-*"))
    assert (failed / "history.db").read_bytes() == b"partially migrated history"
    assert not (tmp_path / "data").exists()


def test_packaging_excludes_local_state_and_secrets(tmp_path: Path) -> None:
    packager = _load("package_release")
    source = tmp_path / "source"
    source.mkdir()
    for filename in packager.FILES:
        (source / filename).parent.mkdir(parents=True, exist_ok=True)
        (source / filename).write_text(
            '[project]\nversion="1.2.3"\n' if filename == "pyproject.toml" else "public source",
            encoding="utf-8",
        )
    for directory in ("src/octop", "dashboard", "docker"):
        (source / directory).mkdir(parents=True, exist_ok=True)
    (source / "src/octop/main.py").write_text("real implementation", encoding="utf-8")
    (source / "docker/.env").write_text("SECRET=not-for-release", encoding="utf-8")
    (source / "dashboard/node_modules").mkdir()
    (source / "dashboard/node_modules/private.js").write_text("local build", encoding="utf-8")
    (source / "src/octop/dashboard").mkdir()
    (source / "src/octop/dashboard/old.js").write_text("stale build", encoding="utf-8")
    (source / "customer.db").write_bytes(b"customer state")
    output = packager.package(source, tmp_path / "output", "amd64", "Bug fix")
    with zipfile.ZipFile(output) as archive:
        names = set(archive.namelist())
        assert "src/octop/main.py" in names
        assert names.isdisjoint(
            {
                "docker/.env",
                "dashboard/node_modules/private.js",
                "src/octop/dashboard/old.js",
                "customer.db",
            }
        )
        assert json.loads(archive.read("maitu-release.json"))["version"] == "1.2.3"


def test_native_prepare_rejects_checksum_without_changing_installation(
    tmp_path: Path, updater_module: Any
) -> None:
    archive = tmp_path / "release.zip"
    _archive(archive)
    home = tmp_path / "home"
    environment = tmp_path / "environment"
    home.mkdir()
    environment.mkdir()
    (home / "license-state.json").write_bytes(b"original identity")
    destination = tmp_path / "new-source"
    with pytest.raises(ValueError, match="checksum"):
        updater_module.prepare_native(archive, destination, environment, home, "0" * 64, [])
    assert not destination.exists()
    assert (home / "license-state.json").read_bytes() == b"original identity"


def test_native_prepare_never_overwrites_existing_source(
    tmp_path: Path, updater_module: Any
) -> None:
    archive = tmp_path / "release.zip"
    _archive(archive)
    home = tmp_path / "home"
    environment = tmp_path / "environment"
    home.mkdir()
    environment.mkdir()
    source = tmp_path / "source"
    source.mkdir()
    (source / "config.json").write_bytes(b"existing config")
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    with pytest.raises(ValueError, match="never overwritten"):
        updater_module.prepare_native(archive, source, environment, home, digest, [])
    assert (source / "config.json").read_bytes() == b"existing config"


# ---------------------------------------------------------------------------
# Native updater: release env, settings, build, ready heartbeat, transactions
# ---------------------------------------------------------------------------


def _keyring(tmp_path: Path) -> Path:
    private_key = Ed25519PrivateKey.generate()
    path = tmp_path / "release-trusted-keys.json"
    path.write_text(
        json.dumps(
            {
                "release-test": private_key.public_key()
                .public_bytes(
                    serialization.Encoding.PEM,
                    serialization.PublicFormat.SubjectPublicKeyInfo,
                )
                .decode("utf-8")
            }
        ),
        encoding="utf-8",
    )
    return path


def _native_environment(
    updater_module: Any, tmp_path: Path, **settings_overrides: Any
) -> tuple[Any, dict[str, Path]]:
    keyring = _keyring(tmp_path)
    updater_root = tmp_path / "updater-root"
    updater_root.mkdir()
    queue = tmp_path / "queue"
    state = tmp_path / "state"
    data = tmp_path / "data"
    data.mkdir()
    (data / "history.db").write_bytes(b"original customer history")
    old_root = tmp_path / "old-release"
    old_root.mkdir()
    (old_root / "pyproject.toml").write_text(
        '[project]\nname = "maitu"\nversion = "1.0.0"\n', encoding="utf-8"
    )
    old_venv = old_root / ".venv"
    old_venv.mkdir()
    (old_venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
    env_file = tmp_path / "etc" / "current-release.env"
    env_file.parent.mkdir()
    updater_module.write_release_env(
        env_file, updater_module.ReleaseEnvironment(old_root, old_venv)
    )
    values: dict[str, Any] = {
        "center_url": "https://center.example",
        "client_token": "test-token",
        "trusted_keys_file": keyring,
        "queue_dir": queue,
        "state_dir": state,
        "data_dir": data,
        "health_url": "http://127.0.0.1:8088/api/health",
        "release_env_file": env_file,
        "systemd_unit": "maitu.service",
        "updater_root": updater_root,
    }
    values.update(settings_overrides)
    settings = updater_module.NativeSettings(**values)
    paths = {
        "queue": queue,
        "state": state,
        "data": data,
        "old_root": old_root,
        "old_venv": old_venv,
        "env_file": env_file,
        "updater_root": updater_root,
    }
    return settings, paths


def _release_manifest(source_zip: Path) -> dict[str, Any]:
    return {
        "product": "maitu",
        "version": "1.2.3",
        "package_type": "source",
        "platform": "linux",
        "architecture": "amd64",
        "release_id": "a" * 32,
        "sha256": "c" * 64,
        "size": source_zip.stat().st_size,
        "filename": "maitu-1.2.3.zip",
    }


def _queue_request(paths: dict[str, Path], *, request_id: str = "b" * 32) -> dict[str, Any]:
    request = {"request_id": request_id, "version": "1.2.3", "release_id": "a" * 32}
    (paths["queue"] / "upgrade-request.json").write_text(json.dumps(request), encoding="utf-8")
    return request


def _patch_native_build(updater: Any, module: Any, monkeypatch: pytest.MonkeyPatch) -> list[Path]:
    built: list[Path] = []

    def build(_manifest: dict[str, Any], release: Path) -> Any:
        venv = release / ".venv"
        venv.mkdir()
        (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        built.append(release)
        return module.ReleaseEnvironment(release, venv)

    monkeypatch.setattr(updater, "build_release", build)
    return built


def test_release_env_parser_rejects_shell_injection_and_unknown_keys(
    tmp_path: Path, updater_module: Any
) -> None:
    root = tmp_path / "root"
    venv = tmp_path / "venv"
    parsed = updater_module.parse_release_env(
        f"MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\n"
    )
    assert parsed.root == root
    assert parsed.venv == venv
    for text in (
        f"MAITU_RELEASE_ROOT=$(touch {tmp_path}/pwned)\nMAITU_RELEASE_VENV={venv}\n",
        f"MAITU_RELEASE_ROOT={root}; rm -rf /\nMAITU_RELEASE_VENV={venv}\n",
        f"MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\nEVIL=1\n",
        f"MAITU_RELEASE_ROOT=relative/root\nMAITU_RELEASE_VENV={venv}\n",
        f"MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\n",
        f"export MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\n",
        f"MAITU_RELEASE_ROOT={root}\n",
        f"MAITU_RELEASE_ROOT={root}\nMAITU_RELEASE_VENV={venv}\nMAITU_RELEASE_VENV={venv}\n",
    ):
        with pytest.raises(ValueError):
            updater_module.parse_release_env(text)


def test_release_env_write_is_atomic_and_roundtrips(tmp_path: Path, updater_module: Any) -> None:
    env_file = tmp_path / "etc" / "current-release.env"
    env_file.parent.mkdir()
    first = updater_module.ReleaseEnvironment(
        tmp_path / "release-a", tmp_path / "release-a" / ".venv"
    )
    second = updater_module.ReleaseEnvironment(
        tmp_path / "release-b", tmp_path / "release-b" / ".venv"
    )
    updater_module.write_release_env(env_file, first)
    assert env_file.read_text(encoding="utf-8") == updater_module.render_release_env(first)
    assert updater_module.read_release_env(env_file) == first
    updater_module.write_release_env(env_file, second)
    assert updater_module.read_release_env(env_file) == second
    assert list(env_file.parent.glob(".*.tmp")) == []
    if os.name == "posix":
        assert stat.S_IMODE(env_file.stat().st_mode) & 0o022 == 0
    with pytest.raises(ValueError):
        updater_module.write_release_env(
            env_file,
            updater_module.ReleaseEnvironment(
                tmp_path / "release" / ".." / "escape", tmp_path / "release-venv"
            ),
        )


@pytest.mark.skipif(os.name != "posix", reason="POSIX permission semantics")
def test_release_env_rejects_insecure_permissions_and_symlinks(
    tmp_path: Path, updater_module: Any
) -> None:
    env_file = tmp_path / "etc" / "current-release.env"
    env_file.parent.mkdir()
    environment = updater_module.ReleaseEnvironment(tmp_path / "release", tmp_path / "venv")
    updater_module.write_release_env(env_file, environment)
    os.chmod(env_file, 0o666)
    with pytest.raises(ValueError, match="group/world-writable"):
        updater_module.read_release_env(env_file)
    os.chmod(env_file, 0o644)
    symlink = tmp_path / "linked.env"
    symlink.symlink_to(env_file)
    with pytest.raises(ValueError, match="symlink"):
        updater_module.read_release_env(symlink)


def test_native_settings_validate_rejects_unsafe_unit_and_shared_paths(
    tmp_path: Path, updater_module: Any
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    settings.validate()
    with pytest.raises(ValueError, match="unit"):
        dataclasses.replace(settings, systemd_unit="maitu.service; rm -rf /").validate()
    with pytest.raises(ValueError, match="separate directories"):
        dataclasses.replace(settings, state_dir=paths["data"]).validate()
    with pytest.raises(ValueError, match="native extra"):
        dataclasses.replace(settings, extras=("bad extra",)).validate()
    inside_data = paths["data"] / "current-release.env"
    inside_data.write_text(
        updater_module.render_release_env(
            updater_module.ReleaseEnvironment(paths["old_root"], paths["old_venv"])
        ),
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="separate from runtime paths"):
        dataclasses.replace(settings, release_env_file=inside_data).validate()


def test_native_settings_from_environment_defaults_and_overrides(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    required = {
        "MAITU_UPDATE_CENTER_URL": "https://center.example",
        "MAITU_UPDATE_CLIENT_TOKEN": "test-token",
        "MAITU_UPDATE_TRUSTED_KEYS_FILE": str(tmp_path / "keys.json"),
        "MAITU_UPDATE_QUEUE_DIR": str(tmp_path / "queue"),
        "MAITU_UPDATE_STATE_DIR": str(tmp_path / "state"),
        "MAITU_UPDATE_DATA_DIR": str(tmp_path / "data"),
        "MAITU_UPDATE_HEALTH_URL": "http://127.0.0.1:8088/api/health",
        "MAITU_UPDATE_RELEASE_ENV_FILE": str(tmp_path / "current-release.env"),
        "MAITU_UPDATE_SYSTEMD_UNIT": "maitu.service",
    }
    for name, value in required.items():
        monkeypatch.setenv(name, value)
    for name in (
        "MAITU_UPDATE_NATIVE_EXTRAS",
        "MAITU_UPDATE_NATIVE_UV",
        "MAITU_UPDATE_NATIVE_NPM",
        "MAITU_UPDATE_UPDATER_ROOT",
        "MAITU_UPDATE_ARCHITECTURE",
        "MAITU_UPDATE_POSTGRES_DSN",
    ):
        monkeypatch.delenv(name, raising=False)
    settings = updater_module.native_settings_from_environment()
    assert settings.extras == ("office", "knowledge-ocr")
    assert settings.uv == "uv"
    assert settings.npm == "npm"
    assert settings.systemd_unit == "maitu.service"
    assert settings.updater_root == Path(updater_module.__file__).resolve().parent

    monkeypatch.setenv("MAITU_UPDATE_NATIVE_EXTRAS", " office , local-embedding ,,office ")
    monkeypatch.setenv("MAITU_UPDATE_NATIVE_UV", "/opt/tools/uv")
    monkeypatch.setenv("MAITU_UPDATE_NATIVE_NPM", "/opt/tools/npm")
    settings = updater_module.native_settings_from_environment()
    assert settings.extras == ("office", "local-embedding")
    assert settings.uv == "/opt/tools/uv"
    assert settings.npm == "/opt/tools/npm"


def test_native_build_release_creates_independent_venv_and_builds(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, _paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    release = tmp_path / "new-release"
    release.mkdir()
    (release / "dashboard").mkdir()
    (release / "pyproject.toml").write_text(
        '[project]\nname = "maitu"\nversion = "1.2.3"\n'
        "[project.optional-dependencies]\noffice = []\nknowledge-ocr = []\n",
        encoding="utf-8",
    )
    commands: list[list[str]] = []
    environments: list[dict[str, str]] = []
    working_directories: list[Path] = []

    def fake_run(arguments: list[str], **kwargs: Any) -> Any:
        commands.append(list(arguments))
        environment = dict(kwargs.get("env") or {})
        environments.append(environment)
        working_directories.append(Path(kwargs["cwd"]))
        if arguments[:2] == ["uv", "sync"]:
            venv = Path(environment["UV_PROJECT_ENVIRONMENT"])
            venv.mkdir(parents=True, exist_ok=True)
            (venv / "pyvenv.cfg").write_text("home = /usr/bin\n", encoding="utf-8")
        return updater_module.subprocess.CompletedProcess(arguments, 0, "", "")

    monkeypatch.setattr(updater_module, "_resolve_native_command", lambda value, _label: value)
    monkeypatch.setattr(updater_module.subprocess, "run", fake_run)
    monkeypatch.setenv("MAITU_UPDATE_CLIENT_TOKEN", "must-not-leak")
    monkeypatch.setenv("MAITU_UPDATE_POSTGRES_DSN", "postgresql://secret")

    environment = updater.build_release({"version": "1.2.3"}, release)

    assert environment.root == release
    assert environment.venv == release / ".venv"
    assert (release / ".venv" / "pyvenv.cfg").is_file()
    assert commands[0][:2] == ["uv", "sync"]
    assert "--frozen" in commands[0] and "--no-dev" in commands[0]
    assert "--extra" in commands[0] and "office" in commands[0]
    assert ["npm", "ci"] in commands
    assert ["npm", "run", "build"] in commands
    assert all(tmp_path / "old-release" not in path.parents for path in working_directories)
    assert environments[0]["UV_PROJECT_ENVIRONMENT"] == str(release / ".venv")
    assert "MAITU_UPDATE_CLIENT_TOKEN" not in environments[0]
    assert "MAITU_UPDATE_POSTGRES_DSN" not in environments[0]


def test_native_ready_heartbeat_is_written_on_startup_and_each_round(
    tmp_path: Path, updater_module: Any
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    heartbeat = paths["queue"] / "updater-ready.json"
    record = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert record["mode"] == "native"
    assert record["ready"] is True
    assert isinstance(record["pid"], int)
    assert isinstance(record["updated_at"], str)
    assert record["unit"] == "maitu.service"
    assert "token" not in heartbeat.read_text(encoding="utf-8").lower()
    heartbeat.unlink()
    assert updater.process() is False
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["mode"] == "native"


def test_docker_updater_ready_heartbeat_is_written_on_startup_and_round(
    tmp_path: Path, updater_module: Any
) -> None:
    settings = updater_module.Settings(
        "https://center.example",
        "test-token",
        _keyring(tmp_path),
        tmp_path / "queue",
        _docker_project(tmp_path),
        _docker_data(tmp_path),
        tmp_path / "state",
        "http://127.0.0.1:8088/api/health",
        _docker_compose(tmp_path),
    )
    updater = updater_module.Updater(settings)
    heartbeat = tmp_path / "queue" / "updater-ready.json"
    record = json.loads(heartbeat.read_text(encoding="utf-8"))
    assert record["mode"] == "docker"
    assert record["ready"] is True
    assert record["unit"] is None
    heartbeat.unlink()
    assert updater.process() is False
    assert json.loads(heartbeat.read_text(encoding="utf-8"))["mode"] == "docker"


def _docker_project(tmp_path: Path) -> Path:
    project = tmp_path / "checkout"
    project.mkdir(exist_ok=True)
    return project


def _docker_compose(tmp_path: Path) -> Path:
    compose = _docker_project(tmp_path) / "compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")
    return compose


def _docker_data(tmp_path: Path) -> Path:
    data = tmp_path / "customer-data"
    data.mkdir(exist_ok=True)
    return data


def test_native_process_switches_release_env_after_healthy_start(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    source_zip = tmp_path / "release.zip"
    _archive(source_zip)
    manifest = _release_manifest(source_zip)
    _queue_request(paths)
    built = _patch_native_build(updater, updater_module, monkeypatch)
    health: list[str] = []
    actions: list[str] = []
    monkeypatch.setattr(updater, "manifest", lambda: manifest)
    monkeypatch.setattr(
        updater, "download", lambda _manifest, archive: shutil.copyfile(source_zip, archive)
    )
    monkeypatch.setattr(
        updater, "health", lambda version, require_version=True: health.append(version)
    )
    monkeypatch.setattr(updater, "systemctl", actions.append)

    assert updater.process() is True

    assert actions == ["stop", "start"]
    assert health == ["1.0.0", "1.2.3"]
    environment = updater_module.read_release_env(paths["env_file"])
    assert environment.root == built[0]
    assert environment.venv == built[0] / ".venv"
    assert not (paths["state"] / "native-transaction.json").exists()
    assert not (paths["queue"] / "upgrade-request.json").exists()
    state = json.loads((paths["queue"] / "upgrade-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "succeeded"
    assert (paths["data"] / "history.db").read_bytes() == b"original customer history"


def test_native_failure_rolls_back_data_and_release_env(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    source_zip = tmp_path / "release.zip"
    _archive(source_zip)
    manifest = _release_manifest(source_zip)
    _queue_request(paths)
    _patch_native_build(updater, updater_module, monkeypatch)
    health: list[str] = []
    actions: list[str] = []

    def health_check(version: str, require_version: bool = True) -> None:
        health.append(version)
        if version == "1.2.3":
            raise RuntimeError("target release never became healthy")

    original_switch = updater.switch_release_env

    def switch_and_mutate(environment: Any) -> None:
        original_switch(environment)
        (paths["data"] / "history.db").write_bytes(b"partially migrated history")
        (paths["data"] / "new-schema.txt").write_text("new schema", encoding="utf-8")

    monkeypatch.setattr(updater, "manifest", lambda: manifest)
    monkeypatch.setattr(
        updater, "download", lambda _manifest, archive: shutil.copyfile(source_zip, archive)
    )
    monkeypatch.setattr(updater, "health", health_check)
    monkeypatch.setattr(updater, "systemctl", actions.append)
    monkeypatch.setattr(updater, "switch_release_env", switch_and_mutate)

    assert updater.process() is True

    assert actions == ["stop", "start", "stop", "start"]
    assert health == ["1.0.0", "1.2.3", "1.0.0"]
    environment = updater_module.read_release_env(paths["env_file"])
    assert environment.root == paths["old_root"]
    assert environment.venv == paths["old_venv"]
    assert (paths["data"] / "history.db").read_bytes() == b"original customer history"
    assert not (paths["data"] / "new-schema.txt").exists()
    failed = next(paths["state"].glob("failed-data-*"))
    assert (failed / "history.db").read_bytes() == b"partially migrated history"
    assert not (paths["state"] / "native-transaction.json").exists()
    assert not (paths["queue"] / "upgrade-request.json").exists()
    state = json.loads((paths["queue"] / "upgrade-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "failed"


def test_native_refuses_to_start_when_old_health_version_differs(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    actions: list[str] = []
    monkeypatch.setattr(updater, "systemctl", actions.append)
    monkeypatch.setattr(
        updater,
        "health",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(RuntimeError("wrong version")),
    )
    release = tmp_path / "prepared-release"
    release.mkdir()
    with pytest.raises(ValueError, match="health"):
        updater.installation({"version": "1.2.3"}, release)
    assert actions == []
    environment = updater_module.read_release_env(paths["env_file"])
    assert environment.root == paths["old_root"]
    assert not (paths["state"] / "native-transaction.json").exists()


def test_native_interrupted_journal_restores_old_release_and_data(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    transaction: dict[str, Any] = {
        "request": {"request_id": "d" * 32, "version": "1.2.3", "release_id": "a" * 32},
        "old_env": {"root": str(paths["old_root"]), "venv": str(paths["old_venv"])},
        "old_version": "1.0.0",
        "target_version": "1.2.3",
        "backup": str(paths["state"] / "native-backup.tar.gz"),
        "pg_dump": str(paths["state"] / "native-backup.pgdump"),
        "backup_complete": False,
    }
    updater.request = transaction["request"]
    updater.backup(transaction)
    new_root = tmp_path / "new-release"
    new_venv = new_root / ".venv"
    new_venv.mkdir(parents=True)
    updater.switch_release_env(updater_module.ReleaseEnvironment(new_root, new_venv))
    (paths["data"] / "history.db").write_bytes(b"partially migrated history")
    _queue_request(paths, request_id="d" * 32)
    actions: list[str] = []
    health: list[str] = []
    monkeypatch.setattr(updater, "systemctl", actions.append)
    monkeypatch.setattr(
        updater, "health", lambda version, require_version=True: health.append(version)
    )

    assert updater.process() is True

    assert actions == ["stop", "start"]
    assert health == ["1.0.0"]
    environment = updater_module.read_release_env(paths["env_file"])
    assert environment.root == paths["old_root"]
    assert (paths["data"] / "history.db").read_bytes() == b"original customer history"
    assert list(paths["state"].glob("failed-data-*"))
    assert not (paths["state"] / "native-transaction.json").exists()
    assert not (paths["queue"] / "upgrade-request.json").exists()
    state = json.loads((paths["queue"] / "upgrade-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "failed"
    assert "Interrupted native upgrade" in state["error"]


def test_native_process_failure_before_switch_marks_failed_and_removes_request(
    tmp_path: Path, updater_module: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings, paths = _native_environment(updater_module, tmp_path)
    updater = updater_module.NativeUpdater(settings)
    _queue_request(paths)
    actions: list[str] = []
    monkeypatch.setattr(updater, "systemctl", actions.append)
    monkeypatch.setattr(
        updater,
        "manifest",
        lambda: (_ for _ in ()).throw(ValueError("no published compatible release")),
    )

    assert updater.process() is True

    state = json.loads((paths["queue"] / "upgrade-state.json").read_text(encoding="utf-8"))
    assert state["phase"] == "failed"
    assert "no published compatible release" in state["error"]
    assert actions == []
    assert not (paths["queue"] / "upgrade-request.json").exists()
    assert (paths["data"] / "history.db").read_bytes() == b"original customer history"
