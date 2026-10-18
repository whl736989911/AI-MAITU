"""Trusted release-center checks and host-updater request handoff."""

from __future__ import annotations

import ipaddress
import json
import logging
import os
import platform
import re
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from packaging.version import InvalidVersion, Version

from octop.infra.setup.version_info import get_local_version

logger = logging.getLogger(__name__)
_PHASES = {
    "idle",
    "queued",
    "downloading",
    "validating",
    "building",
    "backing_up",
    "stopping",
    "installing",
    "restarting",
    "verifying",
    "succeeded",
    "failed",
}
_ARCHITECTURES = {"x86_64": "amd64", "amd64": "amd64", "aarch64": "arm64", "arm64": "arm64"}
_MANIFEST_CLAIMS = {
    "iss",
    "aud",
    "product",
    "protocol",
    "release_id",
    "version",
    "filename",
    "sha256",
    "size",
    "release_notes",
    "package_type",
    "platform",
    "architecture",
    "iat",
}


class UpdateClientError(RuntimeError):
    """An update configuration, center, or trust validation error."""


class UpdateQueueConflict(UpdateClientError):
    """A queue file or active host upgrade already owns the update slot."""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _loopback(host: str | None) -> bool:
    if host == "localhost":
        return True
    try:
        return bool(host and ipaddress.ip_address(host).is_loopback)
    except ValueError:
        return False


def _center_url() -> str:
    raw = os.environ.get("MAITU_UPDATE_CENTER_URL", "").strip().rstrip("/")
    if not raw:
        raise UpdateClientError("Update center is not configured.")
    if any(char.isspace() for char in raw) or chr(92) in raw:
        raise UpdateClientError("Update center URL is invalid.")
    try:
        parsed = urllib.parse.urlsplit(raw)
        hostname = parsed.hostname
        _ = parsed.port
    except ValueError as exc:
        raise UpdateClientError("Update center URL is invalid.") from exc
    if parsed.username or parsed.password or parsed.query or parsed.fragment or not hostname:
        raise UpdateClientError("Update center URL is invalid.")
    loopback_allowed = os.environ.get("MAITU_UPDATE_ALLOW_LOOPBACK", "").lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
    if parsed.scheme != "https" and not (
        parsed.scheme == "http" and loopback_allowed and _loopback(hostname)
    ):
        raise UpdateClientError(
            "Update center URL must use HTTPS (HTTP is allowed only for configured loopback development)."
        )
    if parsed.scheme not in {"https", "http"}:
        raise UpdateClientError("Update center URL scheme is unsupported.")
    return raw


def _architecture() -> str:
    machine = platform.machine().lower()
    if machine in _ARCHITECTURES:
        return _ARCHITECTURES[machine]
    raise UpdateClientError(f"Unsupported update architecture: {machine or 'unknown'}.")


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        request: urllib.request.Request,
        file: Any,
        code: int,
        message: str,
        headers: Any,
        new_url: str,
    ) -> None:
        raise urllib.error.HTTPError(
            request.full_url, code, "redirects are not allowed", headers, file
        )


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


class UpdateClient:
    """Single-flight cached release checks and exclusive host-updater queue."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._cache: dict[str, Any] = {}
        self._loaded = False

    @property
    def configured(self) -> bool:
        return (
            bool(os.environ.get("MAITU_UPDATE_CENTER_URL", "").strip())
            and bool(os.environ.get("MAITU_UPDATE_CLIENT_TOKEN", "").strip())
            and bool(os.environ.get("MAITU_UPDATE_TRUSTED_KEYS_FILE", "").strip())
        )

    @property
    def configuration_requested(self) -> bool:
        return any(
            os.environ.get(key, "").strip()
            for key in (
                "MAITU_UPDATE_CENTER_URL",
                "MAITU_UPDATE_CLIENT_TOKEN",
                "MAITU_UPDATE_TRUSTED_KEYS_FILE",
            )
        )

    @property
    def queue_dir(self) -> Path | None:
        raw = os.environ.get("MAITU_UPDATE_QUEUE_DIR", "").strip()
        return Path(raw).expanduser() if raw else None

    @property
    def install_available(self) -> bool:
        return self.queue_dir is not None

    def _cache_path(self) -> Path:
        if self.queue_dir is not None:
            return self.queue_dir / "update-cache.json"
        from octop.infra.utils.paths import PathLayout

        return PathLayout.from_env().root / "update-cache.json"

    def _load_cache(self) -> None:
        if self._loaded:
            return
        self._loaded = True
        self._cache = _read_json(self._cache_path()) or {}

    def _save_cache(self) -> None:
        path = self._cache_path()
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            temp = path.with_suffix(f".{uuid.uuid4().hex}.tmp")
            temp.write_text(json.dumps(self._cache, ensure_ascii=False), encoding="utf-8")
            os.replace(temp, path)
        except OSError:
            logger.warning("Could not persist update check cache", exc_info=True)

    def _fetch_manifest(self) -> dict[str, Any] | None:
        base = _center_url()
        token = os.environ.get("MAITU_UPDATE_CLIENT_TOKEN", "").strip()
        if not token:
            raise UpdateClientError("Update center client token is not configured.")
        arch = _architecture()
        url = f"{base}/api/v1/releases/latest?platform=linux&architecture={arch}"
        request = urllib.request.Request(
            url, headers={"Authorization": f"Bearer {token}", "Accept": "application/json"}
        )
        try:
            opener = urllib.request.build_opener(_NoRedirect)
            with opener.open(request, timeout=10) as response:
                payload = json.loads(response.read(1024 * 1024).decode("utf-8"))
        except (
            urllib.error.URLError,
            TimeoutError,
            OSError,
            UnicodeError,
            json.JSONDecodeError,
        ) as exc:
            raise UpdateClientError(f"Update center request failed: {exc}") from exc
        if not isinstance(payload, dict) or set(payload) != {"manifest"}:
            raise UpdateClientError("Update center returned an invalid manifest response.")
        token_value = payload["manifest"]
        if token_value is None:
            return None
        if not isinstance(token_value, str):
            raise UpdateClientError("Update center returned an invalid signed manifest.")
        key_file = os.environ.get("MAITU_UPDATE_TRUSTED_KEYS_FILE", "").strip()
        try:
            keyring = json.loads(Path(key_file).expanduser().read_text(encoding="utf-8"))
            header = jwt.get_unverified_header(token_value)
            if (
                not isinstance(keyring, dict)
                or not isinstance(header, dict)
                or set(header) != {"alg", "kid", "typ"}
                or header.get("alg") != "EdDSA"
                or header.get("typ") != "JWT"
                or not isinstance(header.get("kid"), str)
            ):
                raise ValueError("invalid keyring or JWT header")
            pem = keyring.get(header["kid"])
            if not isinstance(pem, str) or not pem:
                raise ValueError("unknown release signing key")
            public_key = serialization.load_pem_public_key(pem.encode("utf-8"))
            if not isinstance(public_key, Ed25519PublicKey):
                raise ValueError("release signing key is not Ed25519")
            claims = jwt.decode(
                token_value,
                public_key,
                algorithms=["EdDSA"],
                issuer="maitu-license-center",
                audience="maitu-updater",
                options={"require": sorted(_MANIFEST_CLAIMS - {"aud", "iss"})},
            )
        except (OSError, json.JSONDecodeError, jwt.PyJWTError, ValueError) as exc:
            raise UpdateClientError(
                f"Signed release manifest could not be verified: {exc}"
            ) from exc
        if (
            claims.get("product") != "maitu"
            or type(claims.get("protocol")) is not int
            or claims.get("protocol") != 1
            or claims.get("package_type") != "source"
            or claims.get("platform") != "linux"
            or claims.get("architecture") != arch
        ):
            raise UpdateClientError(
                "Signed release manifest is incompatible with this installation."
            )
        version = claims.get("version")
        filename = claims.get("filename")
        digest = claims.get("sha256")
        if (
            not isinstance(version, str)
            or not isinstance(filename, str)
            or not isinstance(claims.get("release_id"), str)
            or not re.fullmatch(r"[0-9a-f]{32}", claims["release_id"])
            or type(claims.get("size")) is not int
            or claims["size"] <= 0
            or not isinstance(claims.get("release_notes"), str)
            or type(claims.get("iat")) is not int
        ):
            raise UpdateClientError("Signed release manifest contains invalid release identifiers.")
        try:
            parsed_version = Version(version)
        except InvalidVersion as exc:
            raise UpdateClientError(
                "Signed release manifest has an invalid semantic version."
            ) from exc
        if (
            filename != f"maitu-{version}.zip"
            or not isinstance(digest, str)
            or not re.fullmatch(r"[0-9a-f]{64}", digest)
        ):
            raise UpdateClientError("Signed release manifest integrity fields are invalid.")
        try:
            current = Version(get_local_version())
        except InvalidVersion as exc:
            raise UpdateClientError(
                "Installed version cannot be compared with the release."
            ) from exc
        if parsed_version < current:
            raise UpdateClientError("Signed release manifest is a version downgrade.")
        return {
            "token": token_value,
            "claims": claims,
            "version_obj": str(parsed_version),
            "has_update": parsed_version > current,
        }

    def check(self, *, force: bool = False) -> dict[str, Any]:
        with self._lock:
            self._load_cache()
            interval = self._check_interval()
            checked_at = self._cache.get("checked_at")
            age = (
                (datetime.now(UTC) - self._parse_time(checked_at)).total_seconds()
                if checked_at
                else None
            )
            due = force or age is None or age < 0 or age >= interval
            if due:
                try:
                    if self.configuration_requested and not self.configured:
                        raise UpdateClientError("Update center configuration is incomplete.")
                    manifest = self._fetch_manifest() if self.configured else None
                    self._cache.update({"checked_at": _now(), "error": None, "manifest": manifest})
                except UpdateClientError as exc:
                    self._cache.update({"checked_at": _now(), "error": str(exc)})
                self._save_cache()
            return self.status_payload()

    def _check_interval(self) -> int:
        try:
            return max(60, int(os.environ.get("MAITU_UPDATE_CHECK_INTERVAL", "21600")))
        except ValueError:
            return 21600

    @staticmethod
    def _parse_time(value: str) -> datetime:
        try:
            parsed = datetime.fromisoformat(value)
        except (TypeError, ValueError):
            return datetime.min.replace(tzinfo=UTC)
        return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed

    def status_payload(self) -> dict[str, Any]:
        self._load_cache()
        try:
            current = Version(get_local_version())
        except InvalidVersion:
            current = Version("0")
        cached_manifest = self._cache.get("manifest")
        claims = cached_manifest.get("claims") if isinstance(cached_manifest, dict) else None
        latest = claims.get("version") if isinstance(claims, dict) else None
        if not isinstance(latest, str):
            latest = None
        try:
            has_update = bool(latest and Version(latest) > current)
        except (InvalidVersion, TypeError):
            latest, has_update = None, False
        state = _read_json(self.queue_dir / "upgrade-state.json") if self.queue_dir else None
        request = _read_json(self.queue_dir / "upgrade-request.json") if self.queue_dir else None
        request_valid = bool(
            request
            and isinstance(request.get("request_id"), str)
            and isinstance(request.get("version"), str)
        )
        state_phase = state.get("phase") if state and isinstance(state.get("phase"), str) else None
        invalid_state = state is not None and state_phase not in _PHASES
        phase = (
            state_phase
            if state_phase in _PHASES
            else ("failed" if invalid_state else ("queued" if request_valid else "idle"))
        )
        upgrade_error = (
            state.get("error")
            if state and isinstance(state.get("error"), str)
            else ("Host updater state is invalid." if invalid_state else None)
        )
        state_target = (
            state.get("target_version")
            if state and isinstance(state.get("target_version"), str)
            else None
        )
        request_target = request.get("version") if request_valid and request else None
        error = self._cache.get("error")
        return {
            "current_version": str(current),
            "latest_version": latest,
            "has_update": has_update,
            "error": error if isinstance(error, str) else None,
            "source": "authorization_center" if latest else None,
            "release_notes": claims.get("release_notes")
            if isinstance(claims, dict) and isinstance(claims.get("release_notes"), str)
            else None,
            "is_editable": False,
            "service_mode": None,
            "desktop": False,
            "center_configured": self.configured,
            "install_available": self.install_available,
            "checked_at": self._cache.get("checked_at")
            if isinstance(self._cache.get("checked_at"), str)
            else None,
            "upgrade_phase": phase,
            "upgrade_error": upgrade_error,
            "upgrade_target": state_target or request_target,
        }

    def queue_upgrade(self, target: str | None) -> dict[str, Any]:
        refreshed = self.check(force=True)
        if refreshed["error"]:
            raise UpdateClientError(str(refreshed["error"]))
        manifest = self._cache.get("manifest")
        claims = manifest.get("claims") if isinstance(manifest, dict) else None
        if not claims or not refreshed["has_update"]:
            raise UpdateClientError("No newer trusted release is available.")
        version = claims["version"]
        if target is not None and target != version:
            raise UpdateClientError("The requested version is stale; check for updates and retry.")
        if not self.install_available:
            raise UpdateClientError("Host updater is not configured for this installation.")
        queue_dir = self.queue_dir
        assert queue_dir is not None
        queue_dir.mkdir(parents=True, exist_ok=True)
        state = _read_json(queue_dir / "upgrade-state.json")
        if state and state.get("phase") in _PHASES - {"idle", "succeeded", "failed"}:
            raise UpdateQueueConflict("An upgrade is already in progress.")
        payload = {
            "release_id": claims["release_id"],
            "version": version,
            "request_id": uuid.uuid4().hex,
            "requested_at": _now(),
        }
        temp = queue_dir / f".upgrade-request-{uuid.uuid4().hex}.tmp"
        request_path = queue_dir / "upgrade-request.json"
        try:
            descriptor = os.open(temp, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
            with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                json.dump(payload, stream)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temp, request_path)
        except FileExistsError as exc:
            raise UpdateQueueConflict("An upgrade request is already queued.") from exc
        except OSError as exc:
            raise UpdateClientError(f"Could not queue upgrade request: {exc}") from exc
        finally:
            temp.unlink(missing_ok=True)
        return {"status": "queued", "request_id": payload["request_id"], "version": version}


update_client = UpdateClient()
