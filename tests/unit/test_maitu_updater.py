"""Source archive boundaries and customer-data rollback invariants."""

from __future__ import annotations

import hashlib
import importlib.util
import json
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
