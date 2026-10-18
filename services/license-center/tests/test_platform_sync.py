from __future__ import annotations

import asyncio
import json
from pathlib import Path

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
