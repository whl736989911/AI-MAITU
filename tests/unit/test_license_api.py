from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from octop.api.app import _install_exception_handlers
from octop.api.deps import get_server, sign_token
from octop.api.routers import license as license_router
from octop.infra.errors import ErrorCode
from octop.infra.users.identity import Role


class _User:
    def __init__(self, is_admin: bool, role: Role | None = None) -> None:
        self.role = role or (Role.ADMIN if is_admin else Role.USER)
        self.is_admin = self.role == Role.ADMIN


class _UserManager:
    def __init__(self, user: _User) -> None:
        self.user = user

    def count(self) -> int:
        return 1

    def get_by_id(self, _user_id: int) -> _User:
        return self.user


class _License:
    def status(self) -> dict[str, Any]:
        return {
            "state": "LOCKED",
            "allowed": False,
            "reason": "expired",
            "license_expires_at": 100,
            "valid_until": 90,
            "contact": "support@example.test",
            "instance_id": "private-id",
            "license_revision": 7,
        }

    def details(self) -> dict[str, Any]:
        return {
            **self.status(),
            "instance_id": "public-id",
            "public_key_fingerprint": "a" * 64,
            "license_revision": 7,
            "last_sync_at": 80,
            "last_sync_error": None,
        }

    async def refresh(self) -> dict[str, Any]:
        return self.details()


class _WizardTokens:
    def validate(self, token: str | None) -> bool:
        return token == "first-run-token"


def _client(
    monkeypatch, *, admin: bool = True, role: Role | None = None
) -> tuple[TestClient, _License, str]:
    license_service = _License()
    monkeypatch.setattr(license_router, "current_license", lambda: license_service)
    secret = b"test-jwt-secret"
    server = SimpleNamespace(
        user_manager=_UserManager(_User(is_admin=admin, role=role)),
        wizard_tokens=_WizardTokens(),
        services=SimpleNamespace(
            secret_repo=SimpleNamespace(get=lambda _key: secret),
            config=SimpleNamespace(default_timezone="Asia/Shanghai"),
        ),
        config=SimpleNamespace(default_timezone="Asia/Shanghai"),
    )
    app = FastAPI()
    _install_exception_handlers(app)
    app.include_router(license_router.router, prefix="/api")
    app.dependency_overrides[get_server] = lambda: server
    token = sign_token(secret, sub=1, uname="admin", role="admin")
    return TestClient(app), license_service, token


def test_public_status_is_minimal_and_contains_server_timezone(monkeypatch) -> None:
    client, _service, _token = _client(monkeypatch)
    response = client.get("/api/license/status")

    assert response.status_code == 200
    assert set(response.json()) == {
        "state",
        "allowed",
        "reason",
        "license_expires_at",
        "valid_until",
        "contact",
        "timezone",
    }
    assert "instance_id" not in response.json()
    assert response.json()["timezone"] == "Asia/Shanghai"


@pytest.mark.parametrize("role", [Role.ADMIN, Role.ENTERPRISE_ADMIN])
def test_customer_admin_roles_can_refresh_but_regular_users_cannot(monkeypatch, role) -> None:
    client, _service, token = _client(monkeypatch, role=role)
    response = client.post(
        "/api/license/refresh",
        json={},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert response.status_code == 200

    client.close()
    non_admin, _other_service, non_admin_token = _client(monkeypatch, admin=False)
    denied = non_admin.post(
        "/api/license/refresh",
        json={},
        headers={"Authorization": f"Bearer {non_admin_token}"},
    )
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == ErrorCode.FORBIDDEN.value
    non_admin.close()
