"""Customer-facing instance licensing endpoints."""

from __future__ import annotations

import asyncio
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import APIRouter, Depends, Header, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import get_server, resolve_user_from_token
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import current_license
from octop.infra.setup.state import is_setup_pending
from octop.infra.users.identity import Role, resolved_role

router = APIRouter()

_RATE_LIMITS: dict[str, dict[str, deque[float]]] = defaultdict(dict)
_RATE_LOCK = asyncio.Lock()
_RATE_WINDOW_SECONDS = 60.0
_RATE_LIMITS_PER_WINDOW = {"activate": 5, "refresh": 6}


class ActivateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    activation_code: str = Field(min_length=1, max_length=512)


class RefreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid")


class InstanceLicenseStatus(BaseModel):
    """Minimal public license state; contains no instance identity or sync history."""

    state: str = Field(description="Current authorization state.")
    allowed: bool = Field(description="Whether licensed business access is currently allowed.")
    reason: str | None = Field(description="Stable lock reason, or null while access is allowed.")
    license_expires_at: int | None = Field(description="License deadline as UTC Unix seconds.")
    valid_until: int | None = Field(description="Cached credential deadline as UTC Unix seconds.")
    contact: str = Field(description="Support contact configured by the deployment operator.")
    timezone: str = Field(description="Server timezone used to display license deadlines.")


class InstanceLicenseDetails(InstanceLicenseStatus):
    """Authenticated license state with non-secret instance identity metadata."""

    instance_id: str = Field(description="Stable public instance identifier.")
    public_key_fingerprint: str = Field(
        description="SHA-256 fingerprint of the instance public key."
    )
    license_revision: int = Field(description="Highest accepted license revision.")
    last_sync_at: int | None = Field(
        description="Most recent online sync attempt in UTC Unix seconds."
    )
    last_sync_error: str | None = Field(
        description="Non-secret exception class for the most recent failed sync, or null."
    )


async def _rate_limit(request: Request, operation: str) -> None:
    key = request.client.host if request.client is not None else "unknown"
    now = time.monotonic()
    async with _RATE_LOCK:
        clients = _RATE_LIMITS[operation]
        bucket = clients.setdefault(key, deque())
        while bucket and now - bucket[0] >= _RATE_WINDOW_SECONDS:
            bucket.popleft()
        if len(bucket) >= _RATE_LIMITS_PER_WINDOW[operation]:
            raise OctopError(
                ErrorCode.INSTANCE_LICENSE_RATE_LIMITED,
                "license operation rate limited",
                status=429,
            )
        bucket.append(now)
        if len(clients) > 4096:
            for address in tuple(clients):
                times = clients[address]
                while times and now - times[0] >= _RATE_WINDOW_SECONDS:
                    times.popleft()
                if not times:
                    del clients[address]


async def _authorize_admin_or_first_run(
    server: Any,
    authorization: str | None,
    *,
    request: Request,
    allow_first_run: bool,
) -> None:
    token = (
        authorization.removeprefix("Bearer ").strip()
        if authorization and authorization.startswith("Bearer ")
        else request.query_params.get("access_token", "")
    )
    if (
        allow_first_run
        and token
        and is_setup_pending(server)
        and server.wizard_tokens.validate(token)
    ):
        return
    if not token:
        raise OctopError(ErrorCode.AUTH_FAILED, "credentials required")
    user = resolve_user_from_token(server, token)
    if resolved_role(user) not in {Role.ADMIN, Role.ENTERPRISE_ADMIN}:
        raise OctopError(ErrorCode.FORBIDDEN, "administrator required")


def _details_payload(server: Any) -> InstanceLicenseDetails:
    config = server.services.config if server.services is not None else server.config
    return InstanceLicenseDetails(
        **current_license().details(),
        timezone=config.default_timezone,
    )


@router.get(
    "/license/status",
    summary="Read public instance license status",
    description="Returns the minimum lock state and display timezone before sign-in.",
    response_model=InstanceLicenseStatus,
)
async def license_status(server: Any = Depends(get_server)) -> InstanceLicenseStatus:
    status = current_license().status()
    config = server.services.config if server.services is not None else server.config
    return InstanceLicenseStatus(
        state=status["state"],
        allowed=status["allowed"],
        reason=status["reason"],
        license_expires_at=status["license_expires_at"],
        valid_until=status["valid_until"],
        contact=status["contact"],
        timezone=config.default_timezone,
    )


@router.get(
    "/license/details",
    summary="Read authenticated instance license details",
    description="Requires a customer administrator; never returns private keys or bearer tokens.",
    response_model=InstanceLicenseDetails,
)
async def license_details(
    request: Request,
    server: Any = Depends(get_server),
    authorization: str | None = Header(default=None),
) -> InstanceLicenseDetails:
    await _authorize_admin_or_first_run(
        server,
        authorization,
        request=request,
        allow_first_run=False,
    )
    return _details_payload(server)


@router.post(
    "/license/activate",
    summary="Activate this instance",
    description="Redeems a single-use activation code using the persisted instance identity.",
    response_model=InstanceLicenseDetails,
)
async def activate_license(
    body: ActivateBody,
    request: Request,
    server: Any = Depends(get_server),
    authorization: str | None = Header(default=None),
) -> InstanceLicenseDetails:
    await _rate_limit(request, "activate")
    await _authorize_admin_or_first_run(
        server,
        authorization,
        request=request,
        allow_first_run=True,
    )
    await current_license().activate(body.activation_code)
    return _details_payload(server)


@router.post(
    "/license/refresh",
    summary="Refresh this instance license",
    description="Synchronizes the instance with the trusted license center, including while locked.",
    response_model=InstanceLicenseDetails,
)
async def refresh_license(
    request: Request,
    body: RefreshBody,
    server: Any = Depends(get_server),
    authorization: str | None = Header(default=None),
) -> InstanceLicenseDetails:
    await _rate_limit(request, "refresh")
    await _authorize_admin_or_first_run(
        server,
        authorization,
        request=request,
        allow_first_run=True,
    )
    await current_license().refresh()
    return _details_payload(server)
