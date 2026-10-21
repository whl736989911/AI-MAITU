"""tests/integration/test_providers_api.py"""

from __future__ import annotations


async def test_list_providers(env):
    """GET /providers returns all providers (read-only endpoint for any user)."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201

    r = await c.get("/api/providers", headers=auth)
    assert r.status_code == 200
    row = next(p for p in r.json() if p["name"] == "openai")
    # The admin bypass keeps the manager view on the shared read endpoint.
    assert row["api_key"] == "k"
    assert row["has_api_key"] is True


async def test_regular_user_list_providers_redacts_api_key(env):
    """GET /providers hides the stored key from callers without the providers key."""
    from tests.support.auth import create_user

    c, _, admin_auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=admin_auth,
        json={"name": "openai", "kind": "openai", "api_key": "sk-secret"},
    )
    assert r.status_code == 201
    r = await c.post(
        "/api/admin/providers",
        headers=admin_auth,
        json={"name": "keyless", "kind": "openai"},
    )
    assert r.status_code == 201

    user_auth = await create_user(c, admin_auth, username="plain-provider-reader")
    r = await c.get("/api/providers", headers=user_auth)
    assert r.status_code == 200
    rows = {p["name"]: p for p in r.json()}
    assert rows["openai"]["api_key"] is None
    assert rows["openai"]["has_api_key"] is True
    assert rows["keyless"]["api_key"] is None
    assert rows["keyless"]["has_api_key"] is False

    # The admin-only view stays closed to them.
    r = await c.get("/api/admin/providers", headers=user_auth)
    assert r.status_code == 403


async def test_provider_manager_sees_api_key(env):
    """A caller holding the providers key keeps the manager view on /providers."""
    from tests.support.auth import create_user

    c, _, admin_auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=admin_auth,
        json={"name": "openai", "kind": "openai", "api_key": "sk-secret"},
    )
    assert r.status_code == 201

    manager_auth = await create_user(
        c,
        admin_auth,
        username="provider-manager",
        permissions=["providers"],
    )
    r = await c.get("/api/providers", headers=manager_auth)
    assert r.status_code == 200
    row = next(p for p in r.json() if p["name"] == "openai")
    assert row["api_key"] == "sk-secret"
    assert row["has_api_key"] is True


async def test_admin_creates_provider(env):
    """POST /admin/providers creates a provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "openai", "kind": "openai", "api_key": "k"},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["name"] == "openai"
    assert "user_id" not in body


async def test_regular_user_cannot_create_provider(env):
    """POST /admin/providers is admin-only."""
    c, _, admin_auth = env
    from tests.support.auth import ensure_test_org_unit

    await ensure_test_org_unit(c, admin_auth)
    await c.post(
        "/api/users",
        headers=admin_auth,
        json={
            "username": "regular",
            "password": "TestPass12",
            "role": "user",
            "org_unit": "test-unit",
        },
    )
    tok = (
        await c.post("/api/auth/login", json={"username": "regular", "password": "TestPass12"})
    ).json()["access_token"]
    user_auth = {"Authorization": f"Bearer {tok}"}

    r = await c.post(
        "/api/admin/providers",
        headers=user_auth,
        json={"name": "should-fail", "kind": "openai"},
    )
    assert r.status_code == 403


async def test_admin_delete_provider(env):
    """DELETE /admin/providers/{id} removes the provider."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "to-delete", "kind": "openai"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code in (200, 204)

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid not in ids


async def test_admin_cannot_delete_local_runtime_provider(env):
    """DELETE /admin/providers/{id} keeps ONNX / Ollama local rows."""
    c, _, auth = env
    r = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={"name": "ONNX (Local)", "kind": "openai", "api_key": "onnx"},
    )
    assert r.status_code == 201
    pid = r.json()["id"]

    r = await c.delete(f"/api/admin/providers/{pid}", headers=auth)
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "PROVIDER_LOCAL_PROTECTED"

    r = await c.get("/api/admin/providers", headers=auth)
    ids = [p["id"] for p in r.json()]
    assert pid in ids


async def test_provider_patch_api_key_omitted_preserves_and_null_clears(env):
    """PATCH distinguishes an omitted API key from an explicit null."""
    c, srv, auth = env
    created = await c.post(
        "/api/admin/providers",
        headers=auth,
        json={
            "name": "patch-key",
            "kind": "openai",
            "base_url": "https://provider.example/v1",
            "api_key": "stored-secret",
            "extra_json": '{"keep":true}',
            "note": "keep this",
        },
    )
    assert created.status_code == 201
    pid = created.json()["id"]

    unchanged = await c.patch(f"/api/admin/providers/{pid}", headers=auth, json={"kind": "openai"})
    assert unchanged.status_code == 200
    assert unchanged.json()["api_key"] == "stored-secret"
    assert unchanged.json()["base_url"] == "https://provider.example/v1"
    assert srv.services.provider_repo.get(pid).extra_json == '{"keep":true}'
    assert unchanged.json()["note"] == "keep this"

    cleared = await c.patch(f"/api/admin/providers/{pid}", headers=auth, json={"api_key": None})
    assert cleared.status_code == 200
    assert cleared.json()["api_key"] is None
    assert cleared.json()["has_api_key"] is False
    assert cleared.json()["base_url"] == "https://provider.example/v1"
    assert srv.services.provider_repo.get(pid).extra_json == '{"keep":true}'
    assert cleared.json()["note"] == "keep this"

    replaced = await c.patch(
        f"/api/admin/providers/{pid}", headers=auth, json={"api_key": "new-secret"}
    )
    assert replaced.status_code == 200
    assert replaced.json()["api_key"] == "new-secret"
