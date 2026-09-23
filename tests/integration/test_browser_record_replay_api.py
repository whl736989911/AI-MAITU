from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch


async def test_record_replay_status_returns_daemon_status(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "active": None}),
        ) as mock_send,
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value="rec_latest",
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 200
    assert r.json() == {"ok": True, "active": None, "latestRecordingId": "rec_latest"}
    mock_send.assert_awaited_once_with({"command": "status"})


async def test_record_status_reports_unresponsive_daemon(env: Any) -> None:
    """A daemon that is *there* but stopped answering is unknown state.

    It must not be dressed up as ``{"ok": True, "active": None}``: the
    dashboard reads that as "not recording", which is how a dead recorder
    stayed invisible.
    """
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.send_record_request",
        new=AsyncMock(side_effect=TimeoutError("daemon stalled")),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_record_status_reports_daemon_error_reply(env: Any) -> None:
    """An ``ok: False`` reply is a failure, not an empty status."""
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.send_record_request",
        new=AsyncMock(return_value={"ok": False, "error": "unknown command: status"}),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_record_status_is_idle_when_no_daemon_can_exist(env: Any) -> None:
    """No socket ⇒ no session: the recording lives inside the daemon process.

    That is the idle answer (200), so opening the chat page never reports a
    recorder the user never started.
    """
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(side_effect=FileNotFoundError("no daemon socket")),
        ),
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value="rec_latest",
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "active": None, "latestRecordingId": "rec_latest"}


async def test_record_status_is_idle_without_unix_socket_transport(env: Any) -> None:
    """Windows' event loop has no ``open_unix_connection`` member at all.

    ``harness_browser``'s daemon cannot run there, so the derived idle answer is
    the honest one — with the same predictability as a missing socket file.
    """
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(
                side_effect=AttributeError(
                    "module 'asyncio' has no attribute 'open_unix_connection'"
                )
            ),
        ),
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value=None,
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 200, r.text
    assert r.json() == {"ok": True, "active": None, "latestRecordingId": None}


async def test_record_status_does_not_swallow_unrelated_attribute_error(env: Any) -> None:
    """Only the missing-transport ``AttributeError`` means "no daemon".

    A plain one (a bug inside the probe) is a failure like any other.
    """
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.send_record_request",
        new=AsyncMock(side_effect=AttributeError("'NoneType' object has no attribute 'get'")),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)

    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_record_stop_reports_unresponsive_daemon(env: Any) -> None:
    """Stopping without a recording id probes status first.

    An unresponsive daemon must surface as 503, not as "recording not found"
    (404): the probe failing says nothing about whether a recording exists.
    """
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.send_record_request",
        new=AsyncMock(side_effect=TimeoutError("daemon stalled")),
    ):
        r = await client.post("/api/browser/record-replay/stop", headers=auth, json={})

    assert r.status_code == 503, r.text
    assert r.json()["error"]["code"] == "INTERNAL_ERROR"


async def test_record_replay_start_ensures_daemon_and_starts_recording(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ) as mock_ensure,
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_1", "daemon": True}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo", "name": "demo"},
        )

    assert r.status_code == 200
    assert r.json()["recordingId"] == "rec_1"
    mock_ensure.assert_awaited_once_with()
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": "demo",
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_start_ignores_requested_profile(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_1"}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo", "name": "demo"},
        )

    assert r.status_code == 200
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": "demo",
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_start_returns_503_when_daemon_fails(env: Any) -> None:
    client, _srv, auth = env

    with patch(
        "octop.api.routers.browser.record_replay.ensure_record_daemon",
        new=AsyncMock(return_value={"ok": False, "error": "Daemon did not start"}),
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_demo"},
        )

    assert r.status_code == 503
    body = r.json()
    assert body["error"]["details"]["recordReplay"]["error"] == "Daemon did not start"


async def test_record_replay_stop_generates_steps(env: Any) -> None:
    client, _srv, auth = env
    owned = SimpleNamespace(read_manifest=lambda _rid: SimpleNamespace(profile="user-1"))

    with (
        patch("harness_browser.record.store.RecordingStore", return_value=owned),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(
                return_value={"ok": True, "recordingId": "rec_1", "events": 4, "steps": 2}
            ),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/stop",
            headers=auth,
            json={"recordingId": "rec_1", "name": "demo"},
        )

    assert r.status_code == 200
    assert r.json()["steps"] == 2
    mock_send.assert_awaited_once_with(
        {
            "command": "stop",
            "recording_id": "rec_1",
            "generate_steps": True,
            "name": "demo",
        }
    )


async def test_record_replay_start_ignores_requested_agent_profile(env: Any) -> None:
    client, _srv, auth = env

    with (
        patch(
            "octop.api.routers.browser.record_replay.ensure_record_daemon",
            new=AsyncMock(return_value={"ok": True, "pid": 123}),
        ),
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(return_value={"ok": True, "recordingId": "rec_default"}),
        ) as mock_send,
    ):
        r = await client.post(
            "/api/browser/record-replay/start",
            headers=auth,
            json={"profile": "thr_123", "agentProfile": "default"},
        )

    assert r.status_code == 200
    mock_send.assert_awaited_once_with(
        {
            "command": "start",
            "profile": "user-1",
            "name": None,
            "privacy": "mask-sensitive",
            "screenshots": "off",
        }
    )


async def test_record_replay_replay_runs_runner(env: Any) -> None:
    client, _srv, auth = env
    runner = AsyncMock(return_value={"status": "passed", "recordingId": "rec_1"})

    with (
        patch(
            "harness_browser.record.store.RecordingStore",
            return_value=SimpleNamespace(
                read_manifest=lambda _rid: SimpleNamespace(profile="user-1")
            ),
        ),
        patch(
            "octop.api.routers.browser.record_replay.run_replay_recording",
            new=runner,
        ),
    ):
        r = await client.post(
            "/api/browser/record-replay/replay",
            headers=auth,
            json={"recordingId": "rec_1", "profile": "thr_demo-replay"},
        )

    assert r.status_code == 200
    assert r.json()["status"] == "passed"
    runner.assert_awaited_once_with("rec_1", profile="user-1", inputs={})


async def test_record_status_hides_other_user_active(env: Any) -> None:
    client, _srv, auth = env
    with (
        patch(
            "octop.api.routers.browser.record_replay.send_record_request",
            new=AsyncMock(
                return_value={
                    "ok": True,
                    "active": {"recordingId": "rec_x", "profile": "user-99"},
                }
            ),
        ),
        patch(
            "octop.api.routers.browser.record_replay._latest_recording_id",
            return_value=None,
        ),
    ):
        r = await client.get("/api/browser/record-replay/status", headers=auth)
    assert r.status_code == 200
    assert r.json()["active"] is None


async def test_record_skill_content_hides_other_user_recording(env: Any) -> None:
    client, _srv, auth = env
    store = SimpleNamespace(
        read_manifest=lambda _rid: SimpleNamespace(profile="user-99"),
    )
    with patch("harness_browser.record.store.RecordingStore", return_value=store):
        r = await client.post(
            "/api/browser/record-replay/skill-content",
            headers=auth,
            json={"recordingId": "rec_other"},
        )
    assert r.status_code == 404
