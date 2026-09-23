"""Admin CRUD for users.

Gates, deliberately distinct (design §2.1, §4.3):

* the ``users`` module key opens this surface — listing accounts, creating an
  account, editing profile fields, and granting module keys the actor
  *effectively* holds (``role ∪ department ∪ grant − deny``: the very set
  ``/auth/me`` publishes as the editor's checkboxes — ``_assert_can_assign``);
* the **organization scope** bounds *which* accounts and departments that key
  reaches — a department administrator its own department and its
  sub-departments, an enterprise administrator its whole enterprise
  (:mod:`octop.infra.users.scope`). Every read and every write asks the same
  question, so a row the actor may not edit is not a row it may list either;
* the **role level** bounds which roles an actor may hand out or administer: a
  peer and a senior are out of reach for everyone but a system administrator
  (``assert_assignable_role`` / ``OrgScope.covers_account``);
* permission **denies** stay a system administrator's write (design §2.1: 拒绝权限
  属于高风险操作，第一阶段只允许系统管理员使用).

Guarding is on the *target*, not on "is this me": a department carries module
grants, so binding an account to one is a permission grant by another name. A
deny is that same move run backwards: it subtracts, but it subtracts over the
department too.
"""

from __future__ import annotations

import time
from typing import Any

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.channel_runtime import sync_channel_runtime
from octop.api.deps import (
    current_user,
    get_server,
    request_unit_grants,
    require_permission,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User, role_level
from octop.infra.users.permissions import (
    PERMISSIONS,
    assert_can_grant,
    effective_permissions,
    validate_permission_keys,
)
from octop.infra.users.resource_policy import (
    normalize_token_quota,
    normalize_workspace_root_dir,
    public_policy_fields,
)
from octop.infra.users.scope import (
    OrgScope,
    assert_assignable_role,
    assert_may_manage_account,
    assert_may_manage_unit,
    scope_for,
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
    denied_permissions: list[str] | None = Field(
        default=None,
        description="Permission keys to deny this account, or null for none.",
    )
    workspace_root_dir: str | None = None
    token_quota: int | None = Field(default=None, ge=0)


class UserPatchBody(BaseModel):
    """Partial update; an omitted field keeps its stored value.

    ``org_unit`` and ``denied_permissions`` are tri-state: omitted keeps the
    stored value, ``null`` (or ``[]`` for denies) clears it, a value sets it.
    They ride ``model_fields_set`` like ``email``, so an omitted field is never
    confused with an explicit ``null``.
    """

    model_config = ConfigDict(extra="forbid")

    role: str | None = None
    display_name: str | None = None
    email: str | None = Field(default=None, max_length=254)
    disabled: bool | None = None
    permissions: list[str] | None = None
    org_unit: str | None = Field(default=None, max_length=64)
    denied_permissions: list[str] | None = None
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
        # Same reason: a missing key would read as "no denies" and the next save
        # would silently hand back every key the operator had taken away.
        "denied_permissions": list(getattr(r, "denied_permissions", None) or []),
        **public_policy_fields(policy),
    }


def _policy_kwargs_from_body(body: UserCreateBody | UserPatchBody) -> dict[str, Any]:
    policy_kwargs: dict[str, Any] = {}
    if "workspace_root_dir" in body.model_fields_set:
        policy_kwargs["workspace_root_dir"] = body.workspace_root_dir
    if "token_quota" in body.model_fields_set:
        policy_kwargs["token_quota"] = body.token_quota
    return policy_kwargs


def _writes_access(body: UserPatchBody) -> bool:
    """True when the patch can change an effective key of the target account.

    Each field that reaches :func:`octop.infra.users.permissions.resolve_permissions`:
    ``permissions`` and ``denied_permissions`` are the account's own grant/deny
    legs, ``org_unit`` is the department leg and ``role`` the role leg (and the
    ``admin`` bypass). Everything else a patch writes — display name, email,
    locale, quotas — leaves the effective set alone.
    """
    return (
        body.permissions is not None
        or body.role is not None
        or "denied_permissions" in body.model_fields_set
        or "org_unit" in body.model_fields_set
    )


def _assert_admin(actor: User, action: str) -> None:
    """Refuse anything but a system administrator; ``action`` names the attempt.

    Narrow on purpose: after the four-level model this gate is left to the
    writes that stay a system administrator's alone regardless of scope —
    permission denies (design §2.1: 拒绝权限属于高风险操作，第一阶段只允许系统管理员使
    用). Everything else asks the scope and role questions below.
    """
    if actor.is_admin:
        return
    raise OctopError(ErrorCode.FORBIDDEN, f"admin required to {action}")


def _scope(server: Any, actor: User) -> OrgScope:
    """The actor's reach, resolved from the org tree (design §2.1/§4.2)."""
    return scope_for(actor, server.services.repos.org_unit_repo)


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


def _assert_target_unit(server: Any, scope: OrgScope, unit_key: str | None, *, action: str) -> None:
    """Check the department of a write: scope first, existence second.

    The order is the point. An out-of-scope key answers 403 whether or not it
    exists, so the endpoint cannot be used by a scoped administrator to probe
    which department keys another enterprise holds.
    """
    assert_may_manage_unit(scope, unit_key, action=action)
    if unit_key is not None:
        _assert_org_unit_exists(server, unit_key)


def _require_known_role(raw: str) -> Role:
    """Parse a wire role, refusing a name outside the four-level model.

    ``Role(raw)`` raises a bare ``ValueError`` — a 500 for what is a bad request
    — and defaulting to ``user`` instead would turn a typo such as
    ``enterpise_admin`` into an account whose powers are not the ones the
    operator asked for. Both directions are wrong, so this reports the request.
    """
    try:
        return Role(raw)
    except ValueError as exc:
        raise OctopError(
            ErrorCode.FORBIDDEN,
            f"unknown role {raw!r}; expected one of {[member.value for member in Role]}",
            status=400,
        ) from exc


def _assert_denied_keys_known(denied: list[str] | None) -> list[str]:
    """Return the deduped deny list, refusing a key the catalog does not have.

    Same error shape as ``permissions`` (400). An unknown key stored in the
    column is worse than an error: it reads as a deny but subtracts nothing, so
    the operator would believe a key was taken away while it still resolves.
    """
    try:
        return validate_permission_keys(denied or [])
    except ValueError as exc:
        raise OctopError(ErrorCode.FORBIDDEN, str(exc), status=400) from exc


def _assert_can_assign(request: Request, server: Any, actor: User, permissions: list[str]) -> None:
    """Non-admin actors may only grant keys they *effectively* hold.

    The held set is resolved (``role ∪ department ∪ grant − deny``) through
    :func:`octop.infra.users.permissions.assert_can_grant` — the same resolution
    ``/auth/me`` publishes as the editor's checkbox set, so a key the UI offers
    is a key this accepts. Reading the stored column alone instead showed a
    department's grants as checkable and then refused the submit. The department
    grants come from the per-request cache ``current_user`` already warmed.

    This is one rule with ``org_units.set_org_unit_permissions``, which resolves
    the same set in the scope of the department being edited.
    """
    assert_can_grant(actor, permissions, unit_grants=request_unit_grants(request, server, actor))


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
    actor: User = Depends(current_user),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    """Return all permissions, localized by Accept-Language, for the UI picker.

    Each item carries what design §4.1 asks the catalog for: the key, its
    localized display name, whether it is ``dynamic`` (derived rather than
    written out — today the channel types), and ``can_grant`` — whether *this*
    operator may hand it out. ``can_grant`` is resolved through
    :func:`effective_permissions`, the same function the write path checks
    against, so a box the editor offers is a key the submit accepts.
    """
    locale = resolve_request_locale(request)
    grantable = set(
        effective_permissions(actor, unit_grants=request_unit_grants(request, server, actor))
    )
    items: list[dict[str, Any]] = []
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
                    "dynamic": p.dynamic,
                    "can_grant": key in grantable,
                }
            )
    return items


@router.get("")
async def list_users(
    actor: Any = Depends(require_permission("users")), server: Any = Depends(get_server)
) -> list[dict[str, Any]]:
    """List the accounts this operator may administer (design §4.2).

    The filter is the same check every write below runs, so the list cannot
    advertise a row the operator would be refused on.
    """
    scope = _scope(server, actor)
    rows = [
        r
        for r in server.user_manager.list_all(include_disabled=True)
        if scope.covers_account(
            user_id=int(r.id), role=r.role, org_unit=getattr(r, "org_unit", None) or None
        )
    ]
    policy_map = server.services.user_policy_repo.list_by_user_ids([r.id for r in rows])
    return [_row_to_dict(r, policy_map.get(r.id)) for r in rows]


@router.post("", status_code=201)
async def create_user(
    request: Request,
    body: UserCreateBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create an account inside the actor's own reach (design §4.3 rules 1-4).

    The department binding is role-specific: employees and department
    administrators require one, while a system-created enterprise admin is
    unbound and receives the dynamic enterprise-wide scope.
    """
    _assert_can_assign(request, server, actor, body.permissions)
    scope = _scope(server, actor)
    role = _require_known_role(body.role)
    if body.org_unit is None:
        # System admins must bind employees and department admins, but an
        # enterprise admin is intentionally unbound and receives enterprise-wide
        # scope. Scoped actors still must create inside their own reach.
        if role in {Role.USER, Role.UNIT_ADMIN} or not scope.is_system_admin:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "an account you create must be bound to one of your departments",
                details={
                    "scope_units": sorted(scope.units),
                    "reason": "this role requires a department binding",
                },
            )
    else:
        # A department carries module grants, so this binds permissions.
        _assert_target_unit(server, scope, body.org_unit, action="bind an account to a department")
    # A deny outranks role, unit and grant, so writing one moves the
    # authorization boundary — a system administrator's write (design §2.1). An
    # empty list is the stored default and moves nothing, so an operator who
    # merely round-trips the field is not turned away.
    denied: list[str] = []
    if body.denied_permissions:
        _assert_admin(actor, "deny permissions for an account")
        denied = _assert_denied_keys_known(body.denied_permissions)
    policy_kwargs = _policy_kwargs_from_body(body)
    if "workspace_root_dir" in policy_kwargs:
        normalize_workspace_root_dir(policy_kwargs["workspace_root_dir"])
    if "token_quota" in policy_kwargs:
        normalize_token_quota(policy_kwargs["token_quota"])
    # A role named at creation is a role grant, so it is bounded by the same set
    # that bounds a later role change.
    assert_assignable_role(actor, role, action="create an account with that role")
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
    if denied:
        await server.user_manager.set_denied_permissions(user.username, denied)
    if policy_kwargs:
        await server.user_manager.set_resource_policy(user.username, **policy_kwargs)
    row = server.user_manager.get_row(user.id)
    assert row is not None
    return _row_to_dict(row, server.services.user_policy_repo.list_for_user(row.id))


@router.get("/{user_id}")
async def get_user(
    user_id: int,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    assert_may_manage_account(_scope(server, actor), row, action="read this account")
    return _row_to_dict(row, server.services.user_policy_repo.list_for_user(row.id))


@router.patch("/{user_id}")
async def patch_user(
    user_id: int,
    request: Request,
    body: UserPatchBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    scope = _scope(server, actor)
    # Every write below lands on this account, so the account is checked once,
    # up front: a field the actor may not write on somebody else's account is
    # refused even when its own value would have been acceptable (design §4.3
    # rule 5/6, and 目标用户是否在创建者可管理范围内).
    assert_may_manage_account(scope, row, action="edit this account")
    if body.role is not None or "org_unit" in body.model_fields_set:
        resulting_role = body.role or row.role
        resulting_unit = (
            body.org_unit
            if "org_unit" in body.model_fields_set
            else getattr(row, "org_unit", None)
        )
        if resulting_role in {Role.USER.value, Role.UNIT_ADMIN.value} and resulting_unit is None:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "this role requires a department binding",
                details={"reason": "this role requires a department binding"},
            )
    if body.permissions is not None:
        _assert_can_assign(request, server, actor, body.permissions)
        _assert_not_last_user_manager(
            server,
            actor=actor,
            target_user_id=user_id,
            new_permissions=body.permissions,
        )
    if body.role is not None:
        role = _require_known_role(body.role)
        assert_assignable_role(actor, role, action="change a role")
        if user_id == actor.id and role_level(role) < role_level(actor.role):
            # Self-demotion is not an escalation, but it is the one role move
            # whose consequence the actor cannot undo: it drops the reach it
            # would need to take the role back.
            raise OctopError(ErrorCode.FORBIDDEN, "cannot demote yourself")
        await server.user_manager.set_role(row.username, role)
    if body.display_name is not None:
        await server.user_manager.set_display_name(row.username, body.display_name)
    if "email" in body.model_fields_set:
        await server.user_manager.set_email(row.username, body.email)
    if "org_unit" in body.model_fields_set:
        # Explicit ``null`` clears the binding; an omitted field was filtered out
        # above, so this branch never runs for "leave it as it is". Clearing is
        # a system administrator's move: an unbound account is outside every
        # department tree, so a scoped administrator could otherwise park a user
        # out of its own reach.
        _assert_target_unit(
            server, scope, body.org_unit, action="change the department of an account"
        )
        await server.user_manager.set_org_unit(row.username, body.org_unit)
    if body.disabled is True:
        await server.user_manager.disable(row.username)
    elif body.disabled is False:
        await server.user_manager.enable(row.username)
    if body.permissions is not None:
        await server.user_manager.set_permissions(row.username, body.permissions)
    if "denied_permissions" in body.model_fields_set:
        # Denies land after every grant above, because that is what they do:
        # the key is taken away whatever granted it. Both directions are
        # boundary moves — an explicit ``null``/``[]`` hands back whatever the
        # deny was holding down — so neither is open to a non-admin.
        _assert_admin(actor, "deny permissions for an account")
        await server.user_manager.set_denied_permissions(row.username, body.denied_permissions)
    if _writes_access(body):
        # The account's effective ``channel_<kind>`` may have just changed, and
        # a channel that lost its type key stops now rather than at its next
        # restart (design §2.3/§2.4).
        await sync_channel_runtime(server, user_ids=[user_id])
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
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Clear failed-login counter and temporary lock for a user."""
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    assert_may_manage_account(_scope(server, actor), row, action="unlock this account")
    await server.user_manager.unlock_login(row.username)


@router.post("/{user_id}/reset-password", status_code=204)
async def reset_password(
    user_id: int,
    body: ResetPasswordBody,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Set a new password without the old one — an account-recovery action.

    Bounded by the same scope as every other write on the account: password
    reset *is* account takeover, so it reaches exactly the accounts the actor
    may already administer — its own, and an account strictly below it inside
    its departments. An operator holding only ``users`` cannot aim it at a
    system administrator, at a peer, or at somebody in another department.
    Setting your own stays open — it cannot escalate.
    """
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    assert_may_manage_account(_scope(server, actor), row, action="reset this account's password")
    await server.user_manager.reset_password(row.username, body.new_password)


@router.delete("/{user_id}", status_code=204)
async def delete_user(
    user_id: int,
    actor: Any = Depends(require_permission("users")),
    server: Any = Depends(get_server),
) -> None:
    """Delete an account inside the operator's reach.

    Deletion is part of administering the accounts a role already reaches
    (design §4.2 lists 删除 alongside 修改 as scope-checked), so the gate is the
    scope rather than the role: an administrator removes the accounts below it
    in its own departments, and nobody removes itself.
    """
    if user_id == actor.id:
        raise OctopError(ErrorCode.FORBIDDEN, "cannot delete yourself")
    row = server.user_manager.get_row(user_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, "user not found")
    assert_may_manage_account(_scope(server, actor), row, action="delete this account")
    await server.user_manager.remove(row.username)
