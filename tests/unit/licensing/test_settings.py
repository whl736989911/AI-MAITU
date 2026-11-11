from __future__ import annotations

import json
import time
from datetime import UTC, datetime
from pathlib import Path

import jwt
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.x509.oid import NameOID

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
    assert normalize_center_url("https://111.229.110.180:8091/") == "https://111.229.110.180:8091"


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


def test_none_keeps_existing_external_trust_paths(paths: PathLayout) -> None:
    external_keys = paths.root / "external" / "keys.json"
    external_ca = paths.root / "external" / "ca.pem"
    external_keys.parent.mkdir(parents=True)
    external_keys.write_text(_keyring(), encoding="utf-8")
    paths.config.write_text(
        json.dumps(
            {
                "license": {
                    "center_url": "https://old.example.test",
                    "trusted_keys_file": str(external_keys),
                    "ca_file": str(external_ca),
                    "state_dir": str(paths.root / "old-state"),
                }
            }
        ),
        encoding="utf-8",
    )
    write_license_files(
        paths,
        trusted_keys_json=None,
        ca_pem=None,
        center_url="https://new.example.test",
        allow_loopback=False,
    )
    raw = json.loads(paths.config.read_text(encoding="utf-8"))["license"]
    assert raw["trusted_keys_file"] == str(external_keys)
    assert raw["ca_file"] == str(external_ca)
    assert raw["state_dir"] == str(paths.root / "old-state")
    assert raw["center_url"] == "https://new.example.test"


def test_malformed_license_section_fails_closed(paths: PathLayout) -> None:
    paths.config.write_text(
        json.dumps({"license": "not-an-object"}),
        encoding="utf-8",
    )
    with pytest.raises(ValueError):
        load_license_settings(paths, default_state_dir=paths.root / ".license")


def _public_ca_and_private_key() -> tuple[Ed25519PrivateKey, str, str]:
    key = Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Local test CA")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime(2025, 1, 1, tzinfo=UTC))
        .not_valid_after(datetime(2035, 1, 1, tzinfo=UTC))
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .sign(key, algorithm=None)
    )
    return (
        key,
        certificate.public_bytes(serialization.Encoding.PEM).decode(),
        key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        ).decode(),
    )


@pytest.mark.parametrize("field", ["trusted_keys_json", "ca_pem", "ca_extra_text"])
def test_public_trust_rejects_embedded_private_keys_without_mutation(
    paths: PathLayout, field: str
) -> None:
    key, certificate, private_pem = _public_ca_and_private_key()
    initial = write_license_files(
        paths,
        trusted_keys_json=_keyring(),
        ca_pem=None,
        center_url="https://original.example.test",
        allow_loopback=False,
    )
    assert initial.trusted_keys_file is not None
    config_before = paths.config.read_bytes()
    keys_before = initial.trusted_keys_file.read_bytes()
    public_pem = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    ca_input = None
    if field == "ca_pem":
        ca_input = certificate + private_pem
    elif field == "ca_extra_text":
        ca_input = certificate + private_pem.splitlines()[1]
    supplied = {
        "trusted_keys_json": json.dumps({"kid": public_pem + private_pem})
        if field == "trusted_keys_json"
        else _keyring(),
        "ca_pem": ca_input,
    }
    with pytest.raises(ValueError):
        write_license_files(
            paths,
            **supplied,
            center_url="https://replacement.example.test",
            allow_loopback=False,
        )
    assert paths.config.read_bytes() == config_before
    assert initial.trusted_keys_file.read_bytes() == keys_before
    assert not (paths.root / "license" / "root-ca.pem").exists()


@pytest.mark.parametrize("field", ["ca_pem", "center_url"])
def test_explicit_clear_overrides_env_but_preserves_external_files(
    paths: PathLayout, monkeypatch: pytest.MonkeyPatch, field: str
) -> None:
    _, certificate, _ = _public_ca_and_private_key()
    env_keys = paths.root / "external-keys.json"
    env_ca = paths.root / "external-ca.pem"
    keys = _keyring()
    env_keys.write_text(keys, encoding="utf-8")
    env_ca.write_text(certificate, encoding="utf-8")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(env_keys))
    monkeypatch.setenv("MAITU_LICENSE_CA_FILE", str(env_ca))
    monkeypatch.setenv("MAITU_LICENSE_CENTER_URL", "https://environment.example.test")
    write_license_files(
        paths,
        trusted_keys_json=None,
        ca_pem="" if field == "ca_pem" else None,
        center_url="" if field == "center_url" else None,
        allow_loopback=False,
    )
    loaded = load_license_settings(paths, default_state_dir=paths.root / ".license")
    assert loaded.trusted_keys_file == env_keys
    assert loaded.ca_file == (None if field == "ca_pem" else env_ca)
    assert loaded.center_url == (
        None if field == "center_url" else "https://environment.example.test"
    )
    assert env_keys.read_text(encoding="utf-8") == keys
    assert env_ca.read_text(encoding="utf-8") == certificate


@pytest.mark.parametrize("field", ["state_dir", "ca_file", "section", "center_url"])
def test_invalid_preserved_settings_do_not_partially_replace_trust(
    paths: PathLayout, field: str
) -> None:
    initial = write_license_files(
        paths,
        trusted_keys_json=_keyring(),
        ca_pem=None,
        center_url="https://original.example.test",
        allow_loopback=False,
    )
    assert initial.trusted_keys_file is not None
    invalid = json.loads(paths.config.read_text(encoding="utf-8"))
    if field == "section":
        invalid["license"] = "not-an-object"
    elif field == "center_url":
        invalid["license"][field] = "https://user:secret@original.example.test"
    else:
        invalid["license"][field] = 123
    paths.config.write_text(json.dumps(invalid), encoding="utf-8")
    config_before = paths.config.read_bytes()
    keys_before = initial.trusted_keys_file.read_bytes()
    with pytest.raises(ValueError):
        write_license_files(
            paths,
            trusted_keys_json=_keyring(),
            ca_pem=None,
            center_url=None if field == "center_url" else "https://replacement.example.test",
            allow_loopback=False,
        )
    assert paths.config.read_bytes() == config_before
    assert initial.trusted_keys_file.read_bytes() == keys_before


def test_duplicate_signing_key_ids_are_rejected() -> None:
    first = json.loads(_keyring())["kid-1"]
    second = json.loads(_keyring())["kid-1"]
    ambiguous = '{"kid":' + json.dumps(first) + ',"kid":' + json.dumps(second) + "}"
    with pytest.raises(ValueError):
        parse_trusted_keys(ambiguous)


@pytest.mark.asyncio
async def test_opaque_signing_key_id_survives_configuration(paths: PathLayout) -> None:
    from octop.infra.licensing import LicenseManager

    key, certificate, _ = _public_ca_and_private_key()
    kid = " spaced-key-id "
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo)
        .decode()
    )
    settings = write_license_files(
        paths,
        trusted_keys_json=json.dumps({kid: public}),
        ca_pem=certificate,
        center_url="https://license.example.test",
        allow_loopback=False,
    )
    manager = LicenseManager(
        settings.state_dir, settings.trusted_keys_file, ca_file=settings.ca_file
    )
    now = int(time.time())
    token = jwt.encode(
        {
            "protocol": 1,
            "product": "maitu",
            "iss": "maitu-license-center",
            "aud": "maitu-instance",
            "purpose": "offline-license",
            "instance_id": manager.instance_id,
            "key_fingerprint": manager._identity,
            "license_id": "d" * 32,
            "license_version": 1,
            "status": "active",
            "restored": False,
            "issued_at": now,
            "not_before": now,
            "valid_until": now + 3600,
            "license_expires_at": now + 3600,
        },
        key,
        algorithm="EdDSA",
        headers={"kid": kid, "typ": "JWT"},
    )
    try:
        assert (await manager.import_offline(token))["business_allowed"] is True
    finally:
        await manager.stop()


def test_clearing_keyring_override_keeps_deployment_managed(
    paths: PathLayout, monkeypatch: pytest.MonkeyPatch
) -> None:
    from octop.infra.errors import ErrorCode, OctopError
    from octop.infra.licensing import LicenseManager

    env_keys = paths.root / "deployment-keys.json"
    public_keys = _keyring()
    env_keys.write_text(public_keys, encoding="utf-8")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(env_keys))
    settings = write_license_files(
        paths,
        trusted_keys_json="",
        ca_pem=None,
        center_url="",
        allow_loopback=False,
    )
    manager = LicenseManager(settings.state_dir, settings.trusted_keys_file)
    assert manager.managed is True
    with pytest.raises(OctopError) as locked:
        manager.require_business_access()
    assert locked.value.code is ErrorCode.INSTANCE_LICENSE_LOCKED
    assert env_keys.read_text(encoding="utf-8") == public_keys
