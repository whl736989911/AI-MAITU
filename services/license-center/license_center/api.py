from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import time
import warnings
from collections import defaultdict, deque
from collections.abc import Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import jwt
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError
from fastapi import Cookie, Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .crypto import (
    decrypt_secret,
    encrypt_secret,
    fingerprint,
    new_totp_secret,
    sign_license,
    valid_totp,
    verify_proof,
)
from .models import (
    ActivationCreate,
    ActivationRevoke,
    ActivationView,
    AdminCreate,
    AdminEnrollment,
    AdminUserUpdate,
    AdminUserView,
    AuditView,
    ChallengeReply,
    ChallengeRequest,
    CustomerCreate,
    CustomerUpdate,
    CustomerView,
    HealthView,
    InstanceView,
    LicenseAction,
    LicenseView,
    LoginRequest,
    OverviewView,
    ProofRequest,
    ReauthRequest,
    ReplaceRequest,
    SessionReply,
    SigningKeyActivationView,
    SigningKeyStageView,
    SigningKeyView,
    SystemView,
    TokenReply,
    VerifyEnrollment,
)
from .storage import audit, connect, migrate, now, public_id, transaction

SESSION_COOKIE = "maitu_center_session"
SESSION_TTL = 8 * 3600
RECENT_MFA = 600
PASSWORDS = PasswordHasher()
RATE: dict[str, deque[float]] = defaultdict(deque)


def _rate(key: str, limit: int, period: int) -> None:
    current = time.monotonic()
    bucket = RATE[key]
    while bucket and bucket[0] < current - period:
        bucket.popleft()
    if len(bucket) >= limit:
        raise HTTPException(
            429, detail={"error": {"code": "rate_limited", "message": "Too many requests"}}
        )
    bucket.append(current)


async def _db(fn: Callable[..., Any], *args: Any) -> Any:
    return await asyncio.to_thread(fn, *args)


@asynccontextmanager
async def lifespan(_: FastAPI):
    await _db(migrate)
    yield


app = FastAPI(
    title="Maitu Instance License Center API",
    version="1.0.0",
    description="Standalone Ed25519 instance authorization and administrative API",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url=None,
)


@app.middleware("http")
async def enforce_https_outside_loopback(
    request: Request, call_next: Callable[..., Any]
) -> Response:
    if request.url.scheme != "https" and not _is_loopback_request(request):
        return JSONResponse(
            status_code=400,
            content={
                "error": {"code": "https_required", "message": "HTTPS is required outside loopback"}
            },
        )
    return await call_next(request)


@app.exception_handler(HTTPException)
async def http_error(_: Request, exc: HTTPException) -> JSONResponse:
    detail = exc.detail
    if not isinstance(detail, dict) or "error" not in detail:
        detail = {"error": {"code": "request_error", "message": str(detail)}}
    return JSONResponse(status_code=exc.status_code, content=detail, headers=exc.headers)


@app.exception_handler(RequestValidationError)
async def validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={
            "error": {"code": "validation_error", "message": "Request parameters are invalid"}
        },
    )


def _get_user(token: str | None) -> dict[str, Any]:
    if not token:
        raise HTTPException(
            401, detail={"error": {"code": "unauthorized", "message": "Sign-in required"}}
        )
    digest = hashlib.sha256(token.encode()).hexdigest()
    with connect() as conn:
        row = conn.execute(
            "SELECT s.*,u.id AS id,u.username,u.role,u.active FROM admin_sessions s JOIN admin_users u ON u.id=s.user_id WHERE s.token_hash=?",
            (digest,),
        ).fetchone()
    if row is None or not row["active"] or row["expires_at"] <= now():
        raise HTTPException(
            401, detail={"error": {"code": "unauthorized", "message": "Session expired"}}
        )
    return dict(row)


async def current_user(
    token: str | None = Cookie(default=None, alias=SESSION_COOKIE),
) -> dict[str, Any]:
    return await _db(_get_user, token)


def require_roles(*roles: str):
    async def dependency(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
        if user["role"] not in roles:
            raise HTTPException(
                403, detail={"error": {"code": "forbidden", "message": "Insufficient role"}}
            )
        return user

    return dependency


def _csrf_check(user: dict[str, Any], csrf: str | None) -> None:
    if not csrf or not hmac.compare_digest(
        hashlib.sha256(csrf.encode()).hexdigest(), user["csrf_hash"]
    ):
        raise HTTPException(
            403, detail={"error": {"code": "csrf_failed", "message": "CSRF validation failed"}}
        )


def _recent_mfa(user: dict[str, Any]) -> None:
    if now() - user["recent_mfa_at"] > RECENT_MFA:
        raise HTTPException(
            403, detail={"error": {"code": "reauth_required", "message": "Recent MFA is required"}}
        )


def _session_view(user: dict[str, Any], csrf: str) -> dict[str, Any]:
    return {
        "user": {"id": user["id"], "username": user["username"], "role": user["role"]},
        "csrf_token": csrf,
        "timezone": os.environ.get("LICENSE_CENTER_TIMEZONE", "Asia/Shanghai"),
    }


def _login(username: str, password: str, code: str, ip: str) -> tuple[str, str, dict[str, Any]]:
    _rate("login:" + ip, 8, 300)
    with transaction() as conn:
        row = conn.execute(
            "SELECT * FROM admin_users WHERE username=? AND active=1", (username,)
        ).fetchone()
        if row is None:
            PASSWORDS.hash(password)
            raise HTTPException(
                401,
                detail={"error": {"code": "invalid_credentials", "message": "Invalid credentials"}},
            )
        try:
            PASSWORDS.verify(row["password_hash"], password)
        except VerifyMismatchError:
            raise HTTPException(
                401,
                detail={"error": {"code": "invalid_credentials", "message": "Invalid credentials"}},
            ) from None
        secret = decrypt_secret(row["totp_ciphertext"])
        ok, counter = valid_totp(secret, code, row["last_totp_counter"])
        if not ok:
            raise HTTPException(
                401, detail={"error": {"code": "invalid_mfa", "message": "Invalid credentials"}}
            )
        conn.execute("UPDATE admin_users SET last_totp_counter=? WHERE id=?", (counter, row["id"]))
        token, csrf = secrets.token_urlsafe(48), secrets.token_urlsafe(32)
        conn.execute(
            "INSERT INTO admin_sessions(token_hash,user_id,csrf_hash,csrf_ciphertext,expires_at,recent_mfa_at,created_at) VALUES(?,?,?,?,?,?,?)",
            (
                hashlib.sha256(token.encode()).hexdigest(),
                row["id"],
                hashlib.sha256(csrf.encode()).hexdigest(),
                encrypt_secret(csrf),
                now() + SESSION_TTL,
                now(),
                now(),
            ),
        )
        audit(conn, row["username"], "admin.login", "admin_user", str(row["id"]), "{}", "{}")
        return token, csrf, {"id": row["id"], "username": username, "role": row["role"]}


@app.post(
    "/api/v1/admin/login",
    response_model=SessionReply,
    summary="Authenticate with password and TOTP",
)
async def login(body: LoginRequest, request: Request, response: Response) -> dict[str, Any]:
    secure = os.environ.get("LICENSE_CENTER_COOKIE_SECURE", "1") != "0"
    if not secure and not _is_loopback_request(request):
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "insecure_cookie_configuration",
                    "message": "Insecure cookies are limited to loopback",
                }
            },
        )
    token, csrf, user = await _db(
        _login,
        body.username,
        body.password,
        body.totp,
        request.client.host if request.client else "unknown",
    )
    response.set_cookie(
        SESSION_COOKIE,
        token,
        max_age=SESSION_TTL,
        httponly=True,
        secure=secure,
        samesite="strict",
        path="/",
    )
    return _session_view(user, csrf)


def _is_loopback_request(request: Request) -> bool:
    """Host headers are untrusted; plaintext development also needs a local peer."""
    import ipaddress

    if request.client is None:
        return False
    try:
        if not ipaddress.ip_address(request.client.host).is_loopback:
            return False
        host = request.url.hostname or ""
        return host.lower() == "localhost" or ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@app.get(
    "/api/v1/admin/session",
    response_model=SessionReply,
    summary="Read current administrator session",
)
async def session(user: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    csrf = await _db(decrypt_secret, user["csrf_ciphertext"])
    return _session_view(user, csrf)


@app.post("/api/v1/admin/logout", status_code=204, summary="Invalidate current session")
async def logout(
    response: Response,
    user: dict[str, Any] = Depends(current_user),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> Response:
    _csrf_check(user, csrf)
    await _db(_delete_session, user["token_hash"])
    response.delete_cookie(
        SESSION_COOKIE,
        path="/",
        httponly=True,
        secure=os.environ.get("LICENSE_CENTER_COOKIE_SECURE", "1") != "0",
        samesite="strict",
    )
    response.status_code = 204
    return response


def _delete_session(token_hash: str) -> None:
    with transaction() as conn:
        conn.execute("DELETE FROM admin_sessions WHERE token_hash=?", (token_hash,))


@app.post(
    "/api/v1/admin/reauth", response_model=SessionReply, summary="Refresh recent MFA authorization"
)
async def reauth(
    body: ReauthRequest,
    user: dict[str, Any] = Depends(current_user),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    csrf_new, username = await _db(_reauth, user["token_hash"], user["id"], body.totp)
    user["recent_mfa_at"] = now()
    return _session_view(user, csrf_new)


def _reauth(token_hash: str, user_id: int, code: str) -> tuple[str, str]:
    with transaction() as conn:
        row = conn.execute(
            "SELECT totp_ciphertext,last_totp_counter,username FROM admin_users WHERE id=?",
            (user_id,),
        ).fetchone()
        ok, counter = valid_totp(
            decrypt_secret(row["totp_ciphertext"]), code, row["last_totp_counter"]
        )
        if not ok:
            raise HTTPException(
                401, detail={"error": {"code": "invalid_mfa", "message": "Invalid TOTP"}}
            )
        conn.execute("UPDATE admin_users SET last_totp_counter=? WHERE id=?", (counter, user_id))
        csrf = secrets.token_urlsafe(32)
        conn.execute(
            "UPDATE admin_sessions SET recent_mfa_at=?,csrf_hash=?,csrf_ciphertext=? WHERE token_hash=?",
            (now(), hashlib.sha256(csrf.encode()).hexdigest(), encrypt_secret(csrf), token_hash),
        )
        audit(conn, row["username"], "admin.reauth", "admin_user", str(user_id), "{}", "{}")
        return csrf, row["username"]


@app.post(
    "/api/v1/instance/challenges",
    response_model=ChallengeReply,
    summary="Issue a one-time proof challenge",
)
async def challenge(body: ChallengeRequest, request: Request) -> dict[str, int | str]:
    return await _db(_create_challenge, body, request.client.host if request.client else "unknown")


def _create_challenge(body: ChallengeRequest, ip: str) -> dict[str, int | str]:
    _rate(f"challenge:{ip}", 60, 60)
    try:
        fp = fingerprint(body.public_key)
    except Exception as exc:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "invalid_public_key",
                    "message": "Expected Ed25519 PEM public key",
                }
            },
        ) from exc
    with transaction() as conn:
        conn.execute("DELETE FROM instance_challenges WHERE expires_at<=?", (now() - 300,))
        if body.operation == "sync":
            row = conn.execute(
                "SELECT fingerprint FROM instances WHERE id=? AND status='active'",
                (body.instance_id,),
            ).fetchone()
            if row is None or not hmac.compare_digest(row["fingerprint"], fp):
                raise HTTPException(
                    401,
                    detail={
                        "error": {
                            "code": "invalid_instance",
                            "message": "Instance authentication failed",
                        }
                    },
                )
        challenge_value = secrets.token_urlsafe(48)
        expires = now() + 60
        conn.execute(
            "INSERT INTO instance_challenges(digest,instance_id,operation,fingerprint,expires_at) VALUES(?,?,?,?,?)",
            (
                hashlib.sha256(challenge_value.encode()).hexdigest(),
                body.instance_id,
                body.operation,
                fp,
                expires,
            ),
        )
        return {"challenge": challenge_value, "expires_at": expires}


def _consume_challenge(conn: Any, claims: dict[str, Any], op: str) -> Any:
    challenge_value = claims.get("challenge")
    if not isinstance(challenge_value, str):
        raise HTTPException(
            400, detail={"error": {"code": "invalid_proof", "message": "Missing challenge"}}
        )
    digest = hashlib.sha256(challenge_value.encode()).hexdigest()
    row = conn.execute("SELECT * FROM instance_challenges WHERE digest=?", (digest,)).fetchone()
    if (
        row is None
        or row["consumed_at"] is not None
        or row["expires_at"] <= now()
        or row["operation"] != op
        or row["instance_id"] != claims.get("instance_id")
    ):
        raise HTTPException(
            409,
            detail={
                "error": {
                    "code": "challenge_invalid",
                    "message": "Challenge expired or already used",
                }
            },
        )
    conn.execute(
        "UPDATE instance_challenges SET consumed_at=? WHERE digest=? AND consumed_at IS NULL",
        (now(), digest),
    )
    if conn.execute("SELECT changes() as n").fetchone()["n"] != 1:
        raise HTTPException(
            409,
            detail={"error": {"code": "challenge_invalid", "message": "Challenge already used"}},
        )
    return row


def _mark_expired(conn: Any, instance: Any) -> Any:
    if not instance["revoked"] and not instance["expired"] and instance["expires_at"] <= now():
        before = {
            "expires_at": instance["expires_at"],
            "revoked": instance["revoked"],
            "expired": False,
            "revision": instance["revision"],
        }
        conn.execute(
            "UPDATE licenses SET expired=1,revision=revision+1 WHERE id=? AND expired=0",
            (instance["license_id"],),
        )
        after = conn.execute(
            "SELECT * FROM licenses WHERE id=?", (instance["license_id"],)
        ).fetchone()
        audit(
            conn,
            "system",
            "license.expire",
            "license",
            instance["license_id"],
            json.dumps(before),
            json.dumps(dict(after)),
        )
        instance = conn.execute(
            "SELECT i.*,l.id AS license_id,l.expires_at,l.revoked,l.expired,l.revision,c.active AS customer_active FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id WHERE i.id=?",
            (instance["id"],),
        ).fetchone()
    return instance


def _issue(instance: Any, challenge_value: str) -> str:
    current = now()
    customer_active = instance["customer_active"]
    status = (
        "active"
        if not instance["revoked"]
        and not instance["expired"]
        and customer_active
        and instance["expires_at"] > current
        and instance["status"] == "active"
        else (
            "revoked"
            if instance["revoked"] or instance["status"] != "active"
            else "customer_disabled"
            if not customer_active
            else "expired"
        )
    )
    expires = min(current + 86400, instance["expires_at"]) if status == "active" else current + 300
    claims = {
        "protocol_version": 1,
        "product": "maitu",
        "iss": "maitu-license-center",
        "aud": instance["id"],
        "instance_id": instance["id"],
        "instance_key_fingerprint": instance["fingerprint"],
        "challenge": challenge_value,
        "license_id": instance["license_id"],
        "license_revision": instance["revision"],
        "status": status,
        "iat": current,
        "exp": expires,
        "license_expires_at": instance["expires_at"],
    }
    return sign_license(claims)


def _prove(body: ProofRequest, op: str) -> str:
    try:
        header = jwt.get_unverified_header(body.proof)
        unverified = jwt.decode(
            body.proof,
            options={"verify_signature": False, "verify_exp": False, "verify_aud": False},
        )
    except Exception as exc:
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        ) from exc
    if (
        header.get("alg") != "EdDSA"
        or header.get("typ") != "maitu-instance-proof"
        or unverified.get("operation") != op
    ):
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        )
    return str(unverified.get("public_key", ""))


def _activate(body: ProofRequest) -> dict[str, str]:
    public_pem = _prove(body, "activate")
    try:
        fp = fingerprint(public_pem)
    except Exception as exc:
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        ) from exc
    claims = jwt.decode(
        body.proof, options={"verify_signature": False, "verify_exp": False, "verify_aud": False}
    )
    try:
        verify_proof(body.proof, {"operation": "activate", "public_key": public_pem}, public_pem)
    except Exception as exc:
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        ) from exc
    with transaction() as conn:
        challenge = _consume_challenge(conn, claims, "activate")
        if challenge["fingerprint"] != fp:
            raise HTTPException(
                401,
                detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}},
            )
        instance_id = claims.get("instance_id")
        code = claims.get("activation_code")
        if not isinstance(instance_id, str) or not isinstance(code, str):
            raise HTTPException(
                401,
                detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}},
            )
        code_hash = hashlib.sha256(code.encode()).hexdigest()
        activation = conn.execute(
            "SELECT * FROM activation_codes WHERE digest=?", (code_hash,)
        ).fetchone()
        existing = conn.execute(
            "SELECT i.*,l.id AS license_id,l.expires_at,l.revoked,l.expired,l.revision,c.active AS customer_active FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id WHERE i.id=?",
            (instance_id,),
        ).fetchone()
        if activation is None:
            # Idempotent recovery after the activation code was used by this exact binding.
            if existing is None or not hmac.compare_digest(existing["fingerprint"], fp):
                raise HTTPException(
                    401,
                    detail={
                        "error": {
                            "code": "activation_invalid",
                            "message": "Activation could not be completed",
                        }
                    },
                )
            instance = existing
        else:
            replay_for_same_binding = (
                activation["used_at"] is not None
                and activation["bound_instance_id"] == instance_id
                and existing is not None
                and existing["fingerprint"] == fp
            )
            if activation["revoked_at"] is not None or (
                activation["expires_at"] <= now() and not replay_for_same_binding
            ):
                raise HTTPException(
                    401,
                    detail={
                        "error": {
                            "code": "activation_invalid",
                            "message": "Activation could not be completed",
                        }
                    },
                )
            if activation["used_at"] is not None and not replay_for_same_binding:
                raise HTTPException(
                    409,
                    detail={
                        "error": {
                            "code": "activation_used",
                            "message": "Activation code has already been used",
                        }
                    },
                )
            customer = conn.execute(
                "SELECT * FROM customers WHERE id=? AND active=1", (activation["customer_id"],)
            ).fetchone()
            if customer is None:
                raise HTTPException(
                    401,
                    detail={
                        "error": {
                            "code": "activation_invalid",
                            "message": "Activation could not be completed",
                        }
                    },
                )
            if existing is None:
                if conn.execute("SELECT 1 FROM instances WHERE fingerprint=?", (fp,)).fetchone():
                    raise HTTPException(
                        409,
                        detail={
                            "error": {
                                "code": "key_already_bound",
                                "message": "Public key is already bound",
                            }
                        },
                    )
                conn.execute(
                    "INSERT INTO instances(id,customer_id,public_key,fingerprint,status,last_seen_at) VALUES(?,?,?,?,?,?)",
                    (instance_id, customer["id"], public_pem, fp, "active", now()),
                )
                lid = public_id("lic")
                conn.execute(
                    "INSERT INTO licenses(id,instance_id,expires_at,revision,created_at) VALUES(?,?,?,1,?)",
                    (lid, instance_id, activation["license_expires_at"], now()),
                )
            elif (
                existing["customer_id"] != activation["customer_id"]
                or existing["fingerprint"] != fp
            ):
                raise HTTPException(
                    409,
                    detail={
                        "error": {
                            "code": "instance_conflict",
                            "message": "Instance identity does not match",
                        }
                    },
                )
            conn.execute(
                "UPDATE activation_codes SET used_at=COALESCE(used_at,?),bound_instance_id=? WHERE id=?",
                (now(), instance_id, activation["id"]),
            )
            instance = conn.execute(
                "SELECT i.*,l.id AS license_id,l.expires_at,l.revoked,l.expired,l.revision,c.active AS customer_active FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id WHERE i.id=?",
                (instance_id,),
            ).fetchone()
        instance = _mark_expired(conn, instance)
        conn.execute("UPDATE instances SET last_seen_at=? WHERE id=?", (now(), instance_id))
        audit(
            conn,
            "instance",
            "instance.activated",
            "instance",
            instance_id,
            "{}",
            json.dumps({"fingerprint": fp}),
        )
        return {"token": _issue(instance, claims["challenge"])}


@app.post(
    "/api/v1/instance/activate",
    response_model=TokenReply,
    summary="Activate an instance using a one-time code",
)
async def activate(body: ProofRequest) -> dict[str, str]:
    return await _db(_activate, body)


def _sync(body: ProofRequest) -> dict[str, str]:
    public_pem = _prove(body, "sync")
    try:
        fp = fingerprint(public_pem)
    except Exception as exc:
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        ) from exc
    claims = jwt.decode(
        body.proof, options={"verify_signature": False, "verify_exp": False, "verify_aud": False}
    )
    try:
        verify_proof(body.proof, {"operation": "sync", "public_key": public_pem}, public_pem)
    except Exception as exc:
        raise HTTPException(
            401, detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}}
        ) from exc
    with transaction() as conn:
        challenge = _consume_challenge(conn, claims, "sync")
        if challenge["fingerprint"] != fp:
            raise HTTPException(
                401,
                detail={"error": {"code": "invalid_proof", "message": "Invalid instance proof"}},
            )
        instance = conn.execute(
            "SELECT i.*,l.id AS license_id,l.expires_at,l.revoked,l.expired,l.revision,c.active AS customer_active FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id WHERE i.id=?",
            (claims.get("instance_id"),),
        ).fetchone()
        if (
            instance is None
            or instance["fingerprint"] != fp
            or instance["public_key"] != public_pem
        ):
            raise HTTPException(
                401,
                detail={
                    "error": {
                        "code": "invalid_instance",
                        "message": "Instance authentication failed",
                    }
                },
            )
        instance = _mark_expired(conn, instance)
        conn.execute("UPDATE instances SET last_seen_at=? WHERE id=?", (now(), instance["id"]))
        return {"token": _issue(instance, claims["challenge"])}


@app.post(
    "/api/v1/instance/sync",
    response_model=TokenReply,
    summary="Synchronize signed authorization state",
)
async def sync(body: ProofRequest) -> dict[str, str]:
    return await _db(_sync, body)


def _dict(row: Any) -> dict[str, Any]:
    result = dict(row)
    result.pop("pk", None)
    return result


@app.get("/api/v1/admin/customers", response_model=list[CustomerView], summary="List customers")
async def customers(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(r)
            for r in connect()
            .execute("SELECT * FROM customers ORDER BY created_at DESC")
            .fetchall()
        ]
    )


@app.post(
    "/api/v1/admin/customers",
    response_model=CustomerView,
    status_code=201,
    summary="Create customer",
)
async def create_customer(
    body: CustomerCreate,
    user: dict[str, Any] = Depends(require_roles("owner", "operator")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_create_customer, body, user["username"])


def _create_customer(body: CustomerCreate, actor: str) -> dict[str, Any]:
    with transaction() as conn:
        cid = public_id("cus")
        conn.execute(
            "INSERT INTO customers(id,name,contact,notes,created_at) VALUES(?,?,?,?,?)",
            (cid, body.name, body.contact, body.notes, now()),
        )
        row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        audit(conn, actor, "customer.create", "customer", cid, "{}", json.dumps(dict(row)))
        return _dict(row)


@app.put(
    "/api/v1/admin/customers/{customer_id}",
    response_model=CustomerView,
    summary="Update customer with version check",
)
async def update_customer(
    customer_id: str,
    body: CustomerUpdate,
    user: dict[str, Any] = Depends(require_roles("owner", "operator")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_update_customer, customer_id, body, user["username"])


def _update_customer(cid: str, body: CustomerUpdate, actor: str) -> dict[str, Any]:
    with transaction() as conn:
        old = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        if old is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Customer not found"}}
            )
        if old["version"] != body.version:
            raise HTTPException(
                409,
                detail={"error": {"code": "version_conflict", "message": "Customer was changed"}},
            )
        status_changed = bool(old["active"]) != body.active
        if status_changed and not body.reason:
            raise HTTPException(
                400,
                detail={
                    "error": {
                        "code": "reason_required",
                        "message": "A reason is required to change customer status",
                    }
                },
            )
        licenses = (
            conn.execute(
                "SELECT l.* FROM licenses l JOIN instances i ON i.id=l.instance_id WHERE i.customer_id=?",
                (cid,),
            ).fetchall()
            if status_changed
            else []
        )
        conn.execute(
            "UPDATE customers SET name=?,contact=?,notes=?,active=?,version=version+1 WHERE id=? AND version=?",
            (body.name, body.contact, body.notes, int(body.active), cid, body.version),
        )
        if status_changed:
            conn.execute(
                "UPDATE licenses SET revision=revision+1 WHERE instance_id IN (SELECT id FROM instances WHERE customer_id=?)",
                (cid,),
            )
            for license_before in licenses:
                license_after = conn.execute(
                    "SELECT * FROM licenses WHERE id=?", (license_before["id"],)
                ).fetchone()
                audit(
                    conn,
                    actor,
                    "license.customer_status_change",
                    "license",
                    license_before["id"],
                    json.dumps(dict(license_before)),
                    json.dumps(dict(license_after)),
                    body.reason or "",
                )
        row = conn.execute("SELECT * FROM customers WHERE id=?", (cid,)).fetchone()
        audit(
            conn,
            actor,
            "customer.update",
            "customer",
            cid,
            json.dumps(dict(old)),
            json.dumps(dict(row)),
            body.reason or "",
        )
        return _dict(row)


@app.get(
    "/api/v1/admin/instances",
    response_model=list[InstanceView],
    summary="List registered instances",
)
async def instances(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(r)
            for r in connect()
            .execute(
                "SELECT i.id,i.customer_id,i.fingerprint AS public_key_fingerprint,i.status,i.last_seen_at,l.id AS license_id,l.expires_at AS license_expires_at,CASE WHEN l.revoked THEN 'revoked' WHEN c.active=0 THEN 'customer_disabled' WHEN l.expires_at<=strftime('%s','now') THEN 'expired' ELSE 'active' END AS license_status,l.revision AS license_revision FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id ORDER BY i.last_seen_at DESC"
            )
            .fetchall()
        ]
    )


@app.get(
    "/api/v1/admin/instances/{instance_id}",
    response_model=InstanceView,
    summary="Get instance details",
)
async def instance_detail(
    instance_id: str, _: dict[str, Any] = Depends(current_user)
) -> dict[str, Any]:
    def query() -> dict[str, Any]:
        with connect() as conn:
            row = conn.execute(
                "SELECT i.id,i.customer_id,i.fingerprint AS public_key_fingerprint,i.status,i.last_seen_at,l.id AS license_id,l.expires_at AS license_expires_at,CASE WHEN l.revoked THEN 'revoked' WHEN c.active=0 THEN 'customer_disabled' WHEN l.expires_at<=strftime('%s','now') THEN 'expired' ELSE 'active' END AS license_status,l.revision AS license_revision FROM instances i JOIN licenses l ON l.instance_id=i.id JOIN customers c ON c.id=i.customer_id WHERE i.id=?",
                (instance_id,),
            ).fetchone()
        if row is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Instance not found"}}
            )
        return _dict(row)

    return await _db(query)


@app.post(
    "/api/v1/admin/activation-codes",
    response_model=ActivationView,
    status_code=201,
    summary="Create one-time activation code",
)
async def create_activation(
    body: ActivationCreate,
    user: dict[str, Any] = Depends(require_roles("owner", "operator")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_create_activation, body, user["username"])


def _create_activation(body: ActivationCreate, actor: str) -> dict[str, Any]:
    code = secrets.token_urlsafe(40)
    with transaction() as conn:
        customer = conn.execute(
            "SELECT id FROM customers WHERE id=? AND active=1", (body.customer_id,)
        ).fetchone()
        if customer is None:
            raise HTTPException(
                404,
                detail={
                    "error": {"code": "customer_not_found", "message": "Active customer not found"}
                },
            )
        if body.expires_at <= now():
            raise HTTPException(
                400,
                detail={
                    "error": {
                        "code": "invalid_expiry",
                        "message": "License expiry must be in the future",
                    }
                },
            )
        aid = public_id("act")
        expiry = now() + body.ttl_seconds
        conn.execute(
            "INSERT INTO activation_codes(id,digest,customer_id,license_expires_at,expires_at) VALUES(?,?,?,?,?)",
            (
                aid,
                hashlib.sha256(code.encode()).hexdigest(),
                body.customer_id,
                body.expires_at,
                expiry,
            ),
        )
        audit(
            conn,
            actor,
            "activation.create",
            "activation",
            aid,
            "{}",
            json.dumps(
                {
                    "customer_id": body.customer_id,
                    "activation_expires_at": expiry,
                    "license_expires_at": body.expires_at,
                }
            ),
        )
    return {
        "id": aid,
        "customer_id": body.customer_id,
        "code": code,
        "expires_at": expiry,
        "license_expires_at": body.expires_at,
        "used_at": None,
        "revoked_at": None,
    }


@app.get(
    "/api/v1/admin/activation-codes",
    response_model=list[ActivationView],
    summary="List activation code metadata",
)
async def activation_codes(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(r)
            for r in connect()
            .execute(
                "SELECT id,customer_id,expires_at,license_expires_at,used_at,revoked_at FROM activation_codes ORDER BY expires_at DESC"
            )
            .fetchall()
        ]
    )


@app.post(
    "/api/v1/admin/activation-codes/{activation_id}/revoke",
    response_model=ActivationView,
    summary="Revoke unused activation code with reason",
)
async def revoke_activation(
    activation_id: str,
    body: ActivationRevoke,
    user: dict[str, Any] = Depends(require_roles("owner", "operator")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_revoke_activation, activation_id, body.reason, user["username"])


def _revoke_activation(aid: str, reason: str, actor: str) -> dict[str, Any]:
    with transaction() as conn:
        row = conn.execute(
            "SELECT id,customer_id,expires_at,license_expires_at,used_at,revoked_at FROM activation_codes WHERE id=?",
            (aid,),
        ).fetchone()
        if row is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Activation code not found"}}
            )
        if row["used_at"] is not None:
            raise HTTPException(
                409,
                detail={
                    "error": {"code": "activation_used", "message": "Used code cannot be revoked"}
                },
            )
        if row["revoked_at"] is not None:
            raise HTTPException(
                409,
                detail={
                    "error": {
                        "code": "already_revoked",
                        "message": "Activation code is already revoked",
                    }
                },
            )
        revoked_at = now()
        conn.execute("UPDATE activation_codes SET revoked_at=? WHERE id=?", (revoked_at, aid))
        audit(
            conn,
            actor,
            "activation.revoke",
            "activation",
            aid,
            json.dumps(dict(row)),
            json.dumps({**dict(row), "revoked_at": revoked_at}),
            reason,
        )
        return {**dict(row), "revoked_at": revoked_at}


@app.get("/api/v1/admin/licenses", response_model=list[LicenseView], summary="List licenses")
async def licenses(_: dict[str, Any] = Depends(current_user)) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(r)
            for r in connect()
            .execute(
                "SELECT l.*,i.customer_id FROM licenses l JOIN instances i ON i.id=l.instance_id ORDER BY l.created_at DESC"
            )
            .fetchall()
        ]
    )


def _license_action(
    license_id: str, action: str, body: LicenseAction, actor: str
) -> dict[str, Any]:
    with transaction() as conn:
        old = conn.execute(
            "SELECT l.*,i.customer_id FROM licenses l JOIN instances i ON i.id=l.instance_id WHERE l.id=?",
            (license_id,),
        ).fetchone()
        if old is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "License not found"}}
            )
        if old["revision"] != body.version:
            raise HTTPException(
                409,
                detail={"error": {"code": "version_conflict", "message": "License was changed"}},
            )
        exp = body.expires_at if body.expires_at is not None else old["expires_at"]
        revoked = 1 if action == "revoke" else 0 if action == "restore" else old["revoked"]
        expired = 0 if action in {"extend", "restore"} else old["expired"]
        if action == "restore" and not old["revoked"]:
            raise HTTPException(
                409,
                detail={
                    "error": {
                        "code": "license_not_revoked",
                        "message": "Only a revoked license can be restored",
                    }
                },
            )
        if action == "restore" and exp <= now():
            raise HTTPException(
                400,
                detail={
                    "error": {
                        "code": "expiry_required",
                        "message": "Restoring a license requires a future expiry",
                    }
                },
            )
        if action == "extend" and (
            body.expires_at is None
            or body.expires_at <= old["expires_at"]
            or body.expires_at <= now()
        ):
            raise HTTPException(
                400,
                detail={
                    "error": {
                        "code": "invalid_extension",
                        "message": "New expiry must be later and in the future",
                    }
                },
            )
        if action == "revoke" and old["revoked"]:
            raise HTTPException(
                409,
                detail={
                    "error": {"code": "already_revoked", "message": "License is already revoked"}
                },
            )
        conn.execute(
            "UPDATE licenses SET expires_at=?,revoked=?,expired=?,revision=revision+1 WHERE id=? AND revision=?",
            (exp, revoked, expired, license_id, body.version),
        )
        after = conn.execute(
            "SELECT l.*,i.customer_id FROM licenses l JOIN instances i ON i.id=l.instance_id WHERE l.id=?",
            (license_id,),
        ).fetchone()
        audit(
            conn,
            actor,
            f"license.{action}",
            "license",
            license_id,
            json.dumps(dict(old)),
            json.dumps(dict(after)),
            body.reason,
        )
        return _dict(after)


def license_action(action: str):
    async def endpoint(
        license_id: str,
        body: LicenseAction,
        user: dict[str, Any] = Depends(require_roles("owner", "operator")),
        csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
    ) -> dict[str, Any]:
        _csrf_check(user, csrf)
        _recent_mfa(user)
        return await _db(_license_action, license_id, action, body, user["username"])

    return endpoint


app.post(
    "/api/v1/admin/licenses/{license_id}/extend",
    summary="Extend a license",
    response_model=LicenseView,
)(license_action("extend"))
app.post(
    "/api/v1/admin/licenses/{license_id}/revoke",
    summary="Revoke a license",
    response_model=LicenseView,
)(license_action("revoke"))
app.post(
    "/api/v1/admin/licenses/{license_id}/restore",
    summary="Restore a revoked license",
    response_model=LicenseView,
)(license_action("restore"))


@app.post(
    "/api/v1/admin/instances/{instance_id}/replace",
    response_model=ActivationView,
    summary="Revoke old binding and create replacement code",
)
async def replace_instance(
    instance_id: str,
    body: ReplaceRequest,
    user: dict[str, Any] = Depends(require_roles("owner", "operator")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_replace_instance, instance_id, body, user["username"])


def _replace_instance(instance_id: str, body: ReplaceRequest, actor: str) -> dict[str, Any]:
    code = secrets.token_urlsafe(40)
    with transaction() as conn:
        old = conn.execute(
            "SELECT i.customer_id,i.status,l.id,l.expires_at,l.revision,l.revoked FROM instances i JOIN licenses l ON l.instance_id=i.id WHERE i.id=?",
            (instance_id,),
        ).fetchone()
        if old is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Instance not found"}}
            )
        if old["revision"] != body.version:
            raise HTTPException(
                409,
                detail={"error": {"code": "version_conflict", "message": "License was changed"}},
            )
        if old["status"] != "active" or old["revoked"]:
            raise HTTPException(
                409,
                detail={
                    "error": {
                        "code": "instance_unavailable",
                        "message": "Only an active instance can be replaced",
                    }
                },
            )
        conn.execute("UPDATE instances SET status='replaced' WHERE id=?", (instance_id,))
        conn.execute("UPDATE licenses SET revoked=1,revision=revision+1 WHERE id=?", (old["id"],))
        aid = public_id("act")
        expiry = now() + 86400
        license_exp = old["expires_at"]
        conn.execute(
            "INSERT INTO activation_codes(id,digest,customer_id,license_expires_at,expires_at) VALUES(?,?,?,?,?)",
            (
                aid,
                hashlib.sha256(code.encode()).hexdigest(),
                old["customer_id"],
                license_exp,
                expiry,
            ),
        )
        audit(
            conn,
            actor,
            "instance.replace",
            "instance",
            instance_id,
            json.dumps(dict(old)),
            json.dumps({"activation_id": aid, "expires_at": license_exp}),
            body.reason,
        )
        return {
            "id": aid,
            "customer_id": old["customer_id"],
            "code": code,
            "expires_at": expiry,
            "license_expires_at": license_exp,
            "used_at": None,
            "revoked_at": None,
        }


@app.get("/api/v1/admin/audit", response_model=list[AuditView], summary="Read audit log")
async def audit_records(
    limit: int = 100, before_id: int | None = None, _: dict[str, Any] = Depends(current_user)
) -> list[dict[str, Any]]:
    if limit < 1 or limit > 500:
        raise HTTPException(
            400, detail={"error": {"code": "invalid_limit", "message": "Limit must be 1..500"}}
        )

    def query() -> list[dict[str, Any]]:
        with connect() as conn:
            if before_id is None:
                rows = conn.execute(
                    "SELECT * FROM audit_log ORDER BY id DESC LIMIT ?", (limit,)
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM audit_log WHERE id<? ORDER BY id DESC LIMIT ?",
                    (before_id, limit),
                ).fetchall()
            return [_dict(r) for r in rows]

    return await _db(query)


@app.get(
    "/api/v1/admin/keys", response_model=list[SigningKeyView], summary="List public signing keys"
)
async def signing_keys(
    _: dict[str, Any] = Depends(require_roles("owner", "operator", "reader")),
) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(r)
            for r in connect()
            .execute(
                "SELECT kid,public_pem,state,created_at FROM signing_keys ORDER BY created_at DESC"
            )
            .fetchall()
        ]
    )


@app.exception_handler(Exception)
async def unexpected_error(_: Request, exc: Exception) -> JSONResponse:
    # Never include request data or secret-bearing exception text in the response/log.
    return JSONResponse(
        status_code=500,
        content={"error": {"code": "internal_error", "message": "Request could not be completed"}},
    )


@app.get(
    "/api/v1/admin/overview",
    response_model=OverviewView,
    summary="Get license-center operational totals",
)
async def overview(_: dict[str, Any] = Depends(current_user)) -> dict[str, Any]:
    def query() -> dict[str, Any]:
        with connect() as conn:
            return {
                "customers": conn.execute("SELECT count(*) FROM customers").fetchone()[0],
                "active_customers": conn.execute(
                    "SELECT count(*) FROM customers WHERE active=1"
                ).fetchone()[0],
                "instances": conn.execute("SELECT count(*) FROM instances").fetchone()[0],
                "active_licenses": conn.execute(
                    "SELECT count(*) FROM licenses WHERE revoked=0 AND expires_at>?", (now(),)
                ).fetchone()[0],
                "expired_licenses": conn.execute(
                    "SELECT count(*) FROM licenses WHERE revoked=0 AND expires_at<=?", (now(),)
                ).fetchone()[0],
                "revoked_licenses": conn.execute(
                    "SELECT count(*) FROM licenses WHERE revoked=1"
                ).fetchone()[0],
                "expiring_soon": conn.execute(
                    "SELECT count(*) FROM licenses WHERE revoked=0 AND expires_at>? AND expires_at<=?",
                    (now(), now() + 30 * 86400),
                ).fetchone()[0],
                "recent_contacts": conn.execute(
                    "SELECT id,last_seen_at FROM instances WHERE last_seen_at IS NOT NULL ORDER BY last_seen_at DESC LIMIT 10"
                ).fetchall(),
            }

    result = await _db(query)
    result["recent_contacts"] = [_dict(row) for row in result["recent_contacts"]]
    result["timezone"] = os.environ.get("LICENSE_CENTER_TIMEZONE", "Asia/Shanghai")
    return result


@app.get(
    "/api/v1/admin/users", response_model=list[AdminUserView], summary="List administrator accounts"
)
async def admin_users(_: dict[str, Any] = Depends(require_roles("owner"))) -> list[dict[str, Any]]:
    return await _db(
        lambda: [
            _dict(row)
            for row in connect()
            .execute("SELECT id,username,role,active,created_at FROM admin_users ORDER BY id")
            .fetchall()
        ]
    )


@app.post(
    "/api/v1/admin/users",
    response_model=AdminEnrollment,
    status_code=201,
    summary="Create administrator and begin TOTP enrollment",
)
async def create_admin(
    body: AdminCreate,
    user: dict[str, Any] = Depends(require_roles("owner")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_create_admin, body, user["username"])


def _create_admin(body: AdminCreate, actor: str) -> dict[str, Any]:
    secret = new_totp_secret()
    with transaction() as conn:
        try:
            cur = conn.execute(
                "INSERT INTO admin_users(username,password_hash,role,totp_ciphertext,active,created_at) VALUES(?,?,?,?,0,?)",
                (
                    body.username,
                    PASSWORDS.hash(body.password),
                    body.role,
                    encrypt_secret(secret),
                    now(),
                ),
            )
        except Exception as exc:
            if "UNIQUE" in str(exc):
                raise HTTPException(
                    409,
                    detail={
                        "error": {"code": "username_exists", "message": "Username already exists"}
                    },
                ) from exc
            raise
        uid = cur.lastrowid
        audit(
            conn,
            actor,
            "admin.create",
            "admin_user",
            str(uid),
            "{}",
            json.dumps({"username": body.username, "role": body.role, "active": False}),
        )
    import pyotp

    uri = pyotp.TOTP(secret).provisioning_uri(
        name=body.username, issuer_name="Maitu License Center"
    )
    return {
        "id": uid,
        "username": body.username,
        "role": body.role,
        "totp_secret": secret,
        "provisioning_uri": uri,
        "active": False,
    }


@app.post(
    "/api/v1/admin/users/{user_id}/enroll",
    response_model=AdminUserView,
    summary="Verify first TOTP and activate administrator",
)
async def enroll_admin(
    user_id: int,
    body: VerifyEnrollment,
    user: dict[str, Any] = Depends(require_roles("owner")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    return await _db(_enroll_admin, user_id, body.totp, user["username"])


def _enroll_admin(user_id: int, code: str, actor: str) -> dict[str, Any]:
    with transaction() as conn:
        row = conn.execute("SELECT * FROM admin_users WHERE id=?", (user_id,)).fetchone()
        if row is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Administrator not found"}}
            )
        if row["active"]:
            raise HTTPException(
                409,
                detail={
                    "error": {
                        "code": "already_enrolled",
                        "message": "Administrator is already active",
                    }
                },
            )
        ok, counter = valid_totp(
            decrypt_secret(row["totp_ciphertext"]), code, row["last_totp_counter"]
        )
        if not ok:
            raise HTTPException(
                401, detail={"error": {"code": "invalid_mfa", "message": "Invalid TOTP"}}
            )
        conn.execute(
            "UPDATE admin_users SET active=1,last_totp_counter=? WHERE id=?", (counter, user_id)
        )
        audit(
            conn,
            actor,
            "admin.enroll",
            "admin_user",
            str(user_id),
            json.dumps({"active": False}),
            json.dumps({"active": True}),
        )
        updated = conn.execute(
            "SELECT id,username,role,active,created_at FROM admin_users WHERE id=?", (user_id,)
        ).fetchone()
        return _dict(updated)


@app.put(
    "/api/v1/admin/users/{user_id}",
    response_model=AdminUserView,
    summary="Change administrator role or disable account",
)
async def update_admin(
    user_id: int,
    body: AdminUserUpdate,
    user: dict[str, Any] = Depends(require_roles("owner")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, Any]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    if user_id == user["id"] and not body.active:
        raise HTTPException(
            400,
            detail={
                "error": {
                    "code": "self_disable",
                    "message": "Use a separate owner account to disable this account",
                }
            },
        )
    return await _db(_update_admin, user_id, body, user["username"])


def _update_admin(user_id: int, body: AdminUserUpdate, actor: str) -> dict[str, Any]:
    with transaction() as conn:
        old = conn.execute(
            "SELECT id,username,role,active,created_at,totp_ciphertext,last_totp_counter FROM admin_users WHERE id=?",
            (user_id,),
        ).fetchone()
        if old is None:
            raise HTTPException(
                404, detail={"error": {"code": "not_found", "message": "Administrator not found"}}
            )
        if old["role"] == "owner" and old["active"] and (body.role != "owner" or not body.active):
            owners = conn.execute(
                "SELECT count(*) FROM admin_users WHERE role='owner' AND active=1"
            ).fetchone()[0]
            if owners <= 1:
                raise HTTPException(
                    409,
                    detail={
                        "error": {
                            "code": "last_owner",
                            "message": "At least one active owner is required",
                        }
                    },
                )
        counter = None
        if not old["active"] and body.active:
            if body.totp is None:
                raise HTTPException(
                    400,
                    detail={
                        "error": {
                            "code": "mfa_required",
                            "message": "TOTP is required to enable an account",
                        }
                    },
                )
            ok, counter = valid_totp(
                decrypt_secret(old["totp_ciphertext"]), body.totp, old["last_totp_counter"]
            )
            if not ok:
                raise HTTPException(
                    401, detail={"error": {"code": "invalid_mfa", "message": "Invalid TOTP"}}
                )
        before = {key: old[key] for key in ("id", "username", "role", "active", "created_at")}
        conn.execute(
            "UPDATE admin_users SET role=?,active=?,last_totp_counter=COALESCE(?,last_totp_counter) WHERE id=?",
            (body.role, int(body.active), counter, user_id),
        )
        if not body.active:
            conn.execute("DELETE FROM admin_sessions WHERE user_id=?", (user_id,))
        after = conn.execute(
            "SELECT id,username,role,active,created_at FROM admin_users WHERE id=?", (user_id,)
        ).fetchone()
        audit(
            conn,
            actor,
            "admin.update",
            "admin_user",
            str(user_id),
            json.dumps(before),
            json.dumps(dict(after)),
        )
        return _dict(after)


@app.post(
    "/api/v1/admin/keys/stage",
    response_model=SigningKeyStageView,
    summary="Stage a new Ed25519 signing key",
)
async def stage_key(
    user: dict[str, Any] = Depends(require_roles("owner")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, str]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_stage_key, user["username"])


def _stage_key(actor: str) -> dict[str, str]:
    from .crypto import generate_signing_key

    kid = "license-" + secrets.token_hex(8)
    path, public_pem = generate_signing_key(kid)
    with transaction() as conn:
        conn.execute(
            "INSERT INTO signing_keys(kid,private_path,public_pem,state,created_at) VALUES(?,?,?,'staged',?)",
            (kid, path.name, public_pem, now()),
        )
        audit(
            conn,
            actor,
            "signing_key.stage",
            "signing_key",
            kid,
            "{}",
            json.dumps({"public_pem": public_pem, "state": "staged"}),
        )
    return {"kid": kid, "public_pem": public_pem, "state": "staged"}


@app.post(
    "/api/v1/admin/keys/{kid}/activate",
    response_model=SigningKeyActivationView,
    summary="Activate staged signing key",
)
async def activate_key(
    kid: str,
    user: dict[str, Any] = Depends(require_roles("owner")),
    csrf: str | None = Header(default=None, alias="X-CSRF-Token"),
) -> dict[str, str]:
    _csrf_check(user, csrf)
    _recent_mfa(user)
    return await _db(_activate_key, kid, user["username"])


def _activate_key(kid: str, actor: str) -> dict[str, str]:
    with transaction() as conn:
        candidate = conn.execute(
            "SELECT kid FROM signing_keys WHERE kid=? AND state='staged'", (kid,)
        ).fetchone()
        if candidate is None:
            raise HTTPException(
                404,
                detail={
                    "error": {"code": "key_not_staged", "message": "Staged signing key not found"}
                },
            )
        before = [
            row["kid"]
            for row in conn.execute("SELECT kid FROM signing_keys WHERE state='active'").fetchall()
        ]
        conn.execute("UPDATE signing_keys SET state='retired' WHERE state='active'")
        conn.execute("UPDATE signing_keys SET state='active' WHERE kid=?", (kid,))
        audit(
            conn,
            actor,
            "signing_key.activate",
            "signing_key",
            kid,
            json.dumps({"active": before}),
            json.dumps({"active": kid}),
        )
        return {"kid": kid, "state": "active"}


@app.get(
    "/api/v1/admin/system",
    response_model=SystemView,
    summary="Read center and local TLS certificate status",
)
async def system_status(
    _: dict[str, Any] = Depends(require_roles("owner", "operator", "reader")),
) -> dict[str, Any]:
    return await _db(_system_status)


def _system_status() -> dict[str, Any]:
    cert_setting = os.environ.get("LICENSE_CENTER_TLS_CERT")
    cert_expiry = None
    readable = False
    if cert_setting:
        try:
            from cryptography import x509

            cert = x509.load_pem_x509_certificate(Path(cert_setting).read_bytes())
            cert_expiry = int(cert.not_valid_after_utc.timestamp())
            readable = True
        except (OSError, ValueError):
            readable = False
    with connect() as conn:
        active = conn.execute(
            "SELECT kid FROM signing_keys WHERE state='active' ORDER BY created_at DESC LIMIT 1"
        ).fetchone()
        staged = [
            row["kid"]
            for row in conn.execute(
                "SELECT kid FROM signing_keys WHERE state='staged' ORDER BY created_at"
            ).fetchall()
        ]
    return {
        "service_status": "running",
        "timezone": os.environ.get("LICENSE_CENTER_TIMEZONE", "Asia/Shanghai"),
        "tls_certificate_configured": bool(cert_setting),
        "tls_certificate_expires_at": cert_expiry,
        "tls_certificate_readable": readable,
        "active_signing_kid": active["kid"] if active else None,
        "staged_signing_kids": staged,
    }


@app.get("/health", response_model=HealthView, summary="Check API and SQLite availability")
async def health() -> dict[str, str]:
    try:
        await _db(_database_health)
    except Exception as exc:
        raise HTTPException(
            503,
            detail={
                "error": {"code": "service_unavailable", "message": "Database health check failed"}
            },
        ) from exc
    return {"status": "ok"}


def _database_health() -> None:
    with connect() as conn:
        row = conn.execute("SELECT MAX(version) FROM schema_migrations").fetchone()
        if row is None or row[0] is None or row[0] < 1:
            raise RuntimeError("Database migrations are not initialized")


dashboard_override = os.environ.get("LICENSE_CENTER_DASHBOARD_DIR")
dashboard_dir = (
    Path(dashboard_override).resolve()
    if dashboard_override
    else Path(__file__).with_name("dashboard")
)
if dashboard_dir.is_dir():
    app.mount("/", StaticFiles(directory=dashboard_dir, html=True), name="dashboard")
elif dashboard_override:
    raise RuntimeError("LICENSE_CENTER_DASHBOARD_DIR must point to the built dashboard directory")
else:
    warnings.warn(
        "License center dashboard assets are not built; serving API and OpenAPI only.",
        RuntimeWarning,
        stacklevel=2,
    )
