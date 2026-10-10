from __future__ import annotations

from types import SimpleNamespace

import pytest

from octop.api.routers.setup import _authorize_setup_mid_wizard, _enforce_wizard_token_phase
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.setup.state import (
    is_setup_pending,
    mark_setup_completed,
    mark_setup_pending,
)


class _Settings:
    def __init__(self, values: dict[str, str] | None = None) -> None:
        self.values = values or {}

    def get(self, key: str) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> None:
        self.values[key] = value


class _WizardTokens:
    def __init__(self) -> None:
        self.cleared = False

    def validate(self, token: str) -> bool:
        return token == "valid"

    def clear(self) -> None:
        self.cleared = True


def _server(*, values: dict[str, str] | None = None, users: int = 0) -> SimpleNamespace:
    return SimpleNamespace(
        services=SimpleNamespace(settings_repo=_Settings(values)),
        user_manager=SimpleNamespace(count=lambda: users),
        database_bound=True,
        wizard_tokens=_WizardTokens(),
    )


def test_empty_install_and_explicit_pending_state_are_wizard_eligible() -> None:
    empty = _server(users=0)
    assert is_setup_pending(empty) is True

    pending = _server(values={"setup.state": "pending"}, users=1)
    assert is_setup_pending(pending) is True


def test_legacy_populated_database_defaults_to_completed() -> None:
    legacy = _server(users=1)
    assert is_setup_pending(legacy) is False


def test_setup_completion_persists_and_invalidates_all_tokens() -> None:
    server = _server(users=1)
    mark_setup_pending(server)
    assert is_setup_pending(server) is True

    mark_setup_completed(server)

    assert is_setup_pending(server) is False
    assert server.wizard_tokens.cleared is True


def test_completed_single_admin_cannot_reissue_or_use_wizard_token() -> None:
    server = _server(values={"setup.state": "completed"}, users=1)
    with pytest.raises(OctopError) as exc:
        _enforce_wizard_token_phase(server)
    assert exc.value.code == ErrorCode.SETUP_REQUIRED

    with pytest.raises(OctopError) as exc:
        _authorize_setup_mid_wizard("Bearer valid", server)
    assert exc.value.code == ErrorCode.SETUP_REQUIRED
