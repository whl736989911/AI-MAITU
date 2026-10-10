"""Persistent first-run state for the setup wizard."""

from __future__ import annotations

from typing import Any

_SETUP_STATE_KEY = "setup.state"
_PENDING = "pending"
_COMPLETED = "completed"


def is_setup_pending(server: Any) -> bool:
    """Return whether setup authority is still valid for this installation.

    Explicit state survives restarts. Legacy populated databases without a state
    key are considered completed; an empty/unbound first-run install remains
    eligible for the wizard.
    """
    services = getattr(server, "services", None)
    settings_repo = getattr(services, "settings_repo", None)
    persisted = settings_repo.get(_SETUP_STATE_KEY) if settings_repo is not None else None
    if persisted == _PENDING:
        return True
    if persisted == _COMPLETED:
        return False

    user_manager = getattr(server, "user_manager", None)
    if user_manager is not None:
        return bool(user_manager.count() == 0)
    return not bool(getattr(server, "database_bound", False))


def mark_setup_pending(server: Any) -> None:
    """Persist the wizard's pending state when the first admin is created."""
    services = getattr(server, "services", None)
    settings_repo = getattr(services, "settings_repo", None)
    if settings_repo is None:
        raise RuntimeError("setup state requires the bound settings repository")
    settings_repo.set(_SETUP_STATE_KEY, _PENDING)


def mark_setup_completed(server: Any) -> None:
    """Persist completion and invalidate every outstanding wizard token."""
    services = getattr(server, "services", None)
    settings_repo = getattr(services, "settings_repo", None)
    if settings_repo is None:
        raise RuntimeError("setup state requires the bound settings repository")
    settings_repo.set(_SETUP_STATE_KEY, _COMPLETED)
    token_store = getattr(server, "wizard_tokens", None)
    if token_store is not None:
        token_store.clear()
