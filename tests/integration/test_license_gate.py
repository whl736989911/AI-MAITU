"""Instance-license integration gates.

Real licensed platform lifecycle: locked boot keeps login/activation working
while business HTTP/WS/CLI/direct entries stay blocked; a valid credential
starts business exactly once; expiry denies and cancels in-flight streams; a
higher restored version reopens once without duplicate scheduling.
"""

from __future__ import annotations

import asyncio
import time
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from starlette.websockets import WebSocketDisconnect

from octop.api.middleware.license_gate import LicenseGateMiddleware
from octop.infra.errors import ErrorCode, OctopError
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user
from tests.support.http import ws_connect, ws_token
from tests.support.licensing import CenterSigner, export_request


async def _bootstrap(client: httpx.AsyncClient, home: Any) -> dict[str, str]:
    await bootstrap_admin(client, home)
    return await auth_header(client)


async def test_unmanaged_license_status_does_not_lock_out_system_administrators(
    env: Any,
) -> None:
    client, server, admin_auth = env
    assert server.license_manager is None
    details_response = await client.get("/api/license/details", headers=admin_auth)
    assert details_response.status_code == 200
    details = details_response.json()
    assert details["managed"] is False
    assert details["business_allowed"] is True
    assert details["state"] is None
    assert details["credential_type"] is None
    assert (await client.get("/api/agents", headers=admin_auth)).status_code == 200

    enterprise_auth = await create_user(
        client, admin_auth, username="enterprise-admin", role="enterprise_admin", permissions=[]
    )
    public_response = await client.get("/api/license/status", headers=enterprise_auth)
    assert public_response.status_code == 200
    status = public_response.json()
    assert status["managed"] == details["managed"]
    assert status["business_allowed"] == details["business_allowed"]
    assert (await client.get("/api/agents", headers=enterprise_auth)).status_code == 200

    # Both administrator tiers may inspect status; only system admins may activate.
    assert (await client.get("/api/license/details")).status_code == 401
    enterprise_details = await client.get("/api/license/details", headers=enterprise_auth)
    assert enterprise_details.status_code == 200
    assert enterprise_details.json()["managed"] is False
    enterprise_activation = await client.post(
        "/api/license/activate", headers=enterprise_auth, json={"code": "unmanaged-test"}
    )
    assert enterprise_activation.status_code == 403
    activation = await client.post(
        "/api/license/activate", headers=admin_auth, json={"code": "unmanaged-test"}
    )
    assert activation.status_code == 403


async def test_locked_instance_blocks_business_but_allows_identity(
    license_center: CenterSigner, tmp_octop_home: Any
) -> None:
    async with octop_client(tmp_octop_home) as (client, srv):
        auth = await _bootstrap(client, tmp_octop_home)
        # Identity plane up (users + registry persistence), business plane down.
        assert srv.license_managed
        assert srv.app_runtime is not None
        assert srv.app_runtime.user_manager is not None
        assert srv.app_runtime.agent_registry is not None
        assert srv.app_runtime.gateway is None
        assert srv.app_runtime.cron_manager is None
        assert srv.app_runtime.proactive_scheduler is None
        assert srv.app_runtime.business_running is False

        # Business HTTP denied with the license envelope (423).
        response = await client.get("/api/agents", headers=auth)
        assert response.status_code == 423
        body = response.json()
        assert body["error"]["code"] == ErrorCode.INSTANCE_LICENSE_LOCKED.value

        response = await client.get("/api/cron", headers=auth)
        assert response.status_code == 423

        # Exempt identity/bootstrap/license routes stay reachable.
        assert (await client.get("/api/health")).status_code == 200
        response = await client.get("/api/settings/timezone", headers=auth)
        assert response.status_code == 200
        response = await client.get("/api/license/status")
        assert response.status_code == 200
        status = response.json()
        assert status["managed"] is True
        assert status["business_allowed"] is False
        assert status["state"] == "UNACTIVATED"

        # License admin API works while locked and yields a real request export.
        export = await export_request(client, auth)
        assert export["instance_id"]
        assert export["key_fingerprint"]
        assert export["proof"]

        # Reminder for an unactivated instance never shows.
        response = await client.post("/api/license/reminder", headers=auth)
        assert response.status_code == 200
        assert response.json()["show"] is False


async def test_ws_business_denied_with_license_close_code(
    license_center: CenterSigner, tmp_octop_home: Any
) -> None:
    async with octop_client(tmp_octop_home) as (client, srv):
        auth = await _bootstrap(client, tmp_octop_home)
        with pytest.raises(WebSocketDisconnect) as excinfo:
            async with ws_connect(
                client._octop_app,  # type: ignore[attr-defined]
                f"/api/notifications/ws?token={ws_token(auth)}",
            ) as websocket:
                await websocket.receive_json()
        assert excinfo.value.code == 4423


async def test_activation_starts_business_once_and_expiry_stops_and_restore_reopens(
    license_center: CenterSigner, tmp_octop_home: Any, monkeypatch: pytest.MonkeyPatch
) -> None:
    async with octop_client(tmp_octop_home) as (client, srv):
        auth = await _bootstrap(client, tmp_octop_home)
        export = await export_request(client, auth)

        # In-flight stream registered before the unlock transition: an unlock
        # must not cancel it, only a lock does.
        gate = LicenseGateMiddleware.live
        assert gate is not None
        victim = asyncio.create_task(asyncio.sleep(60), name="stream-victim")
        gate._active.add(victim)  # type: ignore[attr-defined]

        token = license_center.offline(export["instance_id"], export["key_fingerprint"])
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": token}
        )
        assert response.status_code == 200, response.text
        snapshot = response.json()
        assert snapshot["business_allowed"] is True
        assert snapshot["credential_type"] == "offline"
        license_id = snapshot["license_id"]
        await asyncio.sleep(0)
        assert not victim.done()  # unlock direction never cancels

        # Unlock transition started business exactly once.
        assert srv.app_runtime is not None
        assert srv.app_runtime.business_running is True
        gateway = srv.app_runtime.gateway
        assert gateway is not None
        cron = srv.app_runtime.cron_manager
        assert cron is not None

        # A redundant effective allowed=True must not start anything again.
        await srv._on_license_change(True)
        assert srv.app_runtime.gateway is gateway
        assert srv.app_runtime.cron_manager is cron

        # Business HTTP now allowed.
        response = await client.get("/api/agents", headers=auth)
        assert response.status_code == 200

        # A signed online expiry denial invalidates the earlier offline grant too.
        manager = srv.license_manager
        assert manager is not None
        manager.center_url = "https://center.example"

        async def expired_center_response(path: str, payload: dict[str, Any]) -> dict[str, Any]:
            if path.endswith("/challenges"):
                return {"challenge": "expiry-denial-challenge"}
            now = int(time.time())
            return {
                "token": license_center.online(
                    export["instance_id"],
                    export["key_fingerprint"],
                    license_id=license_id,
                    license_version=2,
                    status="expired",
                    not_before=now - 400,
                    license_expires_at=now - 200,
                    valid_until=now + 60,
                    challenge=payload["challenge"],
                )
            }

        monkeypatch.setattr(manager, "_request", expired_center_response)
        # WS business now accepted.
        async with ws_connect(
            client._octop_app,  # type: ignore[attr-defined]
            f"/api/notifications/ws?token={ws_token(auth)}",
        ) as ws:
            await ws.send_json({"type": "ping"})
            assert await ws.receive_json() == {"type": "pong"}
            response = await client.post("/api/license/refresh", headers=auth)
            assert response.status_code == 200, response.text
            snapshot = response.json()
            assert snapshot["business_allowed"] is False
            with pytest.raises(WebSocketDisconnect) as closed:
                await ws.receive_json()
            assert closed.value.code == 4423

        # Business stopped: controlled components down, route denied again,
        # and the in-flight stream was cancelled by the lock transition.
        assert srv.app_runtime is not None
        assert srv.app_runtime.business_running is False
        assert srv.app_runtime.gateway is None
        assert srv.app_runtime.cron_manager is None
        assert srv.app_runtime.agent_registry is not None  # identity stays
        response = await client.get("/api/agents", headers=auth)
        assert response.status_code == 423
        await asyncio.sleep(0)
        assert victim.cancelled()

        # ---- same-version replay and lower version cannot unlock a denial ----
        replay = license_center.offline(
            export["instance_id"],
            export["key_fingerprint"],
            license_id=license_id,
            license_version=1,
        )
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": replay}
        )
        assert response.status_code == 400
        assert response.json()["error"]["code"] == ErrorCode.INSTANCE_LICENSE_INVALID.value
        assert srv.app_runtime is not None and srv.app_runtime.business_running is False

        # ---- higher restored version reopens exactly once ----
        restore = license_center.offline(
            export["instance_id"],
            export["key_fingerprint"],
            license_id=license_id,
            license_version=3,
            restored=True,
            days=30,
        )
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": restore}
        )
        assert response.status_code == 200, response.text
        assert response.json()["business_allowed"] is True
        assert srv.app_runtime is not None
        assert srv.app_runtime.business_running is True
        restored_gateway = srv.app_runtime.gateway
        assert restored_gateway is not None and restored_gateway is not gateway
        # Fresh scheduler: no backlog or duplicate scheduling carried over.
        assert srv.app_runtime.cron_manager is not None
        assert srv.app_runtime.cron_manager is not cron

        await srv._on_license_change(True)
        assert srv.app_runtime.gateway is restored_gateway


async def test_direct_domain_entries_raise_when_locked(
    license_center: CenterSigner, tmp_path: Any
) -> None:
    """Direct (non-HTTP) callers cannot bypass the middleware gate."""
    from octop.cli.support import offline_ops
    from octop.infra.knowledge.jobs import resume_pending_index_jobs
    from octop.infra.licensing import (
        LicenseManager,
        current_license,
        install_license_manager,
    )

    key_file = tmp_path / "trust" / "license-trusted-keys.json"
    signer = CenterSigner()
    signer.write_trusted_keys(key_file)
    manager = LicenseManager(tmp_path / "state", key_file)  # managed, unactivated
    install_license_manager(manager)
    try:
        assert manager.managed and not manager.business_allowed

        # Knowledge index processing boundary.
        with pytest.raises(OctopError) as excinfo:
            resume_pending_index_jobs(None)
        assert excinfo.value.code == ErrorCode.INSTANCE_LICENSE_LOCKED

        # Offline CLI business ops boundary (user management stays allowed).
        for call in (
            lambda: offline_ops.list_cron_offline("agent-x"),
            lambda: offline_ops.create_cron_offline(
                agent_id="agent-x", user_id=1, trigger="* * * * *", prompt="hi"
            ),
            lambda: offline_ops.list_channels_offline("agent-x"),
            lambda: offline_ops.list_providers_offline(),
            lambda: offline_ops.create_thread_offline(agent_id="agent-x", user_id=1),
        ):
            with pytest.raises(OctopError) as excinfo:
                call()
            assert excinfo.value.code == ErrorCode.INSTANCE_LICENSE_LOCKED

        # Manager API surface: business access denied, snapshot honest.
        with pytest.raises(OctopError) as excinfo:
            manager.require_business_access()
        assert excinfo.value.code == ErrorCode.INSTANCE_LICENSE_LOCKED
        snap = manager.snapshot()
        assert snap["managed"] is True
        assert snap["business_allowed"] is False
        assert snap["state"] == "UNACTIVATED"

        # Unmanaged installs keep passing everywhere (existing behavior).
        install_license_manager(LicenseManager(tmp_path / "state-unmanaged", None))
        assert current_license().business_allowed is True
        stub_services = SimpleNamespace(
            knowledge_repo=SimpleNamespace(resume_pending_documents=lambda: [])
        )
        resume_pending_index_jobs(stub_services)  # must not raise
    finally:
        install_license_manager(LicenseManager(tmp_path / "state-default", None))


async def test_managed_defaults_deny_terminal_and_unconfined_agents(
    license_center: CenterSigner, tmp_octop_home: Any, tmp_path: Any
) -> None:
    """Managed hardening is license-independent: active license, shell still denied."""
    from octop.infra.agents.manager import AgentCreateSpec

    async with octop_client(tmp_octop_home) as (client, srv):
        auth = await _bootstrap(client, tmp_octop_home)
        export = await export_request(client, auth)
        token = license_center.offline(export["instance_id"], export["key_fingerprint"])
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": token}
        )
        assert response.status_code == 200
        assert srv.app_runtime is not None and srv.app_runtime.business_running is True

        # Web host terminal denied for enterprise admins even when licensed.
        agent_id = srv.app_runtime.agent_registry.list_rows()[0].agent_id
        response = await client.get(f"/api/agents/{agent_id}/terminal/context", headers=auth)
        assert response.status_code == 403
        with pytest.raises(WebSocketDisconnect) as excinfo:
            async with ws_connect(
                client._octop_app,  # type: ignore[attr-defined]
                f"/api/agents/{agent_id}/terminal/ws?token={ws_token(auth)}",
            ):
                pass
        assert excinfo.value.code == 4403

        # Plugins: custom executables are refused; vetted bundled only.

        pm = srv.plugin_manager
        assert pm is not None
        with pytest.raises(OctopError) as excinfo:
            pm.install_archive(tmp_path / "whatever.zip")
        assert excinfo.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
        with pytest.raises(OctopError) as excinfo:
            pm.load_installed(install_deps=True)
        assert excinfo.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED

        # Agent confinement rejects escaped workspaces before persisting an agent.
        registry = srv.app_runtime.agent_registry
        admin = (
            srv.user_manager.list_admins()[0]
            if hasattr(srv.user_manager, "list_admins")
            else next(u for u in srv.user_manager.list() if u.is_admin)
        )
        spec = AgentCreateSpec(
            name="escaped-ws",
            user_id=admin.id,
            config={"workspace_dir": str(tmp_path / "outside-data-root")},
        )
        with pytest.raises(OctopError) as excinfo:
            await registry.create(spec, defer_bootstrap=True)
        assert excinfo.value.code == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED
        assert all(row.name != "escaped-ws" for row in registry.list_rows())


async def test_reminder_claims_once_per_day_near_expiry(
    license_center: CenterSigner, tmp_octop_home: Any
) -> None:
    async with octop_client(tmp_octop_home) as (client, srv):
        auth = await _bootstrap(client, tmp_octop_home)
        export = await export_request(client, auth)

        # Far expiry (>72h): no reminder.
        token = license_center.offline(export["instance_id"], export["key_fingerprint"])
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": token}
        )
        assert response.status_code == 200
        response = await client.post("/api/license/reminder", headers=auth)
        assert response.status_code == 200
        assert response.json()["show"] is False

        # Near-expiry renewal (<72h, higher version): reminder shows once today.
        near = license_center.offline(
            export["instance_id"],
            export["key_fingerprint"],
            license_version=2,
            days=1,
        )
        response = await client.post(
            "/api/license/offline-import", headers=auth, json={"token": near}
        )
        assert response.status_code == 200
        response = await client.post("/api/license/reminder", headers=auth)
        assert response.status_code == 200
        body = response.json()
        assert body["show"] is True
        assert body["expires_at"] is not None
        assert 0 < body["remaining_seconds"] <= 259200
        assert body["date"]

        # Same user again the same day: claimed once.
        response = await client.post("/api/license/reminder", headers=auth)
        assert response.status_code == 200
        assert response.json()["show"] is False
