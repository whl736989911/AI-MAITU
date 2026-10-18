"""Deployment lockdown regressions for host environment configuration."""

from __future__ import annotations

import os
from typing import Any

import pytest

from tests.support.auth import create_user


async def test_envs_crud_cycle(env: Any) -> None:
    c, srv, auth = env
    r = await c.get("/api/envs", headers=auth)
    assert r.status_code == 200
    assert r.json() == []

    r = await c.put("/api/envs", headers=auth, json={"TAVILY_API_KEY": "tvly-1", "FOO": "bar"})
    assert r.status_code == 200
    body = r.json()
    assert {row["key"] for row in body} == {"FOO", "TAVILY_API_KEY"}

    env_path = srv.paths.root / "env"
    assert env_path.is_file()
    assert "TAVILY_API_KEY" in env_path.read_text(encoding="utf-8")

    r = await c.delete("/api/envs/FOO", headers=auth)
    assert r.status_code == 200
    assert {row["key"] for row in r.json()} == {"TAVILY_API_KEY"}


async def test_envs_put_unsets_removed_process_keys(env: Any) -> None:
    import os

    c, _srv, auth = env
    r = await c.put("/api/envs", headers=auth, json={"GONE": "1", "KEEP": "2"})
    assert r.status_code == 200
    assert os.environ.get("GONE") == "1"
    r = await c.put("/api/envs", headers=auth, json={"KEEP": "2"})
    assert r.status_code == 200
    assert "GONE" not in os.environ
    assert os.environ.get("KEEP") == "2"


async def test_envs_redacts_captcha_secret_and_put_keeps_sentinel(env: Any) -> None:
    c, srv, auth = env
    r = await c.put(
        "/api/envs",
        headers=auth,
        json={
            "OCTOP_CAPTCHA_SECRET": "live-secret",
            "OCTOP_CAPTCHA_CAM_SECRET_KEY": "cam-live",
            "FOO": "bar",
        },
    )
    assert r.status_code == 200
    body = r.json()
    by_key = {row["key"]: row["value"] for row in body}
    assert by_key["OCTOP_CAPTCHA_SECRET"] == "********"
    assert by_key["OCTOP_CAPTCHA_CAM_SECRET_KEY"] == "********"
    assert "live-secret" not in str(body)
    assert "cam-live" not in str(body)
    env_path = srv.paths.root / "env"
    assert "live-secret" in env_path.read_text(encoding="utf-8")
    assert "cam-live" in env_path.read_text(encoding="utf-8")

    r = await c.put(
        "/api/envs",
        headers=auth,
        json={
            "OCTOP_CAPTCHA_SECRET": "********",
            "OCTOP_CAPTCHA_CAM_SECRET_KEY": "********",
            "FOO": "bar",
        },
    )
    assert r.status_code == 200
    env_text = env_path.read_text(encoding="utf-8")
    assert "live-secret" in env_text
    assert "cam-live" in env_text
    assert "********" not in env_path.read_text(encoding="utf-8")


async def test_envs_non_admin_forbidden(env: Any) -> None:
    c, _srv, admin_auth = env
    from tests.support.auth import ensure_test_org_unit

    await ensure_test_org_unit(c, admin_auth)
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={"username": "bob", "password": "TestPass12", "role": "user", "org_unit": "test-unit"},
    )
    bob_tok = (
        await c.post("/api/auth/login", json={"username": "bob", "password": "TestPass12"})
    ).json()["access_token"]
    r = await c.get("/api/envs", headers={"Authorization": f"Bearer {bob_tok}"})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_environment_routes_are_denied_without_process_or_file_mutation(
    env: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    client, server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="env-member")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    env_file = server.paths.root / "env"
    process_key = "OCTOP_TEST_RESTRICTED_ENV_WRITE"
    previous_value = os.environ.get(process_key)
    previous_file = env_file.read_bytes() if env_file.exists() else None
    try:
        for auth in (admin_auth, member_auth):
            for method, path, payload in (
                ("GET", "/api/envs", None),
                ("PUT", "/api/envs", {process_key: "must-not-apply"}),
                ("DELETE", f"/api/envs/{process_key}", None),
            ):
                response = await client.request(method, path, headers=auth, json=payload)
                assert response.status_code == 403, response.text
                assert os.environ.get(process_key) == previous_value
                assert (env_file.read_bytes() if env_file.exists() else None) == previous_file
    finally:
        if previous_value is not None:
            os.environ[process_key] = previous_value


@pytest.mark.parametrize(
    "key",
    [
        "MAITU_LICENSE_TRUSTED_KEYS_FILE",
        "MAITU_LICENSE_STATE_DIR",
        "MAITU_UPDATE_TRUSTED_KEYS_FILE",
        "MAITU_UPDATE_QUEUE_DIR",
        "PATH",
        "PYTHONPATH",
        "LD_PRELOAD",
        "BROWSER_USE_NO_SANDBOX",
        "BROWSER_USE_CHROME_BIN",
    ],
)
async def test_managed_env_rejects_trust_and_loader_override(
    env: Any, monkeypatch, key: str
) -> None:
    c, srv, auth = env
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    monkeypatch.setenv(key, "operator-value")
    path = srv.paths.root / "env"
    path.write_text("BUSINESS_TOKEN=kept\n", encoding="utf-8")
    before = path.read_bytes()
    response = await c.put("/api/envs", headers=auth, json={key: "attacker-value"})
    assert response.status_code == 403
    assert path.read_bytes() == before
    assert os.environ[key] == "operator-value"


async def test_managed_env_full_replacement_cannot_remove_operator_key(
    env: Any, monkeypatch
) -> None:
    c, srv, auth = env
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", "operator-trust.json")
    path = srv.paths.root / "env"
    path.write_text("MAITU_LICENSE_TRUSTED_KEYS_FILE=operator-trust.json\n", encoding="utf-8")
    before = path.read_bytes()
    for response in [
        await c.put("/api/envs", headers=auth, json={"BUSINESS_TOKEN": "new"}),
        await c.delete("/api/envs/MAITU_LICENSE_TRUSTED_KEYS_FILE", headers=auth),
    ]:
        assert response.status_code == 403
        assert path.read_bytes() == before
        assert os.environ["MAITU_LICENSE_TRUSTED_KEYS_FILE"] == "operator-trust.json"
