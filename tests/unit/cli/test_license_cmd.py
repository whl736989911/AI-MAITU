from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.cli.commands.license import _manager
from octop.infra.licensing import LicenseManager, install_license_manager


@pytest.fixture(autouse=True)
def _reset_license_singleton(tmp_path: Path):
    yield
    install_license_manager(LicenseManager(tmp_path / "reset-state", None))


def _public_key() -> str:
    key = Ed25519PrivateKey.generate().public_key()
    return key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()


def test_cli_manager_reads_wizard_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path))
    monkeypatch.delenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", raising=False)
    monkeypatch.delenv("MAITU_LICENSE_CENTER_URL", raising=False)
    monkeypatch.delenv("MAITU_LICENSE_STATE_DIR", raising=False)
    keys = tmp_path / "license" / "trusted-keys.json"
    keys.parent.mkdir()
    keys.write_text(json.dumps({"kid-1": _public_key()}), encoding="utf-8")
    (tmp_path / "config.json").write_text(
        json.dumps(
            {
                "license": {
                    "trusted_keys_file": "license/trusted-keys.json",
                    "state_dir": "license/state",
                    "center_url": "https://license.example.test",
                }
            }
        ),
        encoding="utf-8",
    )

    manager = _manager()

    assert manager.managed is True
    assert manager.trusted_keys_file == keys
    assert manager.center_url == "https://license.example.test"
