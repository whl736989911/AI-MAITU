"""Resolve an active caller's live agent-module capabilities."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.permissions import effective_permissions, unit_permissions


class UserCapabilityPolicy:
    """Live permission resolver for tool execution; deliberately has no cache."""

    def __init__(self, repos: Any) -> None:
        self.replace_repos(repos)

    def replace_repos(self, repos: Any) -> None:
        """Retarget checks held by existing middleware and scheduled jobs."""
        self._user_repo = repos.user_repo
        self._org_unit_repo = repos.org_unit_repo

    def permissions_for(self, user_id: int | None) -> set[str]:
        user = self._active_user(user_id)
        if user is None:
            return set()
        unit_keys = list(self._user_repo.units_for_user(user.id) or [])
        primary = getattr(user, "org_unit", None)
        if primary and primary not in unit_keys:
            unit_keys.append(str(primary))
        unit_grants: set[str] = set()
        for unit_key in dict.fromkeys(unit_keys):
            unit_grants.update(unit_permissions(str(unit_key), self._org_unit_repo))
        return set(effective_permissions(user, unit_grants=unit_grants))

    def _active_user(self, user_id: int | None) -> Any | None:
        if isinstance(user_id, bool) or not isinstance(user_id, int) or user_id <= 0:
            return None
        user = self._user_repo.get(user_id)
        return user if user is not None and not bool(user.disabled) else None

    def require_permissions(self, user_id: int | None, keys: Iterable[str]) -> None:
        user = self._active_user(user_id)
        if user is None:
            raise OctopError.localized(ErrorCode.USER_DISABLED)
        required = set(keys)
        if required and not required.issubset(self.permissions_for(user_id)):
            raise OctopError.localized(ErrorCode.FORBIDDEN)


__all__ = ["UserCapabilityPolicy"]
