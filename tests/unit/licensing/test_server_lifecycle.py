from __future__ import annotations

import asyncio
import json
import time

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from octop.api.app import build_app
from octop.config import DatabaseConfig
from octop.infra.db.rebind import persist_database_config, rebind_control_plane
from octop.infra.server import OctopServer
from octop.infra.users.identity import Role
from tests.support.app import ensure_control_plane_bound, write_octop_config
from tests.support.licensing import provision_license, public_key_pem


@pytest.mark.asyncio
async def test_locked_server_keeps_auth_and_maintenance_then_recovers(tmp_path, monkeypatch):
    home = tmp_path / "instance"
    signer = Ed25519PrivateKey.generate()
    trust = tmp_path / "trust.json"
    trust.write_text(json.dumps({"test": public_key_pem(signer)}), encoding="utf-8")
    monkeypatch.setenv("OCTOP_LICENSE_TRUST_FILE", str(trust))
    write_octop_config(home, database={"driver": "sqlite"}, require_setup_password=False)
    server = OctopServer(home)
    await server.start()
    await ensure_control_plane_bound(server)
    try:
        assert server.user_manager is not None
        await server.user_manager.create(
            username="trial-admin", password="TrialAdmin35!", role=Role.ENTERPRISE_ADMIN
        )
        app = build_app(server)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            login = await client.post(
                "/api/auth/login", json={"username": "trial-admin", "password": "TrialAdmin35!"}
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            assert (await client.get("/api/agents", headers=headers)).status_code == 403
            assert (await client.get("/api/license/status")).json()["allowed"] is False

            assert server.app_runtime is not None
            maintenance = asyncio.Event()

            async def maintained():
                maintenance.set()

            server.app_runtime.cron_manager.schedule_system_job(
                "license-regression-maintenance", trigger="interval:1", func=maintained
            )
            await asyncio.wait_for(maintenance.wait(), timeout=5)

            provision_license(home, signer=signer)
            assert server.license_service is not None
            server.license_service.load_cached()
            await server.license_service._notify()
            assert server._business_start_task is not None
            await asyncio.wait_for(asyncio.shield(server._business_start_task), timeout=20)
            assert (await client.get("/api/agents", headers=headers)).status_code == 200

            begun = asyncio.Event()

            async def business():
                async with server.license_service.business_task():
                    begun.set()
                    await asyncio.Event().wait()

            running = asyncio.create_task(business())
            await begun.wait()
            provision_license(home, signer=signer, status="revoked", revision=2)
            server.license_service.load_cached()
            await server.license_service._notify()
            with pytest.raises(asyncio.CancelledError):
                await running
            denied = await client.get("/api/agents", headers=headers)
            assert denied.status_code == 403
            assert denied.json()["error"]["code"] == "INSTANCE_LICENSE_LOCKED"
            assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
            maintenance.clear()
            await asyncio.wait_for(maintenance.wait(), timeout=5)

            provision_license(home, signer=signer, revision=3, deadline=int(time.time()) + 172800)
            server.license_service.load_cached()
            await server.license_service._notify()
            assert server._business_start_task is not None
            await asyncio.wait_for(asyncio.shield(server._business_start_task), timeout=20)
            assert (await client.get("/api/agents", headers=headers)).status_code == 200
            assert (await client.get("/api/auth/me", headers=headers)).json()[
                "username"
            ] == "trial-admin"
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_invalid_license_trust_config_keeps_auth_status_and_maintenance(
    tmp_path, monkeypatch, caplog
):
    home = tmp_path / "instance"
    monkeypatch.setenv("OCTOP_LICENSE_TRUST_FILE", str(tmp_path / "missing-trust.json"))
    write_octop_config(home, database={"driver": "sqlite"}, require_setup_password=False)
    server = OctopServer(home)
    await server.start()
    await ensure_control_plane_bound(server)
    try:
        assert server.user_manager is not None
        assert server.license_service is not None
        assert server.license_service.allowed is False
        assert server.license_service.server_url is None
        assert server.license_service.trusted_keys == {}
        assert server.license_service.ca_file == ""
        assert "invalid license configuration" in caplog.text

        await server.user_manager.create(
            username="locked-admin", password="LockedAdmin35!", role=Role.ENTERPRISE_ADMIN
        )
        app = build_app(server)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://testserver"
        ) as client:
            login = await client.post(
                "/api/auth/login",
                json={"username": "locked-admin", "password": "LockedAdmin35!"},
            )
            assert login.status_code == 200
            headers = {"Authorization": f"Bearer {login.json()['access_token']}"}
            assert (await client.get("/api/auth/me", headers=headers)).status_code == 200
            assert (await client.get("/api/license/status")).json()["allowed"] is False
            assert (await client.get("/api/agents", headers=headers)).status_code == 403

        assert server.app_runtime is not None
        maintenance = asyncio.Event()

        async def maintained():
            maintenance.set()

        server.app_runtime.cron_manager.schedule_system_job(
            "invalid-license-config-maintenance", trigger="interval:1", func=maintained
        )
        await asyncio.wait_for(maintenance.wait(), timeout=5)
    finally:
        await server.stop()


@pytest.mark.asyncio
async def test_rebind_while_locked_retargets_actual_authentication_database(tmp_path):
    home = tmp_path / "instance"
    write_octop_config(home, database={"driver": "sqlite"}, require_setup_password=False)
    server = OctopServer(home)
    await server.start()
    await ensure_control_plane_bound(server)
    try:
        persist_database_config(
            server.paths.config, DatabaseConfig(sqlite_path="new-control.sqlite")
        )
        rebind_control_plane(server)
        assert server.user_manager is not None
        await server.user_manager.create(
            username="new-db-admin", password="NewDatabase35!", role=Role.ADMIN
        )
        user = await server.user_manager.authenticate("new-db-admin", "NewDatabase35!")
        assert user is not None and user.username == "new-db-admin"
        assert server.services is not None
        with server.services.db.connect() as conn:
            row = conn.execute(
                "SELECT username FROM users WHERE username=?", ("new-db-admin",)
            ).fetchone()
        assert row is not None and row[0] == "new-db-admin"
    finally:
        await server.stop()
