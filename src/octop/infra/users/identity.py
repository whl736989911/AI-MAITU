"""User identity primitives: the four-level administrator model.

Design reference: ``docs/customized-permissions-and-org-management.md`` §2.1
(组织层级) and §4.2 (四级角色和组织范围).

``admin`` (系统管理员) → ``enterprise_admin`` (企业管理员) → ``unit_admin``
(部门管理员) → ``user`` (企业员工).

The four levels answer one question only — *which roles* an actor may mint,
edit, or administer. Three separate axes stay separate on purpose:

* which org units and accounts an administrator reaches is the org tree's
  business (:mod:`octop.infra.users.scope`);
* what an account may *use* stays a module-key question
  (:mod:`octop.infra.users.permissions`).

Keeping them apart is what makes ``enterprise_admin`` an administrator scoped
to its enterprise rather than a second, larger permission catalog: the role
carries no keys of its own (``role_default_permissions`` grants keys to
``admin`` alone), and its reach is bounded by its enterprise's branch of the
org tree.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    """The four roles of the org model, most senior first."""

    # 系统管理员: every permission, every enterprise, every role.
    ADMIN = "admin"
    # 企业管理员: the keys the system administrator granted it, bounded to its
    # own enterprise (the org root its unit descends from).
    ENTERPRISE_ADMIN = "enterprise_admin"
    # 部门管理员: the keys its enterprise administrator granted it, bounded to its
    # own department and that department's sub-departments. Grants no extra
    # module keys (see ``role_default_permissions``).
    UNIT_ADMIN = "unit_admin"
    # 企业员工: the functional keys it was granted, and its own account only.
    USER = "user"


#: Seniority of each role. ``admin`` is top; every other role may only manage
#: roles *strictly below* it, so no role can mint or edit a peer (design §4.2:
#: 角色变更时校验目标角色是否低于操作者可管理的角色层级).
ROLE_LEVELS: dict[str, int] = {
    Role.ADMIN.value: 3,
    Role.ENTERPRISE_ADMIN.value: 2,
    Role.UNIT_ADMIN.value: 1,
    Role.USER.value: 0,
}

#: What each role may hand out, at creation and by a later role change.
#: ``admin`` manages every role (design §2.1: 系统管理员可创建和管理所有角色).
#: ``enterprise_admin`` and ``unit_admin`` cannot create a system administrator or
#: another administrator of their own level, so their set stops one step below
#: themselves; ``user`` hands out nothing — an employee cannot create accounts
#: or authorize (design §2.1, 企业员工 不能创建账号或授权).
ASSIGNABLE_ROLES: dict[str, tuple[Role, ...]] = {
    Role.ADMIN.value: (Role.ADMIN, Role.ENTERPRISE_ADMIN, Role.UNIT_ADMIN, Role.USER),
    Role.ENTERPRISE_ADMIN.value: (Role.UNIT_ADMIN, Role.USER),
    Role.UNIT_ADMIN.value: (Role.USER,),
    Role.USER.value: (),
}


def role_value(role: Role | str | None) -> str:
    """Wire value of a ``Role`` member or of a raw role string from the DB."""
    return str(role) if role is not None else ""


def resolved_role(entity: object) -> str:
    """Wire role of an *account* object — a ``User`` or a persisted user row.

    The ``is_admin`` predicate wins when the object has one, exactly as
    permission resolution has always treated it: a ``User`` exposes it, a
    persisted row carries only ``role``, and hand-built doubles in tests carry
    either. Reading both here keeps those three from answering differently about
    who is a system administrator. Use :func:`role_value` for a bare role.
    """
    if getattr(entity, "is_admin", False):
        return Role.ADMIN.value
    return role_value(getattr(entity, "role", None))


def role_level(role: Role | str | None) -> int:
    """Seniority of *role*.

    A value outside the model (a hand-edited row, a role from a newer build that
    this one does not know) ranks *below* ``user``: -1. Ranking an unknown
    string as if it were an administrator would turn a typo in a row into an
    elevated account, and this function decides refusals.
    """
    return ROLE_LEVELS.get(role_value(role), -1)


def assignable_roles(role: Role | str | None) -> tuple[Role, ...]:
    """Roles *role* may create or assign. Empty = "may not touch roles"."""
    return ASSIGNABLE_ROLES.get(role_value(role), ())


def can_assign_role(actor: Role | str | None, target: Role | str | None) -> bool:
    """Whether *actor* may put an account into *target* — at creation or later.

    One function for both doors: creating a non-``user`` account *is* a role
    grant, so it cannot be looser than changing one afterwards.
    """
    wanted = role_value(target)
    return any(wanted == member.value for member in assignable_roles(actor))


def can_manage_role(actor: Role | str | None, target: Role | str | None) -> bool:
    """Whether *actor* may administer an account that holds *target*.

    ``admin`` administers every role — including another ``admin``, which is
    what "可创建和管理所有角色" means. Every other role only administers the roles
    strictly below it (design §4.2).
    """
    if role_value(actor) == Role.ADMIN.value:
        return True
    return role_level(target) < role_level(actor)


@dataclass
class User:
    id: int
    username: str
    role: Role
    display_name: str | None
    locale: str = "zh"
    permissions: list[str] = field(default_factory=list)
    # Org unit key the user belongs to (``None`` = no unit). Unit-wide module
    # grants are resolved by the caller and passed into ``resolve_permissions``.
    org_unit: str | None = None
    # Explicit denies. Denies outrank role defaults, unit grants and grants.
    denied_permissions: list[str] = field(default_factory=list)

    @property
    def label(self) -> str:
        return self.display_name or self.username

    @property
    def is_admin(self) -> bool:
        """System administrator: bypasses every module gate.

        Deliberately narrow. ``enterprise_admin`` and ``unit_admin`` are *scoped*
        administrators — a role that borrowed this name would inherit the
        catalog-wide bypass instead of the keys it was granted.
        """
        return self.role is Role.ADMIN

    @property
    def is_enterprise_admin(self) -> bool:
        return self.role is Role.ENTERPRISE_ADMIN
