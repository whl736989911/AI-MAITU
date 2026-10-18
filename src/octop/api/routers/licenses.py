"""Instance license management API.

Public minimal status is readable while locked so the dashboard can render the
license gate; every administrative action requires an authenticated admin and
stays reachable while locked (that is the point: activation must be possible
on a locked instance). No token, private key, or customer field is exposed.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server, require_admin
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.licensing import LicenseManager
from octop.infra.server import OctopServer

router = APIRouter()


async def require_license_business() -> None:
    """FastAPI dependency: deny business routes when the managed license is invalid.

    Unmanaged installs resolve to the default unmanaged singleton and pass.
    """
    from octop.infra.licensing import current_license

    current_license().require_business_access()


async def require_license_details_admin(user: Any = Depends(current_user)) -> Any:
    """Allow system and enterprise administrators to inspect license status."""
    if not user.is_admin and not getattr(user, "is_enterprise_admin", False):
        raise OctopError(ErrorCode.FORBIDDEN, "administrator required")
    return user


class LicenseStatus(BaseModel):
    managed: bool
    business_allowed: bool
    state: str | None = None
    reason: str | None = None
    instance_id: str = ""
    license_id: str | None = None
    license_version: int | None = None
    license_expires_at: int | None = Field(
        default=None, description="Actual entitlement deadline (UTC seconds)"
    )
    expiring_soon: bool = False


class LicenseDetails(LicenseStatus):
    credential_type: str | None = None
    last_sync_at: int | None = None
    sync_error: str | None = None


class OfflineRequest(BaseModel):
    instance_id: str
    public_key_pem: str = Field(
        description="Instance public key only; never contains a private key"
    )
    key_fingerprint: str
    proof: str = Field(description="Signed instance possession proof for center approval")


class LicenseReminder(BaseModel):
    show: bool
    expires_at: int | None
    remaining_seconds: int | None
    date: str | None = Field(description="Server-timezone calendar date of the daily reminder")


def _unmanaged_status() -> dict[str, Any]:
    return {
        "managed": False,
        "state": None,
        "business_allowed": True,
        "reason": None,
        "instance_id": "",
        "license_id": None,
        "license_version": None,
        "license_expires_at": None,
        "expiring_soon": False,
    }


def _require_manager(server: OctopServer) -> LicenseManager:
    """Return the managed LicenseManager or raise for unmanaged installs."""
    manager = server.license_manager
    if manager is None:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "This instance is not license-managed",
        )
    return manager


def _public_status(server: OctopServer) -> dict[str, Any]:
    manager = server.license_manager
    if manager is None:
        return _unmanaged_status()
    snap = manager.snapshot()
    return {
        "managed": snap.get("managed", True),
        "state": snap.get("state"),
        "business_allowed": snap.get("business_allowed"),
        "reason": snap.get("reason"),
        "instance_id": snap.get("instance_id"),
        "license_id": snap.get("license_id"),
        "license_version": snap.get("license_version"),
        "license_expires_at": snap.get("license_expires_at"),
        "expiring_soon": snap.get("expiring_soon"),
    }


@router.get("/status", summary="Minimal public license status", response_model=LicenseStatus)
async def license_status(request: Request) -> dict[str, Any]:
    """Unauthenticated minimal snapshot: no keys, no tokens, no customer data."""
    return _public_status(get_server(request))


@router.get(
    "/details",
    summary="Full license snapshot (admin)",
    description="Unmanaged instances return managed=false and business_allowed=true. "
    "Reading status does not require enrollment in license management.",
    response_model=LicenseDetails,
)
async def license_details(
    request: Request, _admin: Any = Depends(require_license_details_admin)
) -> dict[str, Any]:
    server = get_server(request)
    manager = server.license_manager
    if manager is None:
        return _unmanaged_status()
    return manager.snapshot()


class ActivateBody(BaseModel):
    code: str = Field(
        min_length=1, description="One-time activation code issued by the license center"
    )


@router.post(
    "/activate", summary="Activate with a one-time code (admin)", response_model=LicenseDetails
)
async def license_activate(
    request: Request, body: ActivateBody, _admin: Any = Depends(require_admin())
) -> dict[str, Any]:
    server = get_server(request)
    manager = _require_manager(server)
    return await manager.activate(body.code.strip())


@router.post(
    "/refresh",
    summary="Refresh the online license credential (admin)",
    response_model=LicenseDetails,
)
async def license_refresh(
    request: Request, _admin: Any = Depends(require_admin())
) -> dict[str, Any]:
    server = get_server(request)
    manager = _require_manager(server)
    return await manager.refresh()


@router.get(
    "/offline-request",
    summary="Export a signed offline license request (admin)",
    response_model=OfflineRequest,
)
async def license_offline_request(
    request: Request, _admin: Any = Depends(require_admin())
) -> dict[str, Any]:
    server = get_server(request)
    manager = _require_manager(server)
    return manager.export_request()


class OfflineImportBody(BaseModel):
    token: str = Field(min_length=1, description="Raw offline license JWT text or file content")


@router.post(
    "/offline-import",
    summary="Import a signed offline license (admin)",
    response_model=LicenseDetails,
)
async def license_offline_import(
    request: Request, body: OfflineImportBody, _admin: Any = Depends(require_admin())
) -> dict[str, Any]:
    server = get_server(request)
    manager = _require_manager(server)
    try:
        return await manager.import_offline(body.token.strip())
    except ValueError as exc:
        raise OctopError(ErrorCode.INSTANCE_LICENSE_INVALID, str(exc)) from exc


@router.post(
    "/reminder",
    summary="Claim today's expiry reminder for the current user",
    response_model=LicenseReminder,
)
async def license_reminder(request: Request, user: Any = Depends(current_user)) -> dict[str, Any]:
    server = get_server(request)
    manager = server.license_manager
    if manager is None:
        return {"show": False, "expires_at": None, "remaining_seconds": None, "date": None}
    config = server.services.config if server.services else server.config
    timezone = config.default_timezone if config else "UTC"
    return await manager.claim_reminder(user.id, timezone)
