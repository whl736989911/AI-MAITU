"""Sharing governance — ACL state, change requests, and the approval queue.

The rules live in :class:`~octop.infra.sharing.service.SharingService`; this
router only adapts HTTP to them. Two facts belong to the caller, so they are
reported back instead of being re-derived in the dashboard: ``status`` says
whether a change is live (``applied``) or parked for an admin
(``pending_approval``), and ``impact_scope`` says how far it reaches.

Changing access is a user-management action, so reading and writing ACLs needs
the ``users`` module permission. Approving or refusing an org-wide change is a
governance decision and stays admin-only: no module key can grant it.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_admin, require_permission
from octop.infra.db.repos.resource_acl import (
    CHANGE_APPLIED,
    CHANGE_PENDING,
    CHANGE_REJECTED,
    CHANGE_ROLLED_BACK,
    AclChangeRow,
    ResourceAclRepo,
    decode_acl_state,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.sharing import RESOURCE_TYPES, VISIBILITY_UNIT, AclEntry
from octop.infra.sharing.service import ChangeResult, SharingService

router = APIRouter()

# The queue is a reviewer's screen, not an export: a bounded window keeps one
# call from walking a long audit trail.
DEFAULT_QUEUE_LIMIT = 50
MAX_QUEUE_LIMIT = 200

CHANGE_STATUSES = (CHANGE_APPLIED, CHANGE_PENDING, CHANGE_REJECTED, CHANGE_ROLLED_BACK)

Visibility = Literal["private", "unit", "public"]
GranteeType = Literal["user", "unit", "role"]
Permission = Literal["read", "write"]


class GrantBody(BaseModel):
    grantee_type: GranteeType = Field(description="Who the grant is for.")
    grantee_id: str = Field(
        min_length=1, max_length=128, description="User id, unit key, or role name."
    )


class AclChangeBody(BaseModel):
    visibility: Visibility = Field(description="Who may reach the resource by default.")
    unit_key: str | None = Field(
        default=None,
        max_length=64,
        description=(
            "Org unit snapshot for ``unit`` visibility; omit to snapshot the caller's unit."
        ),
    )
    permission: Permission = Field(
        default="read",
        description=(
            "What reaching the resource lets them do: ``read`` reaches it, "
            "``write`` maintains it too."
        ),
    )
    grants: list[GrantBody] = Field(
        default_factory=list, description="Extra grantees; grants only ever widen access."
    )
    reason: str | None = Field(default=None, max_length=200, description="Why access changes.")


class RejectBody(BaseModel):
    reason: str = Field(min_length=1, max_length=200, description="Why the change is refused.")


def _services(server: Any) -> Any:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services are not initialized")
    return server.services


def _sharing(server: Any) -> SharingService:
    return SharingService(_services(server).db)


def _require_resource_type(resource_type: str) -> None:
    """Reject an unknown type here: the service would raise a bare ``ValueError``."""
    if resource_type not in RESOURCE_TYPES:
        raise OctopError(
            ErrorCode.NOT_FOUND,
            f"unknown resource_type {resource_type!r}; expected one of {RESOURCE_TYPES}",
        )


def _maybe_entry_payload(entry: AclEntry | None) -> dict[str, Any] | None:
    """One ACL entry; ``None`` when the resource has no row yet."""
    return None if entry is None else _entry_payload(entry)


def _entry_payload(entry: AclEntry) -> dict[str, Any]:
    return {
        "resource_type": entry.resource_type,
        "resource_id": entry.resource_id,
        "owner_user_id": entry.owner_user_id,
        "visibility": entry.visibility,
        "unit_key": entry.unit_key,
        "permission": entry.permission,
        "version": entry.version,
        "grants": [{"grantee_type": kind, "grantee_id": grantee} for kind, grantee in entry.grants],
    }


def _state_payload(change: AclChangeRow, payload: str, version: int) -> dict[str, Any]:
    """One side of a logged change, decoded into an entry payload."""
    return _entry_payload(
        decode_acl_state(
            payload,
            resource_type=change.resource_type,
            resource_id=change.resource_id,
            version=version,
        )
    )


def _result_payload(
    *,
    change_id: str,
    resource_type: str,
    resource_id: str,
    status: str,
    impact_scope: str,
    entry: AclEntry | None,
) -> dict[str, Any]:
    """Outcome of one change request.

    ``applied`` is true only while that change's state is the one in force, so a
    rolled-back change reports ``false`` although the rollback itself landed.
    """
    return {
        "change_id": change_id,
        "resource_type": resource_type,
        "resource_id": resource_id,
        "status": status,
        "impact_scope": impact_scope,
        "applied": status == CHANGE_APPLIED,
        "entry": _maybe_entry_payload(entry),
    }


def _resolve_change(server: Any, change_id: str) -> AclChangeRow:
    repo: ResourceAclRepo = _services(server).repos.resource_acl_repo
    change = repo.get_change(change_id)
    if change is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"change {change_id!r} not found")
    return change


def _in_force(server: Any, change: AclChangeRow) -> AclEntry | None:
    repo: ResourceAclRepo = _services(server).repos.resource_acl_repo
    return repo.get(change.resource_type, change.resource_id)


def _outcome(server: Any, change_id: str) -> dict[str, Any]:
    """The logged change plus the ACL that is in force for its resource now."""
    change = _resolve_change(server, change_id)
    return _result_payload(
        change_id=change.id,
        resource_type=change.resource_type,
        resource_id=change.resource_id,
        status=change.status,
        impact_scope=change.impact_scope,
        entry=_in_force(server, change),
    )


def _resource_name(repos: Any, resource_type: str, resource_id: str) -> str | None:
    """Best-effort display name; ``None`` when the resource is gone or unnamed.

    The queue is read by a human, so a row must not be a bare UUID — but a name
    is decoration, and a resource deleted since the request must not break the
    listing. A stored change of a type this build has no lookup for (an ACL entry
    for the deleted feature subsystem, say) is such a row: it answers ``None``
    instead of failing the whole listing over a label.
    """
    if resource_type == "agent":
        row = repos.agent_repo.get(resource_id)
        return None if row is None else row.name
    if resource_type == "connector":
        row = repos.connector_repo.get(resource_id)
        return None if row is None else row.display_name
    if resource_type == "knowledge_base":
        row = repos.knowledge_repo.get_base(resource_id)
        return None if row is None else row.name
    return None


def _names_by_resource(
    server: Any, changes: Sequence[AclChangeRow]
) -> dict[tuple[str, str], str | None]:
    """Names for the whole window, one lookup per distinct resource."""
    repos = _services(server).repos
    names: dict[tuple[str, str], str | None] = {}
    for change in changes:
        key = (change.resource_type, change.resource_id)
        if key not in names:
            names[key] = _resource_name(repos, change.resource_type, change.resource_id)
    return names


def _change_payload(
    change: AclChangeRow, names: dict[tuple[str, str], str | None]
) -> dict[str, Any]:
    """One change-log row with both states decoded, for the approval queue."""
    return {
        "change_id": change.id,
        "resource_type": change.resource_type,
        "resource_id": change.resource_id,
        "resource": {"name": names.get((change.resource_type, change.resource_id))},
        "actor_user_id": change.actor_user_id,
        "status": change.status,
        "impact_scope": change.impact_scope,
        "applied": change.status == CHANGE_APPLIED,
        "from_version": change.from_version,
        "to_version": change.to_version,
        "reason": change.reason,
        "created_at": change.created_at,
        "before": _state_payload(change, change.before_json, change.from_version),
        "after": _state_payload(change, change.after_json, change.to_version),
    }


@router.get("/acl/{resource_type}/{resource_id}", summary="Read a resource's ACL")
async def get_resource_acl(
    resource_type: str,
    resource_id: str,
    _user: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """The ACL entry in force, or ``entry: null`` when the resource has none.

    A missing row is not an error: it means nobody has shared the resource, so
    only its owner and admins may reach it (``sharing.can_access``).
    """
    _require_resource_type(resource_type)
    entry = _services(server).repos.resource_acl_repo.get(resource_type, resource_id)
    return {
        "resource_type": resource_type,
        "resource_id": resource_id,
        "entry": _maybe_entry_payload(entry),
    }


@router.post("/acl/{resource_type}/{resource_id}", summary="Request an access change")
async def change_resource_acl(
    resource_type: str,
    resource_id: str,
    body: AclChangeBody,
    user: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Apply an access change, or park it when it would reach the whole org.

    Ownership, versioning, and the approval rule are the service's; this handler
    turns the request into an :class:`~octop.infra.sharing.AclEntry` and reports
    the outcome. A parked change still answers 200: the request was accepted and
    recorded, and ``status`` — not the HTTP code — tells the UI whether it is
    live yet.
    """
    _require_resource_type(resource_type)
    unit_key = body.unit_key
    if body.visibility == VISIBILITY_UNIT:
        # ``unit`` grants access by comparing this snapshot to the viewer's unit,
        # so an empty one would publish to nobody. The caller's own unit is the
        # intended default; a caller without one must name the unit.
        unit_key = unit_key or getattr(user, "org_unit", None)
        if not unit_key:
            raise HTTPException(
                status_code=400,
                detail="unit_key is required: the caller has no org unit to snapshot",
            )
        if _services(server).repos.org_unit_repo.get(unit_key) is None:
            # ``resource_acl.unit_key`` is a foreign key: an unknown unit would
            # otherwise fail the write with an integrity error instead of a 400.
            raise HTTPException(status_code=400, detail=f"unknown unit_key {unit_key!r}")
    entry = AclEntry(
        resource_type=resource_type,
        resource_id=resource_id,
        owner_user_id=user.id,
        visibility=body.visibility,
        unit_key=unit_key,
        version=0,
        permission=body.permission,
        grants=tuple((grant.grantee_type, grant.grantee_id) for grant in body.grants),
    )
    result: ChangeResult = _sharing(server).apply_change(
        user.id, resource_type, resource_id, entry, reason=body.reason
    )
    return _result_payload(
        change_id=result.change_id,
        resource_type=resource_type,
        resource_id=resource_id,
        status=result.status,
        impact_scope=result.impact_scope,
        entry=result.entry,
    )


@router.get("/changes", summary="List access changes")
async def list_sharing_changes(
    status: str = Query(
        CHANGE_PENDING,
        description=f"One of {list(CHANGE_STATUSES)}; defaults to the approval queue.",
    ),
    limit: int = Query(
        DEFAULT_QUEUE_LIMIT, ge=1, le=MAX_QUEUE_LIMIT, description="Newest changes to return."
    ),
    _user: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Newest changes first, with the resource each one is about.

    An unknown ``status`` is refused rather than passed to the query, so the
    filter cannot become a raw scan over the change log.
    """
    if status not in CHANGE_STATUSES:
        raise HTTPException(
            status_code=400, detail=f"status must be one of {list(CHANGE_STATUSES)}"
        )
    rows = _services(server).repos.resource_acl_repo.list_pending_changes(
        status=status, limit=limit
    )
    names = _names_by_resource(server, rows)
    return {
        "status": status,
        "limit": limit,
        "changes": [_change_payload(row, names) for row in rows],
    }


@router.post("/changes/{change_id}/approve", summary="Approve a pending change")
async def approve_sharing_change(
    change_id: str,
    user: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Apply a change parked for approval. Admin-only: this is the org-wide gate."""
    try:
        _sharing(server).approve(change_id, user.id)
    except ValueError as exc:
        # Only a pending change can be approved; anything else is a conflict.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _outcome(server, change_id)


@router.post("/changes/{change_id}/reject", summary="Reject a pending change")
async def reject_sharing_change(
    change_id: str,
    body: RejectBody,
    user: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Refuse a parked change; nothing is applied. Admin-only."""
    try:
        _sharing(server).reject(change_id, user.id, reason=body.reason)
    except ValueError as exc:
        # Only a pending change can be refused; anything else is a conflict.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _outcome(server, change_id)


@router.post("/changes/{change_id}/rollback", summary="Roll a change back")
async def rollback_sharing_change(
    change_id: str,
    user: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Restore the state a change replaced, logged as a new change.

    Who may roll back is the service's call: an admin, or the user who made the
    change. The reply describes the change that was rolled back and the state now
    in force — which is the restored one, not ``after``.
    """
    try:
        _sharing(server).rollback(change_id, user.id)
    except ValueError as exc:
        # Only an applied change can be rolled back; anything else is a conflict.
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _outcome(server, change_id)
