"""User identity primitives."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum


class Role(StrEnum):
    ADMIN = "admin"
    # Resource-scope role: a unit administrator manages the resources of its own
    # org unit. It grants no extra module keys (see ``role_default_permissions``).
    UNIT_ADMIN = "unit_admin"
    USER = "user"


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
        return self.role is Role.ADMIN
