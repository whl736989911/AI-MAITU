"""Deployment configuration updates preserve recoverability and refuse ambiguous state."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.deployment.common import (
    DeploymentError,
    atomic_write,
    configuration_transaction,
    ensure_directory,
    load_manifest,
)


def test_interrupted_configuration_replace_preserves_original(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config = tmp_path / "center.env"
    original = "LICENSE_CENTER_DATA='/srv/original'\n"
    config.write_text(original, encoding="utf-8")

    def interrupted(source: object, destination: object) -> None:
        raise OSError("simulated storage interruption")

    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError):
        atomic_write(config, "LICENSE_CENTER_DATA='/srv/wrong'\n")
    assert config.read_text(encoding="utf-8") == original
    backups = list(tmp_path.glob("center.env.backup-*"))
    assert [path.read_text(encoding="utf-8") for path in backups] == [original]
    assert not list(tmp_path.glob(".center.env-*"))


def test_repeat_same_configuration_preserves_recovery_snapshot(tmp_path: Path) -> None:
    config = tmp_path / "platform.env"
    old = "OCTOP_DATA='/srv/customer-data'\n"
    new = "OCTOP_DATA='/srv/customer-data'\nOCTOP_LICENSE_SERVER_URL='https://192.0.2.10'\n"
    config.write_text(old, encoding="utf-8")
    atomic_write(config, new)
    atomic_write(config, new)
    assert config.read_text(encoding="utf-8") == new
    assert [
        path.read_text(encoding="utf-8") for path in tmp_path.glob("platform.env.backup-*")
    ] == [old]


def test_invalid_deployment_record_is_not_treated_as_first_install(tmp_path: Path) -> None:
    (tmp_path / "center.json").write_text("{broken", encoding="utf-8")
    with pytest.raises(DeploymentError, match="损坏"):
        load_manifest(tmp_path, "center")


@pytest.mark.skipif(
    os.name != "posix", reason="Symlink file protections are POSIX deployment semantics"
)
def test_symlink_cannot_redirect_root_configuration_write(tmp_path: Path) -> None:
    original = tmp_path / "operator.env"
    original.write_text("preserve unrelated configuration", encoding="utf-8")
    linked = tmp_path / "center.env"
    linked.symlink_to(original)
    with pytest.raises(DeploymentError, match="符号链接"):
        atomic_write(linked, "overwrite")
    assert original.read_text(encoding="utf-8") == "preserve unrelated configuration"


def test_deployment_cannot_repermission_system_directory() -> None:
    root = Path(Path.cwd().anchor)
    with pytest.raises(DeploymentError, match="系统根目录"):
        ensure_directory(root)


@pytest.mark.skipif(os.name != "posix", reason="Deployment locking uses POSIX flock")
def test_second_setup_cannot_replace_configuration_during_active_setup(tmp_path: Path) -> None:
    from scripts.deployment.common import deployment_lock

    with (
        deployment_lock(tmp_path),
        pytest.raises(DeploymentError, match="另一个部署向导"),
        deployment_lock(tmp_path),
    ):
        pytest.fail("A concurrent setup acquired the same configuration lock")
    with deployment_lock(tmp_path):
        atomic_write(tmp_path / "center.env", "resume after the first setup exits")
    assert (tmp_path / "center.env").read_text(
        encoding="utf-8"
    ) == "resume after the first setup exits"


def test_failed_configuration_validation_restores_old_trust_and_removes_new_manifest(
    tmp_path: Path,
) -> None:
    trust = tmp_path / "trusted-keys.json"
    original = '{"original": "public key"}\n'
    trust.write_text(original, encoding="utf-8")
    manifest = tmp_path / "platform.json"
    with (
        pytest.raises(DeploymentError, match="invalid Compose"),
        configuration_transaction([trust, manifest]),
    ):
        atomic_write(trust, '{"replacement": "different key"}\n')
        atomic_write(manifest, '{"role": "platform"}\n')
        raise DeploymentError("invalid Compose")
    assert trust.read_text(encoding="utf-8") == original
    assert not manifest.exists()
    assert [
        path.read_text(encoding="utf-8") for path in tmp_path.glob("trusted-keys.json.backup-*")
    ] == [original]


def test_parent_traversal_cannot_repermission_an_ancestor(tmp_path: Path) -> None:
    original_mode = tmp_path.stat().st_mode
    with pytest.raises(DeploymentError, match="父目录遍历"):
        ensure_directory(tmp_path / "child" / "..")
    assert tmp_path.stat().st_mode == original_mode
