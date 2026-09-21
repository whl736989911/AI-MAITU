"""Module-level permission catalog and checks.

A permission is a module key (e.g. ``"browser"``, ``"users"``). Possessing a
key grants access to that module's management page and write/configure actions.
Read access and agent use in chat are never gated. ``admin`` bypasses all.

Categories mirror dashboard nav groups: ``settings`` / ``control`` / ``admin``.
Admin keys may also carry a ``page`` so the picker can group by page / tab.

Effective access is ``role ∪ unit ∪ grant − deny`` (see
:func:`resolve_permissions`): an explicit deny outranks everything but the
``admin`` bypass.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role


class PermissionUser(Protocol):
    @property
    def is_admin(self) -> bool: ...

    permissions: list[str]


class UnitPermissionRepo(Protocol):
    """Minimal repo surface :func:`unit_permissions` needs from the org store."""

    def list_unit_permissions(self, unit_key: str) -> list[str]: ...


@dataclass(frozen=True)
class PermissionDef:
    key: str
    category: str  # "settings" | "control" | "admin"
    label_zh: str
    label_en: str
    page: str = ""
    page_zh: str = ""
    page_en: str = ""
    extra_tabs: tuple[tuple[str, str], ...] = ()


def _p(
    key: str,
    category: str,
    label_zh: str,
    label_en: str,
    *,
    page: str = "",
    page_zh: str = "",
    page_en: str = "",
    extra_tabs: tuple[tuple[str, str], ...] = (),
) -> PermissionDef:
    return PermissionDef(key, category, label_zh, label_en, page, page_zh, page_en, extra_tabs)


PERMISSIONS: dict[str, PermissionDef] = {
    # --- settings (nav.settings) — listed & default-selected for new users ---
    "channels": _p("channels", "settings", "通道", "Channels"),
    "connectors": _p("connectors", "settings", "连接器", "Connectors"),
    "skill_packages": _p("skill_packages", "settings", "技能包", "Skill Packages"),
    "knowledge_bases": _p("knowledge_bases", "settings", "知识库", "Knowledge Base"),
    "features": _p("features", "settings", "功能", "Features"),
    # --- control (nav.control) — page/tab labels ---
    "terminal": _p("terminal", "control", "工作台/终端", "Workbench / Terminal"),
    "browser": _p("browser", "control", "工作台/浏览器", "Workbench / Browser"),
    "desktop": _p("desktop", "control", "远程桌面", "Remote Desktop"),
    "mobile": _p("mobile", "control", "远程手机", "Remote Phone"),
    # --- admin: grouped by page, chip = tab title ---
    "users": _p(
        "users",
        "admin",
        "内置用户",
        "Local users",
        page="users",
        page_zh="用户",
        page_en="Users",
    ),
    "sso": _p(
        "sso",
        "admin",
        "单点登录",
        "Single sign-on",
        page="users",
        page_zh="用户",
        page_en="Users",
    ),
    "providers": _p(
        "providers",
        "admin",
        "云端",
        "Cloud",
        page="models",
        page_zh="模型",
        page_en="Models",
    ),
    "ollama_models": _p(
        "ollama_models",
        "admin",
        "Ollama",
        "Ollama",
        page="models",
        page_zh="模型",
        page_en="Models",
    ),
    "onnx_models": _p(
        "onnx_models",
        "admin",
        "ONNX",
        "ONNX",
        page="models",
        page_zh="模型",
        page_en="Models",
    ),
    "storage_backends": _p("storage_backends", "admin", "存储", "Storage"),
    "plugins": _p(
        "plugins",
        "admin",
        "已安装",
        "Installed",
        page="plugins",
        page_zh="插件",
        page_en="Plugins",
    ),
    "security": _p(
        "security",
        "admin",
        "防护策略",
        "Policy",
        page="security",
        page_zh="安全防护",
        page_en="Security",
    ),
    "admin_console": _p(
        "admin_console",
        "admin",
        "审计日志",
        "Audit log",
        page="security",
        page_zh="安全防护",
        page_en="Security",
    ),
    "envs": _p(
        "envs",
        "admin",
        "环境变量",
        "Environment",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "search": _p(
        "search",
        "admin",
        "搜索引擎",
        "Search engines",
        page="models",
        page_zh="模型",
        page_en="Models",
    ),
    "knowledge_settings": _p(
        "knowledge_settings",
        "admin",
        "知识库设置",
        "Knowledge-base settings",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "voice": _p(
        "voice",
        "admin",
        "语音模型",
        "Voice models",
        page="models",
        page_zh="模型",
        page_en="Models",
    ),
    "observability": _p(
        "observability",
        "admin",
        "可观测",
        "Observability",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "backup": _p(
        "backup",
        "admin",
        "备份与恢复",
        "Backup & restore",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "tls": _p(
        "tls",
        "admin",
        "HTTPS",
        "HTTPS",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "update": _p(
        "update",
        "admin",
        "应用更新",
        "Updates",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
    "captcha": _p(
        "captcha",
        "admin",
        "登录验证码",
        "Login captcha",
        page="advanced",
        page_zh="应用设置",
        page_en="App settings",
    ),
}

ALL_PERMISSION_KEYS: set[str] = set(PERMISSIONS)

# Settings-group keys: shown in the picker and pre-checked for new users.
# They are still stored explicitly — not silently granted without being written.
# ``role_default_permissions`` therefore never implies them for a non-admin role:
# an unset (or unchecked) key means no access.
BASELINE_PERMISSIONS: set[str] = {key for key, p in PERMISSIONS.items() if p.category == "settings"}


def _role_value(role: object) -> str:
    """Wire value of a ``Role`` member or of a raw role string from the DB."""
    return str(role) if role is not None else ""


def _as_set(values: Iterable[str] | None) -> set[str]:
    """Set copy of a possibly-absent key list (rows come back as text columns)."""
    return {str(v) for v in values} if values else set()


def role_default_permissions(role: Role | str | None) -> set[str]:
    """Module keys implied by the role alone (admin -> full catalog).

    Only ``admin`` implies keys. Other roles start empty and are granted keys
    explicitly (stored grants, org-unit grants, or ``BASELINE_PERMISSIONS``
    written at user creation), so revoking a key in the user editor really
    revokes it.
    """
    if _role_value(role) == Role.ADMIN:
        return set(ALL_PERMISSION_KEYS)
    return set()


def unit_permissions(unit_key: str | None, repo: Any) -> set[str]:
    """Module keys granted by the org unit. None -> empty."""
    if not unit_key:
        return set()
    return _as_set(repo.list_unit_permissions(unit_key))


def resolve_permissions(
    *,
    role: Role | str | None,
    permissions: list[str] | None,
    denied: list[str] | None,
    unit_grants: set[str] | None,
) -> set[str]:
    """role ∪ unit ∪ grant − deny, with admin bypassing everything."""
    if _role_value(role) == Role.ADMIN:
        return set(ALL_PERMISSION_KEYS)
    granted = role_default_permissions(role)
    granted |= _as_set(unit_grants) | _as_set(permissions)
    granted -= _as_set(denied)
    return granted


def _resolve_user(user: PermissionUser, unit_grants: set[str] | None) -> set[str]:
    """Resolve a user object, tolerating rows that predate the org-unit columns."""
    return resolve_permissions(
        role=Role.ADMIN if getattr(user, "is_admin", False) else getattr(user, "role", None),
        permissions=list(user.permissions or []),
        denied=list(getattr(user, "denied_permissions", None) or []),
        unit_grants=unit_grants,
    )


def user_has_permission(
    user: PermissionUser,
    key: str,
    *,
    unit_grants: set[str] | None = None,
) -> bool:
    """Return True if ``user`` may access the module ``key``.

    Unknown keys are denied. ``admin`` bypasses everything; an explicit deny
    in ``user.denied_permissions`` outranks role, unit and granted keys.
    """
    if key not in PERMISSIONS:
        return False
    return key in _resolve_user(user, unit_grants)


def validate_permission_keys(keys: list[str]) -> list[str]:
    """Return a deduped list or raise ``ValueError`` on unknown keys."""
    unknown = sorted({k for k in keys if k not in PERMISSIONS})
    if unknown:
        raise ValueError(f"unknown permission keys: {unknown}")
    seen: set[str] = set()
    out: list[str] = []
    for k in keys:
        if k not in seen:
            seen.add(k)
            out.append(k)
    return out


def effective_permissions(
    user: PermissionUser,
    *,
    unit_grants: set[str] | None = None,
) -> list[str]:
    """Permissions to expose on ``/auth/me`` / login (admin gets the full catalog)."""
    return sorted(_resolve_user(user, unit_grants))


def assert_can_grant(
    user: PermissionUser,
    permissions: Iterable[str],
    *,
    unit_grants: set[str] | None = None,
    details: Mapping[str, Any] | None = None,
) -> None:
    """Refuse handing out keys ``user`` does not effectively hold.

    Granting is delegation, not self-escalation. The held set is
    :func:`effective_permissions` — the very set ``/auth/me`` publishes as the
    editor's checkbox set — so a key the UI offers is a key this accepts, and a
    key held down by a deny is never grantable.

    Every grant surface resolves through here (the user editor's ``permissions``
    field, a department's grant set), so the two cannot drift apart.
    ``unit_grants`` is the scope the held set is resolved in: the actor's *own*
    department in the user editor, the department being edited for a unit admin
    — which lets that admin re-submit grants its department already has.
    """
    if getattr(user, "is_admin", False):
        return
    missing = sorted(set(permissions) - _resolve_user(user, unit_grants))
    if not missing:
        return
    payload: dict[str, Any] = {"missing": missing}
    if details:
        payload.update(details)
    raise OctopError(
        ErrorCode.FORBIDDEN,
        "cannot grant permissions you do not hold",
        details=payload,
    )
