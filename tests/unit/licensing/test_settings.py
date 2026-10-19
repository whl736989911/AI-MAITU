from __future__ import annotations

import json
from pathlib import Path

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.infra.licensing.settings import (
    load_license_settings,
    normalize_center_url,
    parse_trusted_keys,
    validate_ca_pem,
    write_license_files,
)
from octop.infra.utils.paths import PathLayout


def _keyring() -> str:
    key = Ed25519PrivateKey.generate().public_key()
    pem = key.public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
    ).decode()
    return json.dumps({"kid-1": pem})


@pytest.fixture
def paths(tmp_path: Path) -> PathLayout:
    return PathLayout(tmp_path)


def test_normalize_center_url_accepts_https_and_strips_trailing_slash() -> None:
    assert (
        normalize_center_url("https://111.229.110.180:8091/")
        == "https://111.229.110.180:8091"
    )


@pytest.mark.parametrize(
    "value",
    [
        "http://license.example.test",
        "https://license.example.test/api",
        "https://user:pass@license.example.test",
        "https://license.example.test/?a=1",
    ],
)
def test_normalize_center_url_rejects_non_root_or_insecure_urls(value: str) -> None:
    with pytest.raises(ValueError):
        normalize_center_url(value)


def test_parse_trusted_keys_requires_ed25519_public_keys() -> None:
    assert "kid-1" in parse_trusted_keys(_keyring())
    with pytest.raises(ValueError):
        parse_trusted_keys('{"kid-1": "not-a-key"}')
    with pytest.raises(ValueError):
        parse_trusted_keys("[]")


def test_validate_ca_rejects_non_pem() -> None:
    with pytest.raises(ValueError):
        validate_ca_pem("not a certificate")


def test_write_and_load_license_settings_round_trip(paths: PathLayout) -> None:
    settings = write_license_files(
        paths,
        trusted_keys_json=_keyring(),
        ca_pem=None,
        center_url="https://license.example.test/",
        allow_loopback=False,
    )
    assert settings.center_url == "https://license.example.test"
    assert settings.trusted_keys_file == paths.root / "license" / "trusted-keys.json"
    assert settings.ca_file is None
    assert settings.state_dir == paths.root / "license" / "state"
    assert settings.source == "config"
    assert settings.trusted_keys_file.read_text(encoding="utf-8").startswith("{")

    raw = json.loads(paths.config.read_text(encoding="utf-8"))
    assert raw["license"]["center_url"] == "https://license.example.test"
    assert raw["license"]["trusted_keys_file"] == "license/trusted-keys.json"
    assert raw["license"]["state_dir"] == "license/state"

    loaded = load_license_settings(paths, default_state_dir=paths.root / ".license")
    assert loaded == settings


def test_empty_keyring_clears_managed_state(paths: PathLayout) -> None:
    first = write_license_files(
        paths,
        trusted_keys_json=_keyring(),
        ca_pem=None,
        center_url="https://license.example.test",
        allow_loopback=False,
    )
    assert first.trusted_keys_file is not None

    second = write_license_files(
        paths,
        trusted_keys_json="",
        ca_pem=None,
        center_url="https://license.example.test",
        allow_loopback=False,
    )
    assert second.trusted_keys_file is None
    assert not (paths.root / "license" / "trusted-keys.json").exists()


def test_env_is_used_when_no_config_exists(
    paths: PathLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    keyring = paths.root / "env-keys.json"
    keyring.write_text(_keyring(), encoding="utf-8")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(keyring))
    monkeypatch.setenv("MAITU_LICENSE_CENTER_URL", "https://env.example.test")
    settings = load_license_settings(paths, default_state_dir=paths.root / ".license")
    assert settings.source == "env"
    assert settings.trusted_keys_file == keyring
    assert settings.center_url == "https://env.example.test"


def test_save_does_not_move_an_env_managed_instance_identity(
    paths: PathLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("MAITU_LICENSE_STATE_DIR", str(paths.root / "protected-state"))
    settings = write_license_files(
        paths,
        trusted_keys_json=_keyring(),
        ca_pem=None,
        center_url="https://license.example.test",
        allow_loopback=False,
    )
    raw = json.loads(paths.config.read_text(encoding="utf-8"))
    assert "state_dir" not in raw["license"]
    assert settings.state_dir == paths.root / "protected-state"
