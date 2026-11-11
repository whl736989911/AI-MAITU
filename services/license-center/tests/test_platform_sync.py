from __future__ import annotations

import asyncio
import json
from pathlib import Path

import jwt
import pytest
from helpers import active_license_public_pem, customer_and_activation

from license_center import storage


@pytest.mark.parametrize("trigger", ["startup", "refresh"])
def test_platform_sync_updates_center_contact(center, tmp_path, monkeypatch, trigger):
    # Exercise the customer client against the center's real signature verifier.
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "src"))
    from octop.infra.licensing import LicenseManager

    client, csrf = center
    _, activation = customer_and_activation(client, csrf)
    trust = tmp_path / "platform-trust.json"
    trust.write_text(
        json.dumps({"test-license-key": active_license_public_pem(client)}), encoding="utf-8"
    )
    state_dir = tmp_path / "platform-state"

    async def request(path, body):
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def exercise():
        manager = LicenseManager(state_dir, trust, "https://127.0.0.1", allow_loopback=True)
        monkeypatch.setattr(manager, "_request", request)
        await manager.activate(activation["code"])
        with storage.transaction() as conn:
            conn.execute(
                "UPDATE instances SET last_seen_at=NULL WHERE id=?", (manager.instance_id,)
            )
        earliest_contact = storage.now()

        restarted = LicenseManager(state_dir, trust, "https://127.0.0.1", allow_loopback=True)
        monkeypatch.setattr(restarted, "_request", request)
        try:
            if trigger == "startup":
                await restarted.start()
            else:
                await restarted.refresh()
            snapshot = restarted.snapshot()
            assert snapshot["sync_error"] is None
            assert snapshot["last_sync_at"] >= earliest_contact
            assert snapshot["business_allowed"] is True
            response = client.get(f"/api/v1/admin/instances/{restarted.instance_id}")
            assert response.status_code == 200, response.text
            assert response.json()["last_seen_at"] >= earliest_contact
        finally:
            await restarted.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("individually_revoked", [False, True])
def test_customer_reactivation_respects_individual_revocation(
    center, tmp_path, monkeypatch, individually_revoked
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "src"))
    from octop.infra.licensing import LicenseManager

    client, csrf = center
    customer, activation = customer_and_activation(client, csrf)
    trust = tmp_path / "trust.json"
    trust.write_text(json.dumps({"test-license-key": active_license_public_pem(client)}))

    async def request(path, body):
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def exercise():
        manager = LicenseManager(
            tmp_path / "state", trust, "https://127.0.0.1", allow_loopback=True
        )
        monkeypatch.setattr(manager, "_request", request)
        try:
            await manager.activate(activation["code"])
            details = client.get(f"/api/v1/admin/instances/{manager.instance_id}").json()
            if individually_revoked:
                response = client.post(
                    f"/api/v1/admin/licenses/{details['license_id']}/revoke",
                    headers=csrf,
                    json={"reason": "Keep this instance suspended", "version": 1},
                )
                assert response.status_code == 200, response.text
            for version, active in ((1, False), (2, True)):
                response = client.put(
                    f"/api/v1/admin/customers/{customer['id']}",
                    headers=csrf,
                    json={
                        "name": customer["name"],
                        "contact": customer["contact"],
                        "notes": customer["notes"],
                        "active": active,
                        "version": version,
                        "reason": "Restore customer" if active else "Suspend customer",
                    },
                )
                assert response.status_code == 200, response.text
                await manager.refresh()
                assert manager.snapshot()["business_allowed"] is (
                    active and not individually_revoked
                )
                overview = client.get("/api/v1/admin/overview").json()
                assert overview["active_licenses"] == int(active and not individually_revoked)
                assert overview["expiring_soon"] == int(active and not individually_revoked)
            with storage.connect() as conn:
                row = conn.execute(
                    "SELECT revoked FROM licenses WHERE id=?", (details["license_id"],)
                ).fetchone()
            assert bool(row["revoked"]) is individually_revoked
        finally:
            await manager.stop()

    asyncio.run(exercise())


def test_replaced_instance_receives_repeatable_signed_revocation(center, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "src"))
    from octop.infra.licensing import LicenseManager

    client, csrf = center
    customer, activation = customer_and_activation(client, csrf)
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps({"test-license-key": active_license_public_pem(client)}), encoding="utf-8"
    )

    async def request(path, body):
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def exercise():
        manager = LicenseManager(
            tmp_path / "state", trust, "https://127.0.0.1", allow_loopback=True
        )
        monkeypatch.setattr(manager, "_request", request)
        try:
            activated = await manager.activate(activation["code"])
            offline = client.post(
                "/api/v1/admin/offline-licenses",
                headers=csrf,
                json={
                    "customer_id": customer["id"],
                    "request": manager.export_request(),
                    "license_expires_at": activated["license_expires_at"],
                    "offline_expires_at": storage.now() + 7 * 86400,
                    "reason": "Keep an offline grant before replacement",
                },
            )
            assert offline.status_code == 200, offline.text
            await manager.import_offline(offline.json()["token"])
            replaced = client.post(
                f"/api/v1/admin/instances/{manager.instance_id}/replace",
                headers=csrf,
                json={"reason": "Move to a new host", "version": 1},
            )
            assert replaced.status_code == 200, replaced.text
            for _ in range(2):
                snapshot = await manager.refresh()
                assert snapshot["business_allowed"] is False
                assert snapshot["reason"] == "revoked"
                assert snapshot["sync_error"] is None
                assert snapshot["license_version"] == 2
            with pytest.raises(ValueError):
                await manager.import_offline(offline.json()["token"])
            assert manager.business_allowed is False
        finally:
            await manager.stop()

    asyncio.run(exercise())


def test_first_offline_grant_can_activate_and_survive_restart(center, tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "src"))
    from octop.infra.licensing import LicenseManager

    client, csrf = center
    customer_response = client.post(
        "/api/v1/admin/customers", headers=csrf, json={"name": "Offline-only customer"}
    )
    assert customer_response.status_code == 201, customer_response.text
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps({"test-license-key": active_license_public_pem(client)}), encoding="utf-8"
    )
    state_dir = tmp_path / "offline-state"

    async def exercise():
        manager = LicenseManager(state_dir, trust)
        try:
            response = client.post(
                "/api/v1/admin/offline-licenses",
                headers=csrf,
                json={
                    "customer_id": customer_response.json()["id"],
                    "request": manager.export_request(),
                    "license_expires_at": storage.now() + 30 * 86400,
                    "offline_expires_at": storage.now() + 7 * 86400,
                    "reason": "First installation without online activation",
                },
            )
            assert response.status_code == 200, response.text
            snapshot = await manager.import_offline(response.json()["token"])
            assert snapshot["business_allowed"] is True
            assert snapshot["credential_type"] == "offline"
            restarted = LicenseManager(state_dir, trust)
            try:
                assert restarted.business_allowed is True
                assert restarted.snapshot()["license_version"] == 1
            finally:
                await restarted.stop()
        finally:
            await manager.stop()

    asyncio.run(exercise())


@pytest.mark.parametrize("operation", ["activate", "sync", "offline"])
def test_possession_proof_allows_advisory_future_client_time(
    center, tmp_path, monkeypatch, operation
):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[3] / "src"))
    from octop.infra.licensing import LicenseManager

    client, csrf = center
    customer, activation = customer_and_activation(client, csrf)
    trust = tmp_path / "trust.json"
    trust.write_text(
        json.dumps({"test-license-key": active_license_public_pem(client)}), encoding="utf-8"
    )

    async def request(path, body):
        response = client.post(path, json=body)
        response.raise_for_status()
        return response.json()

    async def exercise():
        manager = LicenseManager(tmp_path / "state", trust, "https://127.0.0.1")
        monkeypatch.setattr(manager, "_request", request)

        def ahead(proof):
            claims = jwt.decode(proof, options={"verify_signature": False})
            claims["iat"] = storage.now() + 120
            return jwt.encode(
                claims,
                manager._private_key,
                algorithm="EdDSA",
                headers={"kid": "maitu-license-1", "typ": "JWT"},
            )

        try:
            if operation == "offline":
                offline_request = manager.export_request()
                offline_request["proof"] = ahead(offline_request["proof"])
                response = client.post(
                    "/api/v1/admin/offline-licenses",
                    headers=csrf,
                    json={
                        "customer_id": customer["id"],
                        "request": offline_request,
                        "license_expires_at": storage.now() + 30 * 86400,
                        "offline_expires_at": storage.now() + 7 * 86400,
                        "reason": "Client proof timestamp is not the center clock",
                    },
                )
                assert response.status_code == 200, response.text
                snapshot = await manager.import_offline(response.json()["token"])
            else:
                if operation == "sync":
                    await manager.activate(activation["code"])
                original_proof = manager._proof

                async def proof(op, challenge, body):
                    return ahead(await original_proof(op, challenge, body))

                monkeypatch.setattr(manager, "_proof", proof)
                snapshot = (
                    await manager.refresh()
                    if operation == "sync"
                    else await manager.activate(activation["code"])
                )
            assert snapshot["business_allowed"] is True
            restarted = LicenseManager(tmp_path / "state", trust)
            try:
                assert restarted.business_allowed is True
            finally:
                await restarted.stop()
        finally:
            await manager.stop()

    asyncio.run(exercise())
