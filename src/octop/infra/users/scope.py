"""Organization scope: which departments and accounts an administrator reaches.

Design reference: ``docs/customized-permissions-and-org-management.md`` §2.1
(组织层级), §4.2 (四级角色和组织范围) and §4.3 (用户和授权接口).

One tree describes the whole organization. The org units a deployment already
has *are* its enterprises, departments and sub-departments — design §4.2 asks
for exactly that rather than a parallel enterprise/department pair of tables:

```text
企业       a root unit (``parent_key IS NULL``) and everything below it
部门       a unit and every unit below it
用户       one account
角色       the role's base permissions
```

Those four are the grant subjects the model talks about, and none of them needs
a table of its own: a role grant is :func:`role_default_permissions`, a
department grant is a row in ``org_unit_permissions`` — reaching the department's
sub-departments, see :func:`unit_permissions` — a user grant is
``users.permissions``, and the enterprise case is simply the root unit's grant.
This module covers the half those resolvers do not: **who may write those
grants, and who may administer which account**.

Scope is deliberately not a permission key. An ``enterprise_admin`` holding no
keys can do nothing with its reach, and a key holder with no reach can edit only
its own account. Every rule below answers one question — "may this actor touch
this row" — and callers ask it through :func:`assert_may_manage_account` /
:func:`assert_may_manage_unit` / :func:`assert_assignable_role`, so a route
never re-derives a hierarchy rule of its own (design §4.3: 路由层只负责请求校验、
调用领域逻辑和转换错误).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol

from octop.infra.db.repos.org_units import OrgUnitRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import (
    Role,
    assignable_roles,
    can_assign_role,
    can_manage_role,
    resolved_role,
    role_value,
)


class ScopeRepo(Protocol):
    """Minimal repo surface scope resolution needs from the org store."""

    def list_all(self) -> Sequence[OrgUnitRow]: ...

    def ancestor_keys(self, unit_key: str) -> list[str]: ...


@dataclass(frozen=True)
class OrgScope:
    """What one actor may administer, resolved once per request.

    ``units`` is the set of unit keys the actor reaches — the actor's own
    department plus its sub-departments for a department administrator, the
    whole enterprise branch for an enterprise administrator. A system
    administrator reaches everything and is marked as such instead of carrying
    every key, so a unit created a moment ago is inside its scope without a
    second resolution step.
    """

    actor_id: int
    role: str
    is_system_admin: bool
    unit: str | None
    enterprise: str | None
    units: frozenset[str]

    def covers_unit(self, unit_key: str | None) -> bool:
        """Whether this scope reaches *unit_key*.

        An unbound enterprise administrator has dynamic enterprise-wide reach;
        ordinary scoped administrators only reach units in their resolved tree.
        """
        if self.is_system_admin or self.enterprise == "*":
            return True
        if unit_key is None:
            return False
        return unit_key in self.units

    def covers_account(
        self, *, user_id: int, role: Role | str | None, org_unit: str | None
    ) -> bool:
        """Whether this scope may administer that account.

        Self is always covered: every account may act on itself, and the fields a
        self-edit may move never move the authorization boundary — the caller
        checks those separately, and role changes are bounded by
        :func:`assert_assignable_role` whatever the target is.
        """
        if user_id == self.actor_id:
            return True
        if self.is_system_admin:
            return True
        if not can_manage_role(self.role, role):
            # Nobody administers a peer or a senior: an enterprise administrator
            # does not administer another enterprise administrator, its own
            # system administrator least of all (design §4.3 rule 6).
            return False
        return self.covers_unit(org_unit)


def enterprise_root(repo: ScopeRepo, unit_key: str) -> str | None:
    """Topmost unit of *unit_key*'s branch — the enterprise it belongs to.

    ``None`` when the unit is unknown: an unbound or mistyped key names no
    enterprise, and guessing one would hand out reach over somebody else's.
    """
    chain = repo.ancestor_keys(unit_key)
    return chain[-1] if chain else None


def subtree_keys(repo: ScopeRepo, unit_key: str) -> frozenset[str]:
    """*unit_key* plus every unit below it.

    The descendant walk carries a visited set for the same reason
    ``OrgUnitRepo.ancestor_keys`` does: a hierarchy an older release left cyclic
    must not turn a scope lookup into an infinite loop.
    """
    children: dict[str, list[str]] = {}
    known: set[str] = set()
    for row in repo.list_all():
        known.add(row.key)
        if row.parent_key is not None:
            children.setdefault(row.parent_key, []).append(row.key)
    if unit_key not in known:
        return frozenset()
    out: set[str] = set()
    pending = [unit_key]
    while pending:
        current = pending.pop()
        if current in out:
            continue
        out.add(current)
        pending.extend(children.get(current, ()))
    return frozenset(out)


def scope_for(actor: Any, repo: ScopeRepo) -> OrgScope:
    """Resolve *actor*'s reach.

    * ``admin`` — everything (design §2.1: 系统管理员 全部企业、部门和用户).
    * ``enterprise_admin`` — the branch of its own unit's enterprise root, or
      all enterprise units and accounts when unbound.
    * ``unit_admin`` — its own unit and its sub-departments (本部门及所有子部门).
    * ``user`` — no administrative reach at all (仅自身可用资源), which is also
      where a role outside the four-level model lands.
    """
    role = resolved_role(actor)
    unit = getattr(actor, "org_unit", None) or None
    actor_id = int(getattr(actor, "id", 0) or 0)
    enterprise: str | None = None
    units: frozenset[str] = frozenset()
    if role == Role.ADMIN.value:
        return OrgScope(
            actor_id=actor_id,
            role=role,
            is_system_admin=True,
            unit=unit,
            enterprise=None,
            units=frozenset(),
        )
    if role == Role.ENTERPRISE_ADMIN.value:
        if unit:
            enterprise = enterprise_root(repo, unit)
            if enterprise is not None:
                units = subtree_keys(repo, enterprise)
        else:
            # The deployment is one enterprise. Use a dynamic marker rather
            # than a snapshot of current roots: this also covers an empty tree,
            # unbound accounts, and departments created later.
            enterprise = "*"
    elif unit and role == Role.UNIT_ADMIN.value:
        units = subtree_keys(repo, unit)
    return OrgScope(
        actor_id=actor_id,
        role=role,
        is_system_admin=False,
        unit=unit,
        enterprise=enterprise,
        units=units,
    )


def assert_may_manage_unit(scope: OrgScope, unit_key: str | None, *, action: str) -> None:
    """Refuse an action on a department outside *scope* — never silently.

    An unbound account is reachable by a system administrator or an unbound
    enterprise administrator; other scoped administrators cannot manage it.
    """
    if scope.covers_unit(unit_key):
        return
    if unit_key is None:
        reason = (
            "an account with no department is outside your department tree; only a "
            "system or enterprise-wide administrator administers unbound accounts"
        )
    elif not scope.units:
        reason = "your account administers no department"
    else:
        reason = f"{unit_key!r} is not one of your departments"
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"{reason}: cannot {action}",
        details={
            "unit_key": unit_key,
            "scope_units": sorted(scope.units),
            "reason": reason,
        },
    )


def assert_may_manage_account(scope: OrgScope, target: Any, *, action: str) -> None:
    """Refuse an action on an account outside *scope*, naming the reason.

    One gate for list filters and writes alike (design §4.2: 列表查询、详情查询、
    修改和删除操作都使用相同的范围校验): a row the actor may not edit must not be
    the row it may read either.
    """
    if scope.covers_account(
        user_id=int(getattr(target, "id", 0) or 0),
        role=getattr(target, "role", None),
        org_unit=getattr(target, "org_unit", None) or None,
    ):
        return
    reason = "target account is outside your organization scope"
    target_role = role_value(getattr(target, "role", None))
    if not can_manage_role(scope.role, target_role):
        reason = f"a {target_role or 'unknown'} account is not below your own role"
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"{reason}: cannot {action}",
        details={
            "user_id": int(getattr(target, "id", 0) or 0),
            "target_role": target_role,
            "target_org_unit": getattr(target, "org_unit", None),
            "scope_units": sorted(scope.units),
            "reason": reason,
        },
    )


def assert_assignable_role(actor: Any, target_role: Role | str | None, *, action: str) -> None:
    """Refuse handing out a role *actor* may not hand out (design §4.3 rule 3).

    ``admin`` assigns every role; every other administrator only the roles below
    itself, so no one mints a peer or a senior — and the same set bounds creating
    an account, changing a role, and nothing else (design §2.1: 企业管理员和部门管理
    员不能创建系统管理员或企业管理员).
    """
    actor_role = resolved_role(actor)
    if can_assign_role(actor_role, target_role):
        return
    raise OctopError(
        ErrorCode.FORBIDDEN,
        f"{role_value(actor_role) or 'unknown'} may not assign the "
        f"{role_value(target_role) or 'unknown'} role: cannot {action}",
        details={
            "actor_role": role_value(actor_role),
            "target_role": role_value(target_role),
            "assignable": [member.value for member in assignable_roles(actor_role)],
            "reason": "role is at or above the actor's own level",
        },
    )
