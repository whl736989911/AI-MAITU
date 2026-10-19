"""tests/integration/test_setup_bootstrap.py — initial-admin path."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx

from octop.api.app import build_app
from octop.infra.server import OctopServer
from octop.infra.setup.password_file import WIZARD_FILE_NAME, read_password
from tests.support.app import ensure_control_plane_bound, write_octop_config
from tests.support.auth import auth_header, bootstrap_admin
from tests.support.harness import patch_harness
from tests.support.licensing import CenterSigner


async def test_locked_setup_defers_then_resumes_managed_defaults_once(
    tmp_octop_home: Path,
    monkeypatch: Any,
) -> None:
    """A finished setup stays durable while signed-license business work is locked."""
    signer = CenterSigner()
    keys_file = signer.write_trusted_keys(tmp_octop_home / "license-trust" / "trusted-keys.json")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(keys_file))
    monkeypatch.setenv("MAITU_LICENSE_STATE_DIR", str(tmp_octop_home / "license-state"))
    monkeypatch.delenv("MAITU_LICENSE_CENTER_URL", raising=False)
    monkeypatch.delenv("MAITU_LICENSE_ALLOW_LOOPBACK", raising=False)
    write_octop_config(
        tmp_octop_home,
        database={"driver": "sqlite"},
        require_setup_password=False,
    )
    srv = OctopServer(home=tmp_octop_home)
    with patch_harness():
        await srv.start()
        await ensure_control_plane_bound(srv)
        try:
            assert srv.license_manager is not None
            manager = srv.license_manager
            assert manager.managed and not manager.business_allowed
            app = build_app(srv)
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app),
                base_url="http://testserver",
            ) as c:
                token = (await c.post("/api/setup/begin")).json()["wizard_token"]
                created = await c.post(
                    "/api/setup/initial-admin",
                    json={"username": "admin", "password": "TestPass12"},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert created.status_code == 201, created.text
                auth = {"Authorization": f"Bearer {created.json()['access_token']}"}
                finished = await c.post(
                    "/api/setup/finish",
                    json={"provider_draft": None},
                    headers={"Authorization": f"Bearer {token}"},
                )
                assert finished.status_code == 200, finished.text
                assert (await c.get("/api/setup/status")).json()["setup_required"] is False
                # Managed + locked: the business plane (and its routes) is denied.
                assert (await c.get("/api/agents", headers=auth)).status_code == 423
                assert srv.services is not None
                assert srv.services.settings_repo.get("setup.default_agents_pending")
                assert srv.app_runtime is not None
                assert srv.app_runtime.agent_registry.get_row("main") is None
                assert srv.app_runtime.agent_registry.get_row("feat-general-assistant") is None

                token_payload = signer.offline(
                    manager.instance_id,
                    manager._identity,
                    status="active",
                )
                await manager.import_offline(token_payload)
                await srv._on_license_change(True)

                agents = await c.get("/api/agents", headers=auth)
                assert agents.status_code == 200, agents.text
                assert {(a["kind"], a["agent_id"]) for a in agents.json()} == {
                    ("agent", "main"),
                    ("feature", "feat-general-assistant"),
                }
                assert not srv.services.settings_repo.get("setup.default_agents_pending")
                assert srv.app_runtime.business_running
                assert srv.paths is not None
                workspace = srv.paths.agent_workspace("main")
                assert (workspace / "SOUL.md").is_file()
                assert (workspace / ".maitu" / "skills" / "octop-assistant" / "SKILL.md").is_file()

                # An allowed transition while already running is a no-op.
                await srv._on_license_change(True)
                resumed_agents = await c.get("/api/agents", headers=auth)
                assert resumed_agents.status_code == 200
                assert {(a["kind"], a["agent_id"]) for a in resumed_agents.json()} == {
                    ("agent", "main"),
                    ("feature", "feat-general-assistant"),
                }
                assert (await c.get("/api/setup/status")).json()["setup_required"] is False
        finally:
            await srv.stop()


async def test_setup_status_flips_after_bootstrap(patched_app_client: Any) -> None:
    c, _srv, home = patched_app_client
    r = await c.get("/api/setup/status")
    assert r.status_code == 200
    assert r.json()["setup_required"] is True

    r = await bootstrap_admin(c, home)
    assert r.status_code == 201

    r = await c.get("/api/setup/status")
    assert r.json()["setup_required"] is False


async def test_bootstrap_creates_default_main_agent(patched_app_client: Any) -> None:
    c, _srv, home = patched_app_client
    await bootstrap_admin(c, home)
    auth = await auth_header(c)

    r = await c.get("/api/agents", headers=auth)
    assert r.status_code == 200
    agents = r.json()
    assert {(row["kind"], row["agent_id"]) for row in agents} == {
        ("agent", "main"),
        ("feature", "feat-general-assistant"),
    }
    main = next(row for row in agents if row["agent_id"] == "main")
    assert main["name"] == "小通 · 通用助手"


async def test_main_not_created_until_finish(patched_app_client: Any) -> None:
    c, _srv, home = patched_app_client
    pw = read_password(home.parent)
    assert pw is not None
    tok = (await c.post("/api/setup/verify-password", json={"password": pw})).json()["wizard_token"]
    admin = (
        await c.post(
            "/api/setup/initial-admin",
            json={"username": "admin", "password": "TestPass12"},
            headers={"Authorization": f"Bearer {tok}"},
        )
    ).json()
    auth = {"Authorization": f"Bearer {admin['access_token']}"}
    r = await c.get("/api/agents", headers=auth)
    assert r.status_code == 200
    assert r.json() == []

    finish = await c.post(
        "/api/setup/finish",
        json={"provider_draft": None},
        headers={"Authorization": f"Bearer {tok}"},
    )
    assert finish.status_code == 200
    r = await c.get("/api/agents", headers=auth)
    assert r.status_code == 200
    assert {(row["kind"], row["agent_id"]) for row in r.json()} == {
        ("agent", "main"),
        ("feature", "feat-general-assistant"),
    }


async def test_double_bootstrap_returns_410(patched_app_client: Any) -> None:
    c, _srv, home = patched_app_client
    r = await bootstrap_admin(c, home)
    assert r.status_code == 201
    r = await c.post(
        "/api/setup/initial-admin",
        json={"username": "admin2", "password": "TestPass12"},
    )
    assert r.status_code == 410
    assert r.json()["error"]["code"] == "SETUP_REQUIRED"


async def test_start_writes_wizard_password_when_no_users(tmp_octop_home: Path) -> None:
    srv = OctopServer(home=tmp_octop_home)
    await srv.start()
    try:
        assert (tmp_octop_home.parent / WIZARD_FILE_NAME).exists()
    finally:
        await srv.stop()


async def test_start_skips_wizard_password_when_disabled(tmp_octop_home: Path) -> None:
    write_octop_config(tmp_octop_home, require_setup_password=False)
    srv = OctopServer(home=tmp_octop_home)
    await srv.start()
    try:
        assert not (tmp_octop_home / WIZARD_FILE_NAME).exists()
    finally:
        await srv.stop()


async def test_main_agent_seeds_general_assistant_workspace(patched_app_client: Any) -> None:
    """Finish seeds the main agent's managed workspace even before a provider exists."""
    c, srv, _home = patched_app_client
    await bootstrap_admin(c, _home)
    assert srv.paths is not None
    ws = srv.paths.agent_workspace("main")
    assert (ws / "SOUL.md").is_file()
    # Agent system files, including the bundled skill, live under the managed workspace.
    assert (ws / ".maitu" / "skills" / "octop-assistant" / "SKILL.md").is_file()


async def test_main_agent_uses_general_assistant_template(patched_app_client: Any) -> None:
    c, _srv, home = patched_app_client
    await bootstrap_admin(c, home)
    auth = await auth_header(c)
    r = await c.get("/api/agents", headers=auth)
    assert r.status_code == 200
    agent = r.json()[0]
    assert agent["agent_id"] == "main"
    assert agent["name"] == "小通 · 通用助手"
    assert agent.get("template_name") == "general-assistant"
