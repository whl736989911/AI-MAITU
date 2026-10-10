"""Deployment lockdown regressions for browser recording daemon access."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_recording_routes_are_denied_before_daemon_or_store_access(env: Any) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="record-member")
    send_request = AsyncMock(side_effect=AssertionError("browser daemon must not be called"))
    ensure_daemon = AsyncMock(side_effect=AssertionError("browser daemon must not start"))
    replay_runner = AsyncMock(side_effect=AssertionError("replay runner must not run"))

    with (
        patch("octop.api.routers.browser.record_replay.send_record_request", send_request),
        patch("octop.api.routers.browser.record_replay.ensure_record_daemon", ensure_daemon),
        patch("octop.api.routers.browser.record_replay.run_replay_recording", replay_runner),
    ):
        for auth in (admin_auth, member_auth):
            for path, payload in (
                ("/api/browser/record-replay/start", {"name": "restricted"}),
                ("/api/browser/record-replay/stop", {}),
                ("/api/browser/record-replay/replay", {"recordingId": "not-run"}),
            ):
                response = await client.post(path, headers=auth, json=payload)
                assert response.status_code == 403, response.text
                assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"

    send_request.assert_not_awaited()
    ensure_daemon.assert_not_awaited()
    replay_runner.assert_not_awaited()
