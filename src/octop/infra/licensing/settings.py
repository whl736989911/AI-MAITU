"""Persist license-center trust settings without exposing private material."""

from __future__ import annotations

import json
import os
import ssl
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from octop.infra.utils.paths import PathLayout

LICENSE_DIRNAME = "license"
TRUSTED_KEYS_FILENAME = "trusted-keys.json"
ROOT_CA_FILENAME = "root-ca.pem"
STATE_DIRNAME = "state"
MAX_TRUSTED_KEYS = 128
DEFAULT_LICENSE_CENTER_URL = "https://111.229.110.180:8091"


@dataclass(frozen=True)
class LicenseSettings:
    center_url: str | None
    trusted_keys_file: Path | None
    ca_file: Path | None
    state_dir: Path
    allow_loopback: bool
    source: Literal["config", "env", "files", "none"]


def _read_raw_config(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ValueError(f"cannot read {path}: {exc}") from exc
    except ValueError as exc:
        raise ValueError(f"{path} is not valid JSON") from exc
    if not isinstance(data, dict):
        raise ValueError(f"{path} must contain a JSON object")
    return data


def _resolve(root: Path, value: object) -> Path | None:
    if not isinstance(value, str) or not value.strip():
        return None
    candidate = Path(value.strip()).expanduser()
    return candidate if candidate.is_absolute() else root / candidate


def _env(name: str) -> str:
    return os.environ.get(name, "").strip()


def license_dir(paths: PathLayout) -> Path:
    return paths.root / LICENSE_DIRNAME


def write_license_files(
    paths: PathLayout,
    *,
    trusted_keys_json: str | None,
    ca_pem: str | None,
    center_url: str | None,
    allow_loopback: bool,
) -> LicenseSettings:
    """Validate and persist the public trust material plus ``config.json``.

    ``None`` leaves an existing value untouched; an empty string clears it.
    The private instance identity is created by ``LicenseManager`` under the
    protected state directory and is never accepted through this API.
    """
    directory = license_dir(paths)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name != "nt":
        os.chmod(directory, 0o700)
    keys_path = directory / TRUSTED_KEYS_FILENAME
    ca_path = directory / ROOT_CA_FILENAME

    normalized_url = (
        normalize_center_url(center_url, allow_loopback=allow_loopback)
        if center_url is not None
        else None
    )
    parsed_keys: dict[str, str] | None = None
    normalized_ca: str | None = None
    if trusted_keys_json is not None and trusted_keys_json.strip():
        parsed_keys = parse_trusted_keys(trusted_keys_json)
    if ca_pem is not None and ca_pem.strip():
        validate_ca_pem(ca_pem)
        normalized_ca = ca_pem.strip() + "\n"

    # Read and validate the existing config before mutating trust files, so a
    # broken config cannot leave a half-written keyring/CA behind.
    data = _read_raw_config(paths.config)
    current = data.get("license")
    license_cfg: dict[str, Any] = dict(current) if isinstance(current, dict) else {}

    if trusted_keys_json is not None:
        if parsed_keys is not None:
            _atomic_write(keys_path, json.dumps(parsed_keys, ensure_ascii=False, indent=2) + "\n")
        else:
            keys_path.unlink(missing_ok=True)

    if ca_pem is not None:
        if normalized_ca is not None:
            _atomic_write(ca_path, normalized_ca)
        else:
            ca_path.unlink(missing_ok=True)

    if center_url is not None:
        if normalized_url:
            license_cfg["center_url"] = normalized_url
        else:
            license_cfg.pop("center_url", None)
    if trusted_keys_json is not None:
        if parsed_keys is not None:
            license_cfg["trusted_keys_file"] = f"{LICENSE_DIRNAME}/{TRUSTED_KEYS_FILENAME}"
        else:
            license_cfg.pop("trusted_keys_file", None)
    if ca_pem is not None:
        if normalized_ca is not None:
            license_cfg["ca_file"] = f"{LICENSE_DIRNAME}/{ROOT_CA_FILENAME}"
        else:
            license_cfg.pop("ca_file", None)
    # Never silently move an existing instance identity. Keep a configured or
    # env-provided state directory; only new, unmanaged installs get the
    # in-home default.
    if "state_dir" not in license_cfg and not _env("MAITU_LICENSE_STATE_DIR"):
        license_cfg["state_dir"] = f"{LICENSE_DIRNAME}/{STATE_DIRNAME}"
    license_cfg["allow_loopback"] = bool(allow_loopback)
    data["license"] = license_cfg
    _atomic_json_write(paths.config, data)
    return load_license_settings(paths, default_state_dir=directory / STATE_DIRNAME)


def load_license_settings(paths: PathLayout, *, default_state_dir: Path) -> LicenseSettings:
    """Resolve config values with env fallback for existing deployments.

    A value written by the settings UI wins over the process environment, so an
    administrator can change the center from the application. Unconfigured
    installs retain the old env-only behavior.
    """
    raw = _read_raw_config(paths.config)
    raw_license = raw.get("license")
    if raw_license is not None and not isinstance(raw_license, dict):
        raise ValueError("config.license must be a JSON object")
    cfg: dict[str, Any] = raw_license if isinstance(raw_license, dict) else {}
    for field in ("center_url", "trusted_keys_file", "ca_file", "state_dir"):
        value = cfg.get(field)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"config.license.{field} must be a string")
    if "allow_loopback" in cfg and not isinstance(cfg["allow_loopback"], bool):
        raise ValueError("config.license.allow_loopback must be a boolean")

    center_url = cfg.get("center_url") or _env("MAITU_LICENSE_CENTER_URL") or None
    keys_path = _resolve(paths.root, cfg.get("trusted_keys_file")) or _resolve(
        paths.root, _env("MAITU_LICENSE_TRUSTED_KEYS_FILE")
    )
    ca_path = _resolve(paths.root, cfg.get("ca_file")) or _resolve(
        paths.root, _env("MAITU_LICENSE_CA_FILE")
    )
    default_keys = license_dir(paths) / TRUSTED_KEYS_FILENAME
    default_ca = license_dir(paths) / ROOT_CA_FILENAME
    if keys_path is None and default_keys.is_file():
        keys_path = default_keys
    if ca_path is None and default_ca.is_file():
        ca_path = default_ca

    state_path = _resolve(paths.root, cfg.get("state_dir")) or _resolve(
        paths.root, _env("MAITU_LICENSE_STATE_DIR")
    )
    if state_path is None:
        state_path = (
            license_dir(paths) / STATE_DIRNAME
            if cfg or keys_path is not None
            else default_state_dir
        )

    allow_loopback = cfg.get("allow_loopback")
    if allow_loopback is None:
        allow_loopback = _env("MAITU_LICENSE_ALLOW_LOOPBACK").lower() in {
            "1",
            "true",
            "yes",
        }

    if cfg:
        source: Literal["config", "env", "files", "none"] = "config"
    elif any(
        _env(name)
        for name in (
            "MAITU_LICENSE_CENTER_URL",
            "MAITU_LICENSE_TRUSTED_KEYS_FILE",
            "MAITU_LICENSE_STATE_DIR",
        )
    ):
        source = "env"
    elif keys_path is not None:
        source = "files"
    else:
        source = "none"
    return LicenseSettings(
        center_url=str(center_url) if center_url else None,
        trusted_keys_file=keys_path,
        ca_file=ca_path,
        state_dir=state_path,
        allow_loopback=bool(allow_loopback),
        source=source,
    )


def normalize_center_url(raw: str, *, allow_loopback: bool = False) -> str:
    value = raw.strip().rstrip("/")
    if not value:
        raise ValueError("center URL must not be empty")
    parsed = urlsplit(value)
    hostname = parsed.hostname
    if not hostname:
        raise ValueError("center URL must include a host")
    if parsed.username is not None or parsed.password is not None or "@" in parsed.netloc:
        raise ValueError("center URL must not contain credentials")
    if parsed.query or parsed.fragment:
        raise ValueError("center URL must be a root URL without query or fragment")
    if parsed.path not in ("", "/"):
        raise ValueError("center URL must be a root URL without a path")
    if parsed.scheme.lower() != "https":
        loopback = hostname in {"localhost", "127.0.0.1", "::1"}
        if not (allow_loopback and loopback and parsed.scheme.lower() == "http"):
            raise ValueError("center URL must use HTTPS")
    return value


def parse_trusted_keys(raw: str) -> dict[str, str]:
    try:
        data = json.loads(raw)
    except ValueError as exc:
        raise ValueError(f"trusted-keys.json is not valid JSON: {exc}") from exc
    if not isinstance(data, dict) or not data:
        raise ValueError("trusted-keys.json must be a non-empty object mapping KID to PEM")
    if len(data) > MAX_TRUSTED_KEYS:
        raise ValueError(f"trusted-keys.json may contain at most {MAX_TRUSTED_KEYS} keys")
    out: dict[str, str] = {}
    for kid, pem in data.items():
        if not isinstance(kid, str) or not kid.strip():
            raise ValueError("trusted-keys.json has an empty key id")
        if not isinstance(pem, str) or "BEGIN PUBLIC KEY" not in pem:
            raise ValueError(f"trusted key {kid!r} is not a PEM public key")
        try:
            key = serialization.load_pem_public_key(pem.encode())
        except ValueError as exc:
            raise ValueError(f"trusted key {kid!r} is not a valid public key") from exc
        if not isinstance(key, Ed25519PublicKey):
            raise ValueError(f"trusted key {kid!r} is not Ed25519")
        out[kid.strip()] = pem.strip() + "\n"
    return out


def validate_ca_pem(pem: str) -> None:
    value = pem.strip()
    if not value:
        raise ValueError("root CA certificate must not be empty")
    try:
        ssl.create_default_context(cadata=value)
    except (ssl.SSLError, ValueError) as exc:
        raise ValueError("root CA certificate is not a valid PEM certificate") from exc


def _atomic_write(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if os.name != "nt":
        os.chmod(tmp, 0o600)
    os.replace(tmp, path)


def _atomic_json_write(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    _atomic_write(path, json.dumps(data, ensure_ascii=False, indent=2) + "\n")
