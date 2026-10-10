"""Protected Ed25519 identity, cache, and strict instance-license client."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import inspect
import json
import math
import os
import secrets
import ssl
import time
import urllib.parse
import urllib.request
from collections.abc import AsyncIterator, Awaitable, Callable, Coroutine, Mapping
from contextlib import asynccontextmanager, suppress
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.paths import PathLayout

_ACTIVE = "ACTIVE"
_CACHED = "CACHED_ACTIVE"
_LOCKED = "LOCKED"
_UNACTIVATED = "UNACTIVATED"
_TYP = "maitu-license"
_PROOF_TYP = "maitu-instance-proof"
_PRODUCT = "maitu"
_ISSUER = "maitu-license-center"
_AUDIENCE = "maitu-license-center"


def _b64d(value: str) -> bytes:
    if (
        not isinstance(value, str)
        or not value
        or any(
            c not in "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
            for c in value
        )
    ):
        raise ValueError("invalid base64url")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON member")
        result[key] = value
    return result


def _b64e(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode("ascii")


def _canonical(value: object) -> bytes:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def _atomic_private(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    with suppress(OSError):
        path.parent.chmod(0o700)
    tmp = path.with_name(f".{path.name}.{secrets.token_hex(8)}.tmp")
    try:
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(tmp, path)
        with suppress(OSError):
            path.chmod(0o600)
    finally:
        with suppress(FileNotFoundError):
            tmp.unlink()


def _endpoint_allowed(url: str) -> str:
    parsed = urllib.parse.urlsplit(url)
    if (
        parsed.query
        or parsed.fragment
        or parsed.username is not None
        or parsed.password is not None
    ):
        raise ValueError("license server URL must not include credentials, query, or fragment")
    if parsed.scheme == "https" and parsed.hostname:
        return url.rstrip("/")
    if (
        parsed.scheme == "http"
        and parsed.hostname
        and parsed.hostname.lower() in {"localhost", "127.0.0.1", "::1"}
    ):
        return url.rstrip("/")
    raise ValueError("license server must use HTTPS or loopback HTTP")


class LicenseService:
    """Mandatory instance license gate with signed online synchronization."""

    def __init__(
        self,
        home: Path,
        *,
        server_url: str | None = None,
        trusted_keys: Mapping[str, str] | None = None,
        ca_file: str | None = None,
        contact: str = "",
    ) -> None:
        self.home = Path(home).expanduser()
        self.license_dir = self.home / "license"
        self._server_configured = (
            server_url if server_url is not None else os.environ.get("OCTOP_LICENSE_SERVER_URL")
        )
        self.server_url = (
            _endpoint_allowed(self._server_configured) if self._server_configured else None
        )
        self.ca_file = ca_file if ca_file is not None else os.environ.get("OCTOP_LICENSE_CA_FILE")
        self.contact = contact if contact else os.environ.get("OCTOP_LICENSE_CONTACT", "")
        if trusted_keys is None:
            trust_path = os.environ.get("OCTOP_LICENSE_TRUST_FILE")
            if trust_path:
                raw = json.loads(Path(trust_path).expanduser().read_text("utf-8"))
                if not isinstance(raw, dict):
                    raise ValueError("license trust file must be a kid-to-PEM object")
                trusted_keys = raw
        self.trusted_keys: dict[str, Ed25519PublicKey] = {}
        for kid, pem in (trusted_keys or {}).items():
            if not isinstance(kid, str) or not isinstance(pem, str):
                raise ValueError("invalid trusted license key entry")
            key = serialization.load_pem_public_key(pem.encode("utf-8"))
            if not isinstance(key, Ed25519PublicKey):
                raise ValueError("license trust keys must be Ed25519")
            self.trusted_keys[kid] = key
        self._state = _UNACTIVATED
        self._reason: str | None = (
            "unactivated" if self.server_url and self.trusted_keys else "configuration_required"
        )
        self._claims: dict[str, Any] | None = None
        self._token: str | None = None
        self._private: Ed25519PrivateKey | None = None
        self._instance_id: str | None = None
        self._fingerprint: str | None = None
        self._revision = 0
        self._last_sync_at: int | None = None
        self._last_sync_error: str | None = None
        self._valid_until: int | None = None
        self._monotonic_until: float | None = None
        self._last_wall = 0
        self._seen_challenges: list[str] = []
        self._listeners: set[Callable[[bool], Awaitable[None]]] = set()
        self._connections: set[Callable[[], Any]] = set()
        self._tasks: set[asyncio.Task[Any]] = set()
        self._lock = asyncio.Lock()
        self._operation_lock = asyncio.Lock()
        self._refresh_task: asyncio.Task[dict[str, Any]] | None = None
        self._maintenance_task: asyncio.Task[None] | None = None
        self._sync_task: asyncio.Task[None] | None = None
        self._running = False
        self._initialized = False
        self._loop: asyncio.AbstractEventLoop | None = None
        self._notify_pending = False
        self._notify_lock = asyncio.Lock()
        self._notified_allowed: bool | None = None
        self._identity_invalid = False
        self._persisted_fence: dict[str, Any] = {}
        self._trusted_epoch: int | None = None
        self._trusted_monotonic = 0.0

    @property
    def allowed(self) -> bool:
        self._check_deadline()
        return self._state in (_ACTIVE, _CACHED)

    @property
    def restricted(self) -> bool:
        return True

    def _now(self) -> int:
        wall = int(time.time())
        if self._trusted_epoch is None:
            return wall
        return max(wall, self._trusted_epoch + int(time.monotonic() - self._trusted_monotonic))

    def _check_deadline(self) -> None:
        if self._last_wall and self._now() < self._last_wall - 300:
            self._state, self._reason = _LOCKED, "clock_anomaly"
            self._valid_until = self._monotonic_until = None
            self._schedule_notify()
        elif (
            self._state in (_ACTIVE, _CACHED)
            and self._valid_until is not None
            and (
                self._now() >= self._valid_until
                or (self._monotonic_until is not None and time.monotonic() >= self._monotonic_until)
            )
        ):
            reason = (
                "expired"
                if self._claims and self._claims["license_expires_at"] <= self._now()
                else "cache_expired"
            )
            self._state, self._reason = _LOCKED, reason
            self._schedule_notify()

    def require_business_access(self) -> None:
        self._check_deadline()
        if not self.allowed:
            raise OctopError(ErrorCode.INSTANCE_LICENSE_LOCKED, "Instance license is not active")

    def status(self) -> dict[str, Any]:
        self._check_deadline()
        return {
            "state": self._state,
            "allowed": self.allowed,
            "reason": None if self.allowed else self._reason,
            "license_expires_at": self._claims.get("license_expires_at") if self._claims else None,
            "valid_until": self._valid_until,
            "last_sync_at": self._last_sync_at,
            "last_sync_error": self._last_sync_error,
            "contact": self.contact,
        }

    def details(self) -> dict[str, Any]:
        return {
            **self.status(),
            "instance_id": self._instance_id,
            "public_key_fingerprint": self._fingerprint,
            "license_revision": self._revision,
        }

    def add_listener(self, callback: Callable[[bool], Awaitable[None]]) -> Callable[[], None]:
        self._listeners.add(callback)
        return lambda: self._listeners.discard(callback)

    def register_connection(self, close_async_callback: Callable[[], Any]) -> Callable[[], None]:
        self._connections.add(close_async_callback)
        return lambda: self._connections.discard(close_async_callback)

    def _schedule_notify(self) -> None:
        loop = self._loop
        if not self._initialized or loop is None or not loop.is_running() or self._notify_pending:
            return
        self._notify_pending = True
        loop.call_soon_threadsafe(lambda: asyncio.create_task(self._notify()))

    async def _notify(self) -> None:
        async with self._notify_lock:
            self._notify_pending = False
            self._check_deadline()
            is_allowed = (
                self._state in (_ACTIVE, _CACHED)
                and self._valid_until is not None
                and self._now() < self._valid_until
            )
            if self._notified_allowed is is_allowed:
                return
            self._notified_allowed = is_allowed
            failure: Exception | None = None
            if not is_allowed:
                for close_callback in tuple(self._connections):
                    try:
                        result = close_callback()
                        if inspect.isawaitable(result):
                            await result
                    except Exception as exc:
                        failure = failure or exc
                current = asyncio.current_task()
                for task in tuple(self._tasks):
                    if task is not current:
                        task.cancel()
            for listener in tuple(self._listeners):
                try:
                    await listener(is_allowed)
                except Exception as exc:
                    failure = failure or exc
            if failure is not None:
                raise failure

    def _serialize_private(self) -> str:
        assert self._private is not None
        pem = self._private.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        return _b64e(pem)

    async def _persist(self) -> None:
        data = {
            "instance_id": self._instance_id,
            "private_key": self._serialize_private() if self._private else None,
            "token": self._token,
            "last_wall": self._now(),
            "fence": {
                "revision": self._revision,
                "entitlement": self._persisted_fence.get("entitlement"),
                "challenges": self._seen_challenges[-128:],
            },
        }
        raw = _canonical(data)
        await asyncio.to_thread(_atomic_private, self.license_dir / "state.json", raw)

    async def _ensure_identity(self) -> None:
        if self._identity_invalid:
            raise ValueError("protected license identity is invalid")
        if self._private is None:
            self._private = Ed25519PrivateKey.generate()
            self._instance_id = secrets.token_hex(16)
            pub = self._private.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            self._fingerprint = hashlib.sha256(pub).hexdigest()
            await self._persist()
        elif self._instance_id is None:
            self._instance_id = secrets.token_hex(16)
            await self._persist()

    def _public_pem(self) -> str:
        assert self._private is not None
        return (
            self._private.public_key()
            .public_bytes(
                serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            .decode("ascii")
        )

    def _accept_token(
        self,
        token: str,
        *,
        persist: bool = True,
        expected_challenge: str | None = None,
        cached: bool = False,
        elapsed: float | None = None,
    ) -> dict[str, Any]:
        if len(token) > 65536 or token.count(".") != 2:
            raise ValueError("invalid signed license")
        h, p, s = token.split(".")
        header, claims = (
            json.loads(_b64d(h), object_pairs_hook=_unique_object),
            json.loads(_b64d(p), object_pairs_hook=_unique_object),
        )
        if (
            not isinstance(header, dict)
            or header.get("alg") != "EdDSA"
            or header.get("typ") != _TYP
            or set(header) - {"alg", "typ", "kid"}
        ):
            raise ValueError("invalid signed license header")
        kid = header.get("kid")
        if not isinstance(kid, str) or not kid:
            raise ValueError("invalid license signing key id")
        key = self.trusted_keys.get(kid)
        if key is None:
            raise ValueError("untrusted license signing key")
        try:
            key.verify(_b64d(s), f"{h}.{p}".encode("ascii"))
        except InvalidSignature as exc:
            raise ValueError("invalid license signature") from exc
        required = {
            "protocol_version",
            "product",
            "iss",
            "aud",
            "instance_id",
            "instance_key_fingerprint",
            "challenge",
            "license_id",
            "license_revision",
            "status",
            "iat",
            "exp",
            "license_expires_at",
        }
        if not isinstance(claims, dict) or not required <= claims.keys() or set(claims) - required:
            raise ValueError("invalid license claims")
        if (
            type(claims["protocol_version"]) is not int
            or claims["protocol_version"] != 1
            or claims["product"] != _PRODUCT
            or claims["iss"] != _ISSUER
            or claims["aud"] != self._instance_id
            or claims["instance_id"] != self._instance_id
            or claims["instance_key_fingerprint"] != self._fingerprint
        ):
            raise ValueError("license identity or issuer mismatch")
        if claims["status"] not in ("active", "revoked", "expired", "customer_disabled"):
            raise ValueError("invalid license status")
        for field in ("license_revision", "iat", "exp", "license_expires_at"):
            if type(claims[field]) is not int:
                raise ValueError("invalid license timestamp or revision")
        revision = claims["license_revision"]
        if revision < 1 or revision < self._revision:
            raise ValueError("license revision rollback")
        now = self._now()
        if self._last_wall and now < self._last_wall - 300:
            raise ValueError("clock anomaly")
        if claims["iat"] > now + 300:
            raise ValueError("license server clock is too far ahead")
        if elapsed is not None:
            now = max(now, claims["iat"] + math.ceil(elapsed) + 1)
        if claims["exp"] <= now or claims["exp"] <= claims["iat"]:
            raise ValueError("license envelope expired or invalid")
        if expected_challenge is not None and claims["challenge"] != expected_challenge:
            raise ValueError("license challenge mismatch")
        challenge = claims["challenge"]
        if not isinstance(challenge, str) or len(_b64d(challenge)) < 32:
            raise ValueError("invalid license challenge")
        if cached:
            if not self._seen_challenges or challenge != self._seen_challenges[-1]:
                raise ValueError("cached license challenge is missing from replay fence")
        elif challenge in self._seen_challenges:
            raise ValueError("license challenge replay")
        if not isinstance(claims["license_id"], str) or not claims["license_id"]:
            raise ValueError("invalid license identifier")
        entitlement = [
            claims["status"],
            claims["license_expires_at"],
            claims["instance_key_fingerprint"],
        ]
        old_entitlement = self._persisted_fence.get("entitlement")
        expired_transition = (
            revision == self._revision
            and claims["status"] == "expired"
            and isinstance(old_entitlement, list)
            and len(old_entitlement) == 3
            and old_entitlement[0] == "active"
            and old_entitlement[1:] == entitlement[1:]
        )
        if (
            revision == self._revision
            and old_entitlement is not None
            and entitlement != old_entitlement
            and not expired_transition
        ):
            raise ValueError("license entitlement changed without revision")
        if claims["status"] == "active":
            if claims["license_expires_at"] <= now or claims["exp"] != min(
                claims["iat"] + 86400, claims["license_expires_at"]
            ):
                raise ValueError("invalid active license expiry")
            state, reason, deadline = (
                _ACTIVE,
                None,
                min(claims["exp"], claims["license_expires_at"]),
            )
        else:
            if claims["exp"] != claims["iat"] + 300:
                raise ValueError("invalid deny envelope")
            state, reason, deadline = _LOCKED, str(claims["status"]), None
        if revision > self._revision or expired_transition:
            self._persisted_fence["entitlement"] = entitlement
        self._revision = revision
        if not cached:
            self._seen_challenges.append(challenge)
            self._seen_challenges = self._seen_challenges[-128:]
        self._claims, self._token = claims, token
        self._state, self._reason, self._valid_until = state, reason, deadline
        self._monotonic_until = (
            time.monotonic() + max(0, deadline - now) if deadline is not None else None
        )
        if elapsed is not None:
            self._trusted_epoch, self._trusted_monotonic = now, time.monotonic()
        if persist:
            self._last_sync_at = now
        self._schedule_notify()
        return claims

    async def _save_accepted(self) -> None:
        await self._persist()

    def _http_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        assert self.server_url is not None
        context = (
            ssl.create_default_context(cafile=self.ca_file)
            if self.ca_file
            else ssl.create_default_context()
        )
        req = urllib.request.Request(
            self.server_url + path,
            data=_canonical(body),
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10, context=context) as response:
            data = json.loads(response.read(131073))
        if not isinstance(data, dict):
            raise ValueError("invalid licensing response")
        return data

    async def _request_json(self, path: str, body: dict[str, Any]) -> dict[str, Any]:
        return await asyncio.wait_for(asyncio.to_thread(self._http_json, path, body), timeout=10)

    async def _online_exchange(
        self, operation: str, activation_code: str | None = None
    ) -> dict[str, Any]:
        if not self.server_url or not self.trusted_keys:
            raise RuntimeError("configuration_required")
        started = time.monotonic()
        await self._ensure_identity()
        challenge_data = await self._request_json(
            "/api/v1/instance/challenges",
            {
                "instance_id": self._instance_id,
                "operation": operation,
                "public_key": self._public_pem(),
            },
        )
        challenge, expires_at = challenge_data.get("challenge"), challenge_data.get("expires_at")
        if (
            not isinstance(challenge, str)
            or len(_b64d(challenge)) < 32
            or type(expires_at) is not int
            or expires_at <= self._now() - 300
            or expires_at > self._now() + 360
        ):
            raise ValueError("invalid server challenge")
        assert self._private is not None
        claims: dict[str, Any] = {
            "protocol_version": 1,
            "product": _PRODUCT,
            "aud": _AUDIENCE,
            "instance_id": self._instance_id,
            "public_key": self._public_pem(),
            "operation": operation,
            "challenge": challenge,
        }
        if activation_code is not None:
            claims["activation_code"] = activation_code
        h, p = _b64e(_canonical({"typ": _PROOF_TYP, "alg": "EdDSA"})), _b64e(_canonical(claims))
        proof = f"{h}.{p}.{_b64e(self._private.sign(f'{h}.{p}'.encode('ascii')))}"
        reply = await self._request_json(f"/api/v1/instance/{operation}", {"proof": proof})
        token = reply.get("token")
        if not isinstance(token, str):
            raise ValueError("license response missing token")
        self._accept_token(token, expected_challenge=challenge, elapsed=time.monotonic() - started)
        await self._save_accepted()
        self._last_sync_at, self._last_sync_error = self._now(), None
        return self.details()

    def load_cached(self) -> dict[str, Any]:
        """Synchronously load verified local identity/cache for CLI guards."""

        def load() -> dict[str, Any] | None:
            path = self.license_dir / "state.json"
            if not path.exists():
                return None
            try:
                data = json.loads(path.read_text("utf-8"))
                if not isinstance(data, dict):
                    raise ValueError("invalid cached license state")
                return data
            except (OSError, ValueError):
                self._identity_invalid = True
                return {}

        data = load()
        if data:
            try:
                private_raw = _b64d(data["private_key"])
                private = serialization.load_pem_private_key(private_raw, password=None)
                if not isinstance(private, Ed25519PrivateKey):
                    raise ValueError("invalid identity")
                self._private = private
                self._instance_id = data.get("instance_id")
                pub = private.public_key().public_bytes(
                    serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
                )
                self._fingerprint = hashlib.sha256(pub).hexdigest()
                self._persisted_fence = data.get("fence", {})
                self._revision = int(self._persisted_fence.get("revision", 0))
                self._seen_challenges = list(self._persisted_fence.get("challenges", []))[-128:]
                self._last_wall = int(data.get("last_wall", 0))
                token = data.get("token")
                if token:
                    try:
                        self._accept_token(token, persist=False, cached=True)
                        if self._state == _ACTIVE:
                            self._state = _CACHED
                    except ValueError as exc:
                        self._claims = self._token = None
                        self._state = _LOCKED
                        message = str(exc)
                        self._reason = (
                            "clock_anomaly"
                            if "clock anomaly" in message
                            else ("cache_expired" if "expired" in message else "invalid_credential")
                        )
                        self._valid_until = self._monotonic_until = None
            except Exception:
                self._claims = self._token = None
                self._state, self._reason, self._valid_until = _LOCKED, "invalid_credential", None
                if self._private is None:
                    self._identity_invalid = True
        self._check_deadline()
        if not self.trusted_keys:
            self._reason = "configuration_required"
        self._initialized = True
        return self.status()

    async def start(self) -> dict[str, Any]:
        if self._running:
            return self.status()
        self._loop = asyncio.get_running_loop()
        await asyncio.to_thread(self.load_cached)
        await asyncio.to_thread(self._initialize_identity_sync)
        self._initialized = True
        self._running = True
        self._maintenance_task = asyncio.create_task(
            self._maintenance(), name="license-deadline-maintenance"
        )
        self._sync_task = asyncio.create_task(self._sync_loop(), name="license-sync")
        return self.status()

    def _initialize_identity_sync(self) -> None:
        if self._identity_invalid:
            self._state, self._reason = _LOCKED, "invalid_credential"
            self._initialized = True
            return
        if self._private is None:
            self._private = Ed25519PrivateKey.generate()
            self._instance_id = secrets.token_hex(16)
            pub = self._private.public_key().public_bytes(
                serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo
            )
            self._fingerprint = hashlib.sha256(pub).hexdigest()
            _atomic_private(
                self.license_dir / "state.json",
                _canonical(
                    {
                        "instance_id": self._instance_id,
                        "private_key": self._serialize_private(),
                        "token": None,
                        "last_wall": self._now(),
                        "fence": {"revision": 0, "entitlement": None, "challenges": []},
                    }
                ),
            )
        self._initialized = True

    async def stop(self) -> None:
        self._running = False
        tasks = [task for task in (self._maintenance_task, self._sync_task) if task is not None]
        self._maintenance_task = self._sync_task = None
        for task in tasks:
            task.cancel()
        for task in tasks:
            with suppress(asyncio.CancelledError):
                await task

    async def activate(self, code: str) -> dict[str, Any]:
        if not isinstance(code, str) or not code:
            raise ValueError("activation code is required")
        async with self._operation_lock:
            return await asyncio.wait_for(self._online_exchange("activate", code), timeout=10)

    async def refresh(self) -> dict[str, Any]:
        return await self._single_flight(self._serialized_refresh)

    async def _serialized_refresh(self) -> dict[str, Any]:
        async with self._operation_lock:
            try:
                return await asyncio.wait_for(self._online_exchange("sync"), timeout=10)
            except Exception as exc:
                self._last_sync_error = type(exc).__name__
                self._last_sync_at = self._now()
                if self.allowed:
                    self._state = _CACHED
                raise

    async def _single_flight(
        self, operation: Callable[[], Coroutine[Any, Any, dict[str, Any]]]
    ) -> dict[str, Any]:
        async with self._lock:
            existing = self._refresh_task
            if existing is None or existing.done():
                task: asyncio.Task[dict[str, Any]] = asyncio.create_task(operation())
                self._refresh_task = task
            else:
                task = existing
        try:
            return await asyncio.shield(task)
        finally:
            if task is self._refresh_task and task.done():
                self._refresh_task = None

    async def _maintenance(self) -> None:
        while self._running:
            self._check_deadline()
            await asyncio.sleep(1)

    async def _sync_loop(self) -> None:
        retry_delay = 30.0
        while self._running:
            delay: float | None = None
            if self.server_url and self.trusted_keys:
                try:
                    await asyncio.wait_for(self.refresh(), timeout=10)
                except asyncio.CancelledError:
                    raise
                except Exception as exc:
                    self._last_sync_error = type(exc).__name__
                    self._last_sync_at = self._now()
                    ceiling = 3600 if self.allowed else 300
                    delay = min(
                        ceiling, min(retry_delay, ceiling) * (90 + secrets.randbelow(21)) / 100
                    )
                    retry_delay = min(retry_delay * 2, 3600)
                else:
                    retry_delay = 30.0
            await asyncio.sleep(delay if delay is not None else 3600 if self.allowed else 300)

    @asynccontextmanager
    async def business_task(self) -> AsyncIterator[None]:
        self.require_business_access()
        task = asyncio.current_task()
        owns_registration = task is not None and task not in self._tasks
        if owns_registration:
            assert task is not None
            self._tasks.add(task)
        try:
            self.require_business_access()
            yield
        finally:
            if owns_registration:
                assert task is not None
                self._tasks.discard(task)


_CURRENT: LicenseService | None = None


def current_license() -> LicenseService:
    global _CURRENT
    if _CURRENT is None:
        _CURRENT = LicenseService(PathLayout.from_env().root)
        _CURRENT.load_cached()
    return _CURRENT


def set_current_license(service: LicenseService) -> None:
    global _CURRENT
    if not isinstance(service, LicenseService):
        raise TypeError("service must be a LicenseService")
    _CURRENT = service
