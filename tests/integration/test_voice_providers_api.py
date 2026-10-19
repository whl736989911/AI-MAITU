"""tests/integration/test_voice_providers_api.py — voice provider list access.

The user-facing ``GET /api/voice/providers`` returns rows that carry the
configured API key (the Models → Voice tab prefills it), so it must be gated by
the same ``voice`` module key as the write endpoints.
"""

from __future__ import annotations

import json

TENCENT_EXTRA = {"secret_id": "sid", "secret_key": "sk", "region": "ap-guangzhou"}


async def _seed_voice_provider(c, auth):
    r = await c.post(
        "/api/admin/voice/providers",
        headers=auth,
        json={
            "name": "tencent-asr",
            "kind": "tencent",
            "capability": "both",
            "api_key": "sid:sk",
            "extra_json": json.dumps(TENCENT_EXTRA),
        },
    )
    assert r.status_code == 201
    return r.json()


async def test_regular_user_cannot_list_voice_providers(env):
    """A caller without the voice key gets no voice provider rows at all."""
    from tests.support.auth import create_user

    c, _srv, admin_auth = env
    await _seed_voice_provider(c, admin_auth)

    user_auth = await create_user(c, admin_auth, username="plain-voice-reader")
    r = await c.get("/api/voice/providers", headers=user_auth)
    assert r.status_code == 403
    assert r.json()["error"]["code"] == "FORBIDDEN"

    # The admin-only view stays closed to them as well.
    r = await c.get("/api/admin/voice/providers", headers=user_auth)
    assert r.status_code == 403


async def test_voice_manager_reads_providers_through_user_endpoint(env):
    """The Voice tab (which requires the voice key) keeps its key prefill path."""
    from tests.support.auth import create_user

    c, _srv, admin_auth = env
    created = await _seed_voice_provider(c, admin_auth)
    assert created["api_key"] == "sid:sk"

    voice_auth = await create_user(
        c,
        admin_auth,
        username="voice-manager",
        permissions=["voice"],
    )
    r = await c.get("/api/voice/providers", headers=voice_auth)
    assert r.status_code == 200
    row = next(p for p in r.json() if p["name"] == "tencent-asr")
    assert row["api_key"] == "sid:sk"
    assert row["extra"]["secret_key"] == "sk"
