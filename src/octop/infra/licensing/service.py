"""Persistent Ed25519 instance licensing and coherent online/offline grants."""

from __future__ import annotations

import asyncio
import hashlib
import importlib
import json
import logging
import os
import re
import ssl
import tempfile
import threading
import time
import urllib.request
from collections.abc import Awaitable, Callable, Iterator
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)
_PROTOCOL = 1
_PRODUCT = "maitu"
_ISSUER = "maitu-license-center"
_AUDIENCE = "maitu-instance"
_KID = "maitu-license-1"
_STATE_NAME = "license-state.json"
_REMINDER_NAME = "license-reminders.json"
_MAX_FUTURE_SKEW = 300


def _canonical(value: Any) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":")).encode()


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd, name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        if os.name != "nt":
            os.chmod(name, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
        if os.name != "nt":
            os.chmod(path, 0o600)
    finally:
        with suppress(FileNotFoundError):
            os.unlink(name)


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else None
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return None


def _fingerprint(public_key: Ed25519PublicKey) -> str:
    der = public_key.public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
    )
    return hashlib.sha256(der).hexdigest()


def _strict_int(claims: dict[str, Any], key: str, minimum: int = 0) -> int:
    value = claims.get(key)
    if type(value) is not int or value < minimum:
        raise ValueError(f"invalid {key}")
    return value


@contextmanager
def _process_file_lock(path: Path) -> Iterator[None]:
    """Serialize reminder read/modify/write claims across processes."""
    lock_path = path.with_suffix(path.suffix + ".lock")
    lock_path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with lock_path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"\0")
            stream.flush()
        stream.seek(0)
        if os.name == "nt":
            msvcrt = importlib.import_module("msvcrt")

            stream.seek(0)
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
            try:
                yield
            finally:
                stream.seek(0)
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            fcntl = importlib.import_module("fcntl")

            flock = fcntl.flock
            flock(stream.fileno(), fcntl.LOCK_EX)
            try:
                yield
            finally:
                flock(stream.fileno(), fcntl.LOCK_UN)


class LicenseManager:
    """Owns a machine identity, signed credentials, and a durable entitlement ledger."""

    def __init__(
        self,
        state_dir: Path,
        trusted_keys_file: Path | None,
        center_url: str | None = None,
        allow_loopback: bool = False,
        ca_file: Path | None = None,
    ) -> None:
        self.state_dir = Path(state_dir).expanduser()
        self.trusted_keys_file = (
            Path(trusted_keys_file).expanduser() if trusted_keys_file is not None else None
        )
        self.center_url = center_url.rstrip("/") if center_url else None
        self.allow_loopback = allow_loopback
        self.ca_file = Path(ca_file).expanduser() if ca_file is not None else None
        self._ssl_context = (
            ssl.create_default_context(cafile=str(self.ca_file)) if self.ca_file else None
        )
        self._managed = self.trusted_keys_file is not None
        self._state_path = self.state_dir / _STATE_NAME
        self._reminder_path = self.state_dir / _REMINDER_NAME
        self._lock = threading.RLock()
        self._network_lock = asyncio.Lock()
        self._persist_lock = asyncio.Lock()
        self._callback_lock = asyncio.Lock()
        self._callback: Callable[[bool], Awaitable[None]] | None = None
        self._tasks: list[asyncio.Task[None]] = []
        self._state: dict[str, Any] = {}
        self._load_error: str | None = None
        self._configured_keys: dict[str, Ed25519PublicKey] = {}
        self._private_key: Ed25519PrivateKey | None = None
        self._mono_anchor = time.monotonic()
        self._time_anchor = int(time.time())
        self._observed_highwater = self._time_anchor
        self._time_anomaly = False
        self._stopping = False
        self._verified_cache: dict[tuple[str, str], dict[str, Any]] = {}
        self._last_allowed: bool | None = None
        self._initialize()

    @classmethod
    def from_env(cls, default_state_dir: Path) -> LicenseManager:
        state = Path(os.environ.get("MAITU_LICENSE_STATE_DIR", str(default_state_dir)))
        keyring = os.environ.get("MAITU_LICENSE_TRUSTED_KEYS_FILE")
        allow = os.environ.get("MAITU_LICENSE_ALLOW_LOOPBACK", "").strip().lower() in {
            "1",
            "true",
            "yes",
        }
        return cls(
            state,
            Path(keyring) if keyring else None,
            os.environ.get("MAITU_LICENSE_CENTER_URL"),
            allow,
            Path(os.environ["MAITU_LICENSE_CA_FILE"])
            if os.environ.get("MAITU_LICENSE_CA_FILE")
            else None,
        )

    def _initialize(self) -> None:
        try:
            self.state_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
            if os.name != "nt":
                os.chmod(self.state_dir, 0o700)
            private_path = self.state_dir / "instance-ed25519.pem"
            exists_private = private_path.exists()
            if exists_private:
                key = serialization.load_pem_private_key(private_path.read_bytes(), password=None)
                if not isinstance(key, Ed25519PrivateKey):
                    raise ValueError("instance key is not Ed25519")
                self._private_key = key
            else:
                key = Ed25519PrivateKey.generate()
                pem = key.private_bytes(
                    serialization.Encoding.PEM,
                    serialization.PrivateFormat.PKCS8,
                    serialization.NoEncryption(),
                )
                _atomic_write(private_path, pem)
                self._private_key = key
            if os.name != "nt":
                os.chmod(private_path, 0o600)
            public_key = key.public_key()
            self._identity = _fingerprint(public_key)
            self._instance_id = hashlib.sha256(
                public_key.public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
            ).hexdigest()[:32]
            state = _read_json(self._state_path)
            if state is None:
                if self._managed and exists_private:
                    self._load_error = "missing_ledger"
                    self._state = self._empty_state()
                else:
                    self._state = self._empty_state()
                    self._persist_sync()
            else:
                if (
                    state.get("instance_id") != self._instance_id
                    or state.get("key_fingerprint") != self._identity
                ):
                    raise ValueError("ledger identity mismatch")
                for field in (
                    "license_version",
                    "license_expires_at",
                    "revocation_floor",
                    "trusted_time",
                ):
                    if type(state.get(field)) is not int or state[field] < 0:
                        raise ValueError(f"invalid ledger {field}")
                if type(state.get("restored")) is not bool:
                    raise ValueError("invalid ledger restored flag")
                if state.get("status") not in {None, "active", "revoked", "expired"}:
                    raise ValueError("invalid ledger status")
                if any(
                    state.get(name) is not None and not isinstance(state.get(name), str)
                    for name in ("online", "offline")
                ):
                    raise ValueError("invalid ledger credential")
                if state["license_version"]:
                    if not re.fullmatch(
                        r"[0-9a-f]{32}", state.get("license_id", "")
                    ) or not state.get("status"):
                        raise ValueError("invalid ledger entitlement")
                    if not state.get("online") and not state.get("offline"):
                        raise ValueError("ledger entitlement has no signed credential")
                elif state.get("license_id") is not None:
                    raise ValueError("invalid fresh ledger")
                self._state = state
            if self._managed:
                if not self.trusted_keys_file or not self.trusted_keys_file.is_file():
                    raise ValueError("configured license trusted-key file is missing")
                try:
                    value = json.loads(self.trusted_keys_file.read_text(encoding="utf-8"))
                    if not isinstance(value, dict) or not value:
                        raise ValueError("trusted keys must be a non-empty JSON object")
                    keys: dict[str, Ed25519PublicKey] = {}
                    for kid, pem in value.items():
                        if not isinstance(kid, str) or not kid or not isinstance(pem, str):
                            raise ValueError("trusted keys must map key IDs to PEM strings")
                        parsed = serialization.load_pem_public_key(pem.encode("utf-8"))
                        if not isinstance(parsed, Ed25519PublicKey):
                            raise ValueError(f"trusted key {kid!r} is not Ed25519")
                        keys[kid] = parsed
                    self._configured_keys = keys
                except Exception as exc:
                    raise ValueError("configured license trusted-key file is invalid") from exc
            self._time_anchor = max(int(time.time()), int(self._state.get("trusted_time", 0)))
            self._mono_anchor = time.monotonic()
            self._time_anomaly = bool(
                self._state.get("trusted_time", 0) > int(time.time()) + _MAX_FUTURE_SKEW
            )
            self._observed_highwater = max(
                int(time.time()), int(self._state.get("trusted_time", 0))
            )
        except Exception as exc:
            self._load_error = str(exc)
            if not getattr(self, "_state", None):
                self._state = self._empty_state()
            self._private_key = getattr(self, "_private_key", None)
            self._identity = getattr(self, "_identity", "")
            self._instance_id = getattr(self, "_instance_id", "")

    def _empty_state(self) -> dict[str, Any]:
        return {
            "instance_id": getattr(self, "_instance_id", ""),
            "key_fingerprint": getattr(self, "_identity", ""),
            "license_id": None,
            "license_version": 0,
            "license_expires_at": 0,
            "status": None,
            "restored": False,
            "revocation_floor": 0,
            "online": None,
            "offline": None,
            "trusted_time": 0,
            "last_sync_at": None,
            "sync_error": None,
        }

    @property
    def managed(self) -> bool:
        return self._managed

    @property
    def instance_id(self) -> str:
        return self._instance_id

    def _trusted_now(self) -> int:
        wall = int(time.time())
        monotonic = self._time_anchor + max(0, int(time.monotonic() - self._mono_anchor))
        self._observed_highwater = max(
            self._observed_highwater, wall, monotonic, int(self._state.get("trusted_time", 0))
        )
        return self._observed_highwater

    def _credential_valid(
        self, token: str | None, purpose: str, now: int, *, calibrating: bool = False
    ) -> dict[str, Any] | None:
        if token is None or not self._configured_keys:
            return None
        if not isinstance(token, str) or not token:
            raise ValueError("invalid license token type")
        cached = None if calibrating else self._verified_cache.get((token, purpose))
        if cached is not None:
            if now >= cached["valid_until"] or (
                cached["status"] == "active" and now >= cached["license_expires_at"]
            ):
                return None
            return cached
        try:
            h = jwt.get_unverified_header(token)
            if (
                not isinstance(h, dict)
                or set(h) != {"alg", "kid", "typ"}
                or h.get("alg") != "EdDSA"
                or h.get("typ") != "JWT"
                or not isinstance(h.get("kid"), str)
            ):
                raise ValueError("unexpected token header")
            key = self._configured_keys.get(h["kid"])
            if key is None:
                raise ValueError("unknown license signing key")
            claims = jwt.decode(
                token,
                key,
                algorithms=["EdDSA"],
                audience=_AUDIENCE,
                issuer=_ISSUER,
                options={"verify_exp": False, "verify_iat": False, "verify_nbf": False},
            )
            if (
                claims.get("protocol") != _PROTOCOL
                or type(claims.get("protocol")) is not int
                or claims.get("product") != _PRODUCT
            ):
                raise ValueError("wrong license protocol")
            if claims.get("purpose") != purpose or claims.get("instance_id") != self.instance_id:
                raise ValueError("license purpose or identity mismatch")
            if claims.get("key_fingerprint") != self._identity:
                raise ValueError("license key fingerprint mismatch")
            if purpose == "online-license" and (
                not isinstance(claims.get("challenge"), str) or not claims["challenge"]
            ):
                raise ValueError("missing online challenge binding")
            for name in (
                "issued_at",
                "not_before",
                "license_expires_at",
                "valid_until",
                "license_version",
            ):
                _strict_int(claims, name, 1 if name == "license_version" else 0)
            if not re.fullmatch(r"[0-9a-f]{32}", claims.get("license_id", "")):
                raise ValueError("invalid license id")
            if (
                claims.get("status") not in {"active", "revoked", "expired"}
                or type(claims.get("restored")) is not bool
            ):
                raise ValueError("invalid entitlement status")
            validation_now = claims["issued_at"] if calibrating else now
            if (
                not calibrating and now + _MAX_FUTURE_SKEW < claims["issued_at"]
            ) or validation_now < claims["not_before"]:
                raise ValueError("license is not yet valid")
            if claims["valid_until"] < claims["issued_at"] or (
                claims["status"] == "active" and claims["license_expires_at"] < claims["not_before"]
            ):
                raise ValueError("inconsistent license times")
            if claims["status"] == "expired" and claims["license_expires_at"] > claims["issued_at"]:
                raise ValueError("expired entitlement precedes its deadline")
            if purpose == "online-license":
                max_cache = 86400 if claims["status"] == "active" else 300
                if claims["valid_until"] > claims["issued_at"] + max_cache:
                    raise ValueError("online cache validity exceeds protocol limit")
                if (
                    claims["status"] == "active"
                    and claims["valid_until"] > claims["license_expires_at"]
                ):
                    raise ValueError("online cache outlives the entitlement")
            elif claims["valid_until"] > claims["license_expires_at"]:
                raise ValueError("offline validity outlives the entitlement")
            if validation_now >= claims["valid_until"] or (
                claims["status"] == "active" and validation_now >= claims["license_expires_at"]
            ):
                return None
            if len(self._verified_cache) >= 4:
                self._verified_cache.clear()
            self._verified_cache[(token, purpose)] = claims
            return claims
        except Exception as exc:
            raise ValueError(f"invalid signed {purpose}: {exc}") from exc

    def _reason(self) -> str | None:
        if not self.managed:
            return None
        if self._load_error:
            if "configured" in self._load_error or "trusted-key" in self._load_error:
                return "configuration"
            return "invalid_credential"
        state = self._state
        if not state.get("license_id"):
            return "unactivated"
        now = self._trusted_now()
        if self._time_anomaly:
            return "time_anomaly"
        if now >= int(state.get("license_expires_at", 0)):
            return "expired"
        if state.get("status") == "revoked":
            return "revoked"
        if state.get("status") == "expired":
            return "expired"
        if state.get("status") != "active":
            return "invalid_credential"
        if (
            int(state.get("revocation_floor", 0)) >= int(state.get("license_version", 0))
            and int(state.get("revocation_floor", 0)) > 0
        ):
            return "revoked"
        for typ in ("online", "offline"):
            try:
                claims = self._credential_valid(state.get(typ), f"{typ}-license", now)
            except ValueError:
                return "invalid_credential"
            if claims:
                canonical = (
                    claims["license_id"],
                    claims["license_version"],
                    claims["status"],
                    claims["license_expires_at"],
                    claims["restored"],
                )
                current = (
                    state.get("license_id"),
                    state.get("license_version"),
                    state.get("status"),
                    state.get("license_expires_at"),
                    state.get("restored", False),
                )
                if canonical != current:
                    return "invalid_credential"
                if claims["status"] == "active":
                    return None
        if state.get("online") or state.get("offline"):
            return "cache_expired"
        return "invalid_credential"

    @property
    def business_allowed(self) -> bool:
        with self._lock:
            return not self.managed or self._reason() is None

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            reason = self._reason()
            allowed = not self.managed or reason is None
            state = self._state
            remaining = (
                int(state.get("license_expires_at", 0)) - self._trusted_now()
                if state.get("license_expires_at")
                else None
            )
            typ = None
            if allowed and self.managed:
                for candidate in ("online", "offline"):
                    if self._credential_valid(
                        state.get(candidate), f"{candidate}-license", self._trusted_now()
                    ):
                        typ = candidate
                        break
            state_name = (
                "UNACTIVATED"
                if not self.managed
                else "ACTIVE"
                if allowed and typ == "online"
                else "CACHED_ACTIVE"
                if allowed and typ == "offline"
                else "UNACTIVATED"
                if reason == "unactivated"
                else "LOCKED"
            )
            return {
                "managed": self.managed,
                "state": state_name,
                "business_allowed": allowed,
                "reason": reason,
                "instance_id": self.instance_id,
                "license_id": state.get("license_id"),
                "license_version": state.get("license_version") or None,
                "license_expires_at": state.get("license_expires_at") or None,
                "credential_type": typ,
                "expiring_soon": bool(remaining is not None and 0 < remaining <= 259200),
                "last_sync_at": state.get("last_sync_at"),
                "sync_error": state.get("sync_error"),
            }

    def require_business_access(self) -> None:
        if self.managed and not self.business_allowed:
            raise OctopError(
                ErrorCode.INSTANCE_LICENSE_LOCKED,
                "instance license is not active",
                status=423,
                details={"reason": self.snapshot()["reason"]},
            )

    def _persist_sync(self) -> None:
        _atomic_write(self._state_path, _canonical(self._state))

    async def _persist(self) -> None:
        async with self._persist_lock:
            with self._lock:
                payload = _canonical(dict(self._state))
            await asyncio.to_thread(_atomic_write, self._state_path, payload)

    def _transition(self, claims: dict[str, Any], token: str, token_type: str) -> None:
        old = self._state
        ver = claims["license_version"]
        current = int(old.get("license_version", 0))
        canonical = (
            claims["license_id"],
            ver,
            claims["status"],
            claims["license_expires_at"],
            claims["restored"],
        )
        existing = (
            old.get("license_id"),
            current,
            old.get("status"),
            old.get("license_expires_at"),
            old.get("restored", False),
        )
        if ver < current:
            raise ValueError("license version rollback")
        if ver == current and current:
            derived_expiry = (
                claims["status"] == "expired"
                and old.get("status") == "active"
                and claims["license_id"] == old.get("license_id")
                and claims["license_expires_at"] == old.get("license_expires_at")
                and claims["restored"] == old.get("restored", False)
                and claims["license_expires_at"] <= claims["issued_at"]
            )
            if canonical != existing and not derived_expiry:
                raise ValueError("equal-version canonical entitlement conflict")
            if int(old.get("revocation_floor", 0)) >= ver and not (
                claims["restored"] and claims["status"] == "active"
            ):
                raise ValueError("same-version credential cannot override revocation")
        if (
            ver > current
            and int(old.get("revocation_floor", 0)) >= current
            and current
            and not (claims["restored"] and claims["status"] == "active")
            and claims["status"] == "active"
        ):
            raise ValueError("revoked entitlement requires explicit restored grant")
        state = dict(old)
        if ver > current:
            state.update(
                license_id=claims["license_id"],
                license_version=ver,
                license_expires_at=claims["license_expires_at"],
                status=claims["status"],
                restored=claims["restored"],
                online=None,
                offline=None,
            )
        state[token_type] = token
        if claims["status"] == "revoked":
            state["revocation_floor"] = max(int(state.get("revocation_floor", 0)), ver)
            state["status"] = "revoked"
        elif claims["status"] == "expired":
            state["status"] = "expired"
        elif (
            claims["restored"]
            and ver > int(state.get("revocation_floor", 0))
            or not state.get("status")
        ):
            state["status"] = "active"
        self._state = state

    def export_request(self) -> dict[str, str]:
        if self._private_key is None:
            raise RuntimeError("instance private key unavailable")
        pub = (
            self._private_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode()
        )
        claims = {
            "iss": self.instance_id,
            "aud": "maitu-license-center",
            "purpose": "offline-request",
            "protocol": 1,
            "product": _PRODUCT,
            "instance_id": self.instance_id,
            "key_fingerprint": self._identity,
            "iat": self._trusted_now(),
        }
        proof = jwt.encode(
            claims, self._private_key, algorithm="EdDSA", headers={"kid": _KID, "typ": "JWT"}
        )
        return {
            "instance_id": self.instance_id,
            "public_key_pem": pub,
            "key_fingerprint": self._identity,
            "proof": proof,
        }

    async def import_offline(self, token: str) -> dict[str, Any]:
        if not self.managed or not self._configured_keys:
            raise ValueError("licensing is not configured")
        if self._load_error == "missing_ledger":
            raise ValueError(
                "existing instance ledger is missing; offline import cannot restore lost history"
            )
        async with self._network_lock:
            try:
                now = self._trusted_now()
                claims = self._credential_valid(token, "offline-license", now)
                if claims is None:
                    raise ValueError("invalid offline token")
                with self._lock:
                    self._transition(claims, token, "offline")
                    self._state["trusted_time"] = max(
                        int(self._state.get("trusted_time", 0)), now, claims["issued_at"]
                    )
                    self._time_anchor = self._state["trusted_time"]
                    self._mono_anchor = time.monotonic()
                await self._persist()
                self._load_error = None
                await self._notify_if_changed()
                return self.snapshot()
            except Exception:
                await self._notify_if_changed()
                raise

    async def _request(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        if not self.center_url:
            raise RuntimeError("license center URL is not configured")
        url = self.center_url + path
        parsed = urlsplit(self.center_url)
        loopback = parsed.hostname in {"localhost", "127.0.0.1", "::1"}
        if parsed.scheme != "https" and not (
            self.allow_loopback and loopback and parsed.scheme == "http"
        ):
            raise ValueError("license center must use HTTPS (or explicitly enabled loopback HTTP)")
        raw = _canonical(body)

        def send() -> dict[str, Any]:
            req = urllib.request.Request(
                url, raw, {"Content-Type": "application/json"}, method="POST"
            )
            kwargs: dict[str, Any] = {"timeout": 15}
            if self._ssl_context is not None:
                kwargs["context"] = self._ssl_context
            with urllib.request.urlopen(req, **kwargs) as resp:
                result = json.loads(resp.read())
            if not isinstance(result, dict):
                raise ValueError("invalid license center response")
            return result

        return await asyncio.to_thread(send)

    async def _proof(self, operation: str, challenge: str, request: dict[str, Any]) -> str:
        if self._private_key is None:
            raise RuntimeError("instance private key unavailable")
        unsigned = dict(request)
        claims = {
            "iss": self.instance_id,
            "aud": "maitu-license-center",
            "purpose": "instance-proof",
            "protocol": 1,
            "product": _PRODUCT,
            "operation": operation,
            "challenge": challenge,
            "content_hash": hashlib.sha256(_canonical(unsigned)).hexdigest(),
            "iat": int(time.time()) if self._time_anomaly else self._trusted_now(),
        }
        return jwt.encode(
            claims, self._private_key, algorithm="EdDSA", headers={"kid": _KID, "typ": "JWT"}
        )

    async def _online(self, operation: str, code: str | None = None) -> dict[str, Any]:
        if not self.managed:
            return self.snapshot()
        if not self.center_url:
            raise RuntimeError("license center URL is not configured")
        async with self._network_lock:
            calibrating = self._time_anomaly
            wall_started = int(time.time())
            request_started_at = self._trusted_now()
            started = time.monotonic()
            try:
                private_key = self._private_key
                if private_key is None:
                    raise RuntimeError("instance private key unavailable")
                public = (
                    private_key.public_key()
                    .public_bytes(
                        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
                    )
                    .decode()
                )
                challenge_req = {
                    "instance_id": self.instance_id,
                    "operation": "activate" if code else "sync",
                    "public_key_pem": public if code else None,
                }
                challenge = await self._request("/api/v1/instance/challenges", challenge_req)
                base: dict[str, Any] = {
                    "instance_id": self.instance_id,
                    "public_key_pem": public,
                }
                if code:
                    base["activation_code"] = code
                base.update(challenge=challenge["challenge"])
                proof = await self._proof(
                    "activate" if code else "sync", challenge["challenge"], base
                )
                token_request_started = time.monotonic()
                response = await self._request(
                    "/api/v1/instance/activate" if code else "/api/v1/instance/sync",
                    {**base, "proof": proof},
                )
                token_rtt = max(0, int(time.monotonic() - token_request_started))
                token = response.get("token")
                if not isinstance(token, str):
                    raise ValueError("license center returned an invalid token")
                validation_now = wall_started + token_rtt if calibrating else self._trusted_now()
                claims = self._credential_valid(
                    token, "online-license", validation_now, calibrating=calibrating
                )
                if claims is None:
                    raise ValueError("license center returned an invalid token")
                if claims.get("challenge") != challenge["challenge"]:
                    raise ValueError("license response is not bound to the fresh challenge")
                if (
                    claims["issued_at"]
                    > request_started_at + int(time.monotonic() - started) + _MAX_FUTURE_SKEW
                    and not calibrating
                ):
                    raise ValueError("license center issued time exceeds bounded request interval")
                if calibrating and claims["issued_at"] < int(self._state.get("last_sync_at") or 0):
                    raise ValueError("license center signed time predates the last synchronization")
                with self._lock:
                    self._transition(claims, token, "online")
                    self._state["last_sync_at"] = claims["issued_at"]
                    self._state["sync_error"] = None
                    if calibrating:
                        calibrated_time = claims["issued_at"] + token_rtt
                        self._state["trusted_time"] = calibrated_time
                        self._time_anchor = calibrated_time
                        self._observed_highwater = calibrated_time
                    else:
                        self._state["trusted_time"] = max(
                            self._state["trusted_time"], claims["issued_at"]
                        )
                        self._time_anchor = claims["issued_at"]
                        self._observed_highwater = max(
                            self._observed_highwater, claims["issued_at"], int(time.time())
                        )
                    self._mono_anchor = time.monotonic()
                    self._time_anomaly = False
                await self._persist()
                self._load_error = None
                await self._notify_if_changed()
                return self.snapshot()
            except Exception as exc:
                with self._lock:
                    self._state["sync_error"] = str(exc)[:300]
                await self._persist()
                await self._notify_if_changed()
                raise

    async def activate(self, code: str) -> dict[str, Any]:
        if not isinstance(code, str) or not code.strip():
            raise ValueError("activation code is required")
        return await self._online("activate", code.strip())

    async def refresh(self) -> dict[str, Any]:
        return await self._online("sync")

    async def _notify_if_changed(self) -> None:
        async with self._callback_lock:
            if self._stopping:
                return
            after = self.business_allowed
            previous = self._last_allowed
            if previous is None:
                callback = self._callback
                if callback is not None:
                    self._last_allowed = after
                    await callback(after)
                return
            if previous == after:
                return
            if not after and self.managed:
                with self._lock:
                    self._state["trusted_time"] = max(
                        int(self._state.get("trusted_time", 0)), self._trusted_now()
                    )
                await self._persist()
            self._last_allowed = after
            callback = self._callback
            if callback is not None:
                await callback(after)

    async def start(self, on_change: Callable[[bool], Awaitable[None]] | None = None) -> None:
        self._stopping = False
        self._callback = on_change
        current = self.business_allowed
        if self._last_allowed is None:
            self._last_allowed = current
            if self._callback:
                await self._callback(current)
        self._tasks = [
            asyncio.create_task(self._deadline_loop()),
            asyncio.create_task(self._sync_loop()),
            asyncio.create_task(self._time_loop()),
        ]
        if self.managed and self.center_url:
            try:
                await self.refresh()
            except Exception:
                logger.info("Initial license synchronization failed", exc_info=True)

    async def stop(self) -> None:
        self._stopping = True
        tasks, self._tasks = self._tasks, []
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        self._callback = None

    async def _deadline_loop(self) -> None:
        while True:
            await asyncio.sleep(1)
            await self._notify_if_changed()

    async def _sync_loop(self) -> None:
        while True:
            await asyncio.sleep(3600)
            if self.managed and self.center_url:
                try:
                    await self.refresh()
                except Exception:
                    logger.info("Periodic license synchronization failed", exc_info=True)

    async def _time_loop(self) -> None:
        while True:
            await asyncio.sleep(60)
            if self.managed:
                with self._lock:
                    self._state["trusted_time"] = max(
                        int(self._state.get("trusted_time", 0)), self._trusted_now()
                    )
                await self._persist()

    async def claim_reminder(self, user_id: int, time_zone: str) -> dict[str, Any]:
        with self._lock:
            expiry = self._state.get("license_expires_at") if self.managed else None
            now = self._trusted_now()
            remaining = int(expiry) - now if expiry else None
            if not self.business_allowed or remaining is None or not 0 < remaining <= 259200:
                return {
                    "show": False,
                    "expires_at": expiry,
                    "remaining_seconds": remaining,
                    "date": None,
                }
            key = f"{self.instance_id}:{user_id}:{expiry}:{time_zone}"
        show, today = await asyncio.to_thread(self._claim_reminder_sync, key, now, time_zone)
        return {"show": show, "expires_at": expiry, "remaining_seconds": remaining, "date": today}

    def _claim_reminder_sync(self, key: str, now: int, time_zone: str) -> tuple[bool, str]:
        try:
            today = (
                datetime.fromtimestamp(now, UTC).astimezone(ZoneInfo(time_zone)).date().isoformat()
            )
        except Exception:
            today = datetime.fromtimestamp(now, UTC).date().isoformat()
        with _process_file_lock(self._reminder_path):
            db = _read_json(self._reminder_path) or {"claims": {}}
            claims = db.get("claims")
            if not isinstance(claims, dict):
                claims = {}
            if claims.get(key) == today:
                return False, today
            claims[key] = today
            db["claims"] = claims
            _atomic_write(self._reminder_path, _canonical(db))
            return True, today


def _unmanaged() -> LicenseManager:
    """Create the import-time no-op accessor without environment or filesystem access."""
    manager = LicenseManager.__new__(LicenseManager)
    manager.state_dir = Path()
    manager.trusted_keys_file = None
    manager.center_url = None
    manager.allow_loopback = False
    manager._managed = False
    manager._state_path = Path()
    manager._reminder_path = Path()
    manager._lock = threading.RLock()
    manager._network_lock = asyncio.Lock()
    manager._persist_lock = asyncio.Lock()
    manager._callback_lock = asyncio.Lock()
    manager._callback = None
    manager._tasks = []
    manager._state = {
        "license_id": None,
        "license_version": 0,
        "license_expires_at": 0,
        "status": None,
        "restored": False,
        "revocation_floor": 0,
        "online": None,
        "offline": None,
        "trusted_time": 0,
        "last_sync_at": None,
        "sync_error": None,
    }
    manager._load_error = None
    manager._configured_keys = {}
    manager._private_key = None
    manager._identity = ""
    manager._verified_cache = {}
    manager._instance_id = ""
    manager._mono_anchor = time.monotonic()
    manager._time_anchor = int(time.time())
    manager._observed_highwater = manager._time_anchor
    manager._time_anomaly = False
    manager._last_allowed = True
    manager._stopping = False
    return manager


_DEFAULT = _unmanaged()
_CURRENT = _DEFAULT
_CURRENT_LOCK = threading.Lock()


def current_license() -> LicenseManager:
    return _CURRENT


def install_unmanaged_license_manager() -> None:
    """Restore the process-wide no-op manager after trust settings are cleared."""
    install_license_manager(_DEFAULT)


def install_license_manager(manager: LicenseManager) -> None:
    global _CURRENT
    with _CURRENT_LOCK:
        _CURRENT = manager
