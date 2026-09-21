"""Admin CRUD for users.

Two gates, deliberately distinct:

* the ``users`` module key opens this surface — listing accounts, creating one,
  editing profile fields, and granting module keys the actor itself holds;
* the operations that move the authorization boundary itself — role changes,
  another account's password / disabled state / deletion, and department
  assignment — require the ``admin`` role (``_assert_admin`` /
  ``_assert_can_administer``).

Guarding is on the *target*, not on "is this me": promoting yourself is the same
escalation as promoting somebody else, and a department carries module grants,
so binding an account to one is a permission grant by another name.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import current_user, get_server, require_admin, require_permission
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User
from octop.infra.users.permissions import PERMISSIONS
from octop.infra.users.resource_policy import (
    normalize_token_quota,
    normalize_workspace_root_dir,
    public_policy_fields,
)
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter()


class UserCreateBody(BaseModel):
    # ``extra="forbid"``: a body field this API does not write must fail loudly.
    # Silently dropping one is how ``org_unit`` stayed unset while the editor
    # reported a successful save.
    model_config = ConfigDict(extra="forbid")

    username: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=1, max_length=200)
    role: str = "user"
    display_name: str | None = None
    email: str | None = Field(default=None, max_length=254)
    permissions: list[str] = Field(default_factory=list)
    org_unit: str | None = Field(
        default=None, max_length=64, description="Org unit key, or null for no unit scope."
    )
    workspace_root_dir: str | None = None
    token_quota: int | None = Field(default=None, ge=0)


class UserPatchBody(BaseModel):
    """Partial update; an omitted field keeps its stored value.

    ``org_unit`` is tri-state: omitted keeps the binding, ``null`` clears it, a
    key moves the account. It rides ``model_fields_set`` like ``email``, so an
    omitted field is never confused with an explicit ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    display_name: str | None = None
    email: str | None = Field(default=None, max_length=254)
    disabled: bool | None = None
    permissions: list[str] | None = None
    org_unit: str | None = Field(default=None, max_length=64)
    workspace_root_dir: str | None = None
    token_quota: int | None = Field(default=None, ge=0)


class ResetPasswordBody(BaseModel):
    new_password: str = Field(min_length=1, max_length=200)


def _row_to_dict(r: Any, policy: Any | None = None) -> dict[str, Any]:
    now = int(time.time())
    locked_until = int(getattr(r, "login_locked_until", 0) or 0)
    locked = locked_until > now and not bool(r.disabled)
    retry_after = max(0, locked_until - now) if locked else 0
    return {
        "id": r.id,
        "username": r.username,
        "role": r.role,
        "display_name": r.display_name,
        "email": r.email,
        "has_password": r.password_hash is not None,
        "sso_linked": r.sso_provider_id is not None and r.sso_subject is not None,
        "disabled": bool(r.disabled),
        "login_failed_count": int(getattr(r, "login_failed_count", 0) or 0),
        "login_locked": locked,
        "login_locked_until": locked_until if locked else 0,
        "login_retry_after_seconds": retry_after,
        "created_at": int(r.created_at),
        "permissions": list(getattr(r, "permissions", None) or []),
        # The editor round-trips this field, so a missing key would read as
        # "no unit" and re-open as "不指定（仅基础权限）".
        "org_unit": getattr(r, "org_unit", None),
        **public_policy_fields(policy),
    }


def _policy_kwargs_from_body(body: UserCreateBody | UserPatchBody) -> dict[str, Any]:
    policy_kwargs: dict[str, Any] = {}
    if "workspace_root_dir" in body.model_fields_set:
        policy_kwargs["workspace_root_dir"] = body.workspace_root_dir
    if "token_quota" in body.model_fields_set:
        policy_kwargs["token_quota"] = body.token_quota
    return policy_kwargs


def _assert_admin(actor: User, action: str) -> None:
    """Refuse anything but an admin; ``action`` names the attempt.

    The ``users`` key opens the management page — it does not carry the role
    system, the account-recovery path, or department assignment. A caller that
    holds ``users`` but not ``admin`` could otherwise promote itself, take over
    an admin account, or lock one out; the module key would then be equivalent
    to ``admin`` in effect and the role boundary would be decorative.
    """
    if actor.is_admin:
        return
    raise OctopError(ErrorCode.FORBIDDEN, f"admin required to {action}")


def _assert_can_administer(actor: User, target_user_id: int, action: str) -> None:
    """:func:`_assert_admin`, but an account may still act on itself.

    Used where the self case cannot escalate (disabling yourself, re-setting
    your own password): the guard is on *who is being acted on*, never on "is
    this me and am I being careful".
    """
    if actor.is_admin or target_user_id == actor.id:
        return
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"admin required to {action}",
        details={"user_id": target_user_id},
    )


def _assert_org_unit_exists(server: Any, unit_key: str) -> None:
    """Refuse a binding to a unit that does not exist.

    ``users.org_unit`` is a plain text column with no foreign key, so an unknown
    key would be stored happily and then resolve to *no* grants — a department
    whose permissions quietly never arrive. Refusing here keeps that failure
    loud and next to the write.
    """
    if server.services.repos.org_unit_repo.get(unit_key) is None:
        raise OctopError(
            ErrorCode.NOT_FOUND,
            f"org unit {unit_key!r} not found",
            details={"unit_key": unit_key, "reason": f"unknown org unit {unit_key!r}"},
        )


def _assert_can_assign(actor: User, permissions: list[str]) -> None:
    """Non-admin actors may only grant permissions they themselves hold."""
    if actor.is_admin:
        return
    missing = sorted(set(permissions) - set(actor.permissions or []))
    if missing:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "cannot grant permissions you do not hold",
            details={"missing": missing},
        )


def _can_manage_users(row: Any) -> bool:
    if str(getattr(row, "role", "")) == "admin":
        return True
    return "users" in (getattr(row, "permissions", None) or [])


def _assert_not_last_user_manager(
    server: Any,
    *,
    actor: User,
    target_user_id: int,
    new_permissions: list[str],
) -> None:
    """Refuse stripping ``users`` if no one else could manage users afterward."""
    if target_user_id != actor.id:
        return
    if "users" in new_permissions:
        return
    managers = [
        u
        for u in server.user_manager.list_all(include_disabled=False)
        if _can_manage_users(u) and int(u.id) != target_user_id
    ]
    if not managers:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "cannot remove own user management permission",
        )


@router.get("/permissions", summary="List assignable permission catalog")
async def list_permission_catalog(
    request: Request,
    _: User = Depends(current_user),
) -> list[dict[str, str]]:
    """Return all permissions, localized by Accept-Language, for the UI picker."""
    locale = resolve_request_locale(request)
    items: list[dict[str, str]] = []
    zh = locale.startswith("zh")
    for key, p in PERMISSIONS.items():
        labels = [(p.label_zh, p.label_en), *p.extra_tabs]
        for label_zh, label_en in labels:
            items.append(
                {
                    "key": key,
                    "category": p.category,
                    "label": label_zh if zh else label_en,
                    "page": p.page,
                    "page_label": (p.page_zh if zh else p.page_en) if p.page else "",
                }
            )
    return items


@router.get("")
async def list_users(
    _: Any = Depends(require_permission("users")), server: Any = Depends(get_server)
) -> list[dict[str, Any]]:
    rows = server.user_manager.list_all(include_disabled=True)
    policy_map = server.services.user_policy_repo.list_by_user_ids([r.id for r in rows])
    return [_row_to_dict(r, policy_map.get(r.id)) for r in rows]


@router.post("", status_code=201)
async def create_user(
    body: UserCreateBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    _assert_can_assign(actor, body.permissions)
    if body.org_unit is not None:
        # A department carries module grants, so this binds permissions.
        _assert_admin(actor, "bind an account to a department")
        _assert_org_unit_exists(server, body.org_unit)
    policy_kwargs = _policy_kwargs_from_body(body)
    if "workspace_root_dir" in policy_kwargs:
        normalize_workspace_root_dir(policy_kwargs["workspace_root_dir"])
    if "token_quota" in policy_kwargs:
        normalize_token_quota(policy_kwargs["token_quota"])
    role = Role(body.role)
    user = await server.user_manager.create(
        username=body.username,
        password=body.password,
        role=role,
        display_name=body.display_name,
        email=body.email,
        permissions=body.permissions,
    )
    if body.org_unit is not None:
        await server.user_manager.set_org_unit(user.username, body.org_unit)
    if policy_kwargs:
        await server.user_manager.set_resource_policy(user.username, **policy_kwargs)
    row = server.user_manager.get_row(user.id)
    assert row is not None
    return _row_to_dict(row, server.services.user_policy_repo.list_for_user(row.id))


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    _: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    return _row_to_dict(row, server.services.user_policy_repo.list_for_user(row.id))


@router.patch("/{user_id}")
async def patch_user(
    user_id: int,
    body: UserPatchBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    if body.permissions is not None:
        _assert_can_assign(actor, body.permissions)
        _assert_not_last_user_manager(
            server,
            actor=actor,
            target_user_id=user_id,
            new_permissions=body.permissions,
        )
    if body.role is not None:
        _assert_admin(actor, "change a role")
        if user_id == actor.id and Role(body.role) is not Role.ADMIN:
            raise OctopError(ErrorCode.FORBIDDEN, "cannot demote yourself")
        await server.user_manager.set_role(row.username, Role(body.role))
    if body.display_name is not None:
        await server.user_manager.set_display_name(row.username, body.display_name)
    if "email" in body.model_fields_set:
        await server.user_manager.set_email(row.username, body.email)
    if "org_unit" in body.model_fields_set:
        # Explicit ``null`` clears the binding; an omitted field was filtered out
        # above, so this branch never runs for "leave it as it is".
        _assert_admin(actor, "change the department of an account")
        if body.org_unit is not None:
            _assert_org_unit_exists(server, body.org_unit)
        await server.user_manager.set_org_unit(row.username, body.org_unit)
    if body.disabled is True:
        _assert_can_administer(actor, user_id, "disable another account")
        await server.user_manager.disable(row.username)
    elif body.disabled is False:
        _assert_can_administer(actor, user_id, "enable another account")
        await server.user_manager.enable(row.username)
    if body.permissions is not None:
        await server.user_manager.set_permissions(row.username, body.permissions)
    policy_kwargs = _policy_kwargs_from_body(body)
    if policy_kwargs:
        await server.user_manager.set_resource_policy(row.username, **policy_kwargs)
    updated = server.user_manager.get_row(user_id)
    assert updated is not None
    return _row_to_dict(
        updated,
        server.services.user_policy_repo.list_for_user(updated.id),
    )


@router.post("/{user_id}/unlock-login", status_code=204, summary="Clear login lockout")
async def unlock_user_login(
    user_id: int,
    _: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Clear failed-login counter and temporary lock for a user."""
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    await server.user_manager.unlock_login(row.username)


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(
    user_id: int,
    body: ResetPasswordBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Set a new password without the old one — an account-recovery action.

    Admin-only for anyone else's account: password reset *is* account takeover,
    so an operator holding only ``users`` must not be able to aim it at an admin
    (or at a colleague). Setting your own stays open — it cannot escalate.
    """
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    _assert_can_administer(actor, user_id, "reset another account's password")
    await server.user_manager.reset_password(row.username, body.new_password)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    actor: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> None:
    """Delete an account. Admin-only: ``users`` manages, it does not remove."""
    if user_id == actor.id:
        raise OctopError(ErrorCode.FORBIDDEN, "cannot delete yourself")
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    await server.user_manager.remove(row.username)
