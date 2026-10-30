"""A remote-control connection must lose access without reconnecting."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect

from tests.support.auth import create_user
from tests.support.http import ws_connect, ws_token


@pytest.mark.parametrize("capability", ["browser", "desktop", "mobile"])
async def test_connected_control_route_rejects_input_after_permission_revocation(
    env: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capability: str
) -> None:
    client, _server, admin = env
    monkeypatch.setattr("octop.infra.users.permissions.license_managed_install", lambda: False)
    auth = await create_user(
        client, admin, username=f"control_{capability}", permissions=[capability]
    )
    user_id = (await client.get("/api/auth/me", headers=auth)).json()["id"]
    marker = tmp_path / "controlled-resource.txt"
    performed = asyncio.Event()
    loop = asyncio.get_running_loop()

    def write_value(value: str) -> None:
        marker.write_text(value)
        loop.call_soon_threadsafe(performed.set)

    async def frames(websocket: Any, *args: Any, **kwargs: Any) -> None:
        await websocket.send_text(json.dumps({"type": "ready"}))
        await asyncio.Event().wait()

    if capability == "browser":
        from octop.api.routers.browser import stream as module

        async def navigate(url: str) -> None:
            write_value(url)

        monkeypatch.setattr(
            module,
            "resolve_harness_session",
            AsyncMock(return_value=SimpleNamespace(navigate=navigate)),
        )
        monkeypatch.setattr(module, "_stream_loop", frames)
        operation = {"type": "navigate", "url": "https://allowed.invalid"}
    elif capability == "desktop":
        from octop.api.routers.desktop import stream as module

        monkeypatch.setattr(
            module,
            "desktop_status",
            lambda **kwargs: SimpleNamespace(setup_state="ready", ok=True, display="test"),
        )
        session = SimpleNamespace(display="test", input=SimpleNamespace(type_text=write_value))
        monkeypatch.setattr(module, "acquire_session", AsyncMock(return_value=session))
        monkeypatch.setattr(module, "release_session", AsyncMock())
        monkeypatch.setattr(module, "supersede_user_stream", AsyncMock())
        monkeypatch.setattr(module, "clear_user_stream", AsyncMock())
        monkeypatch.setattr(module, "_stream_loop", frames)
        operation = {"type": "type", "text": "https://allowed.invalid"}
    else:
        from octop.api.routers.mobile import stream as module

        monkeypatch.setattr(
            module,
            "mobile_status",
            lambda *args, **kwargs: SimpleNamespace(
                setup_state="ready", ok=True, selected_device="test-device"
            ),
        )
        monkeypatch.setattr(module, "_stream_frames", frames)
        monkeypatch.setattr(module, "find_adb", lambda: "test-adb")
        monkeypatch.setattr(module, "set_mobile_agent_control", lambda **kwargs: None)
        monkeypatch.setattr(module, "clear_mobile_agent_control_if_device", lambda device: None)
        monkeypatch.setattr(module, "input_text", lambda device, text, **kwargs: write_value(text))
        operation = {"type": "type", "text": "https://allowed.invalid"}

    app = FastAPI()
    app.state.octop_server = _server
    app.include_router(module.router, prefix="/api")
    path = f"/api/{capability}-stream/ws?token={ws_token(auth)}"
    async with ws_connect(app, path) as socket:
        await socket.send_json({"type": "start", "codec": "jpeg"})
        assert (await socket.receive_json())["type"] == "ready"
        await socket.send_json(operation)
        await asyncio.wait_for(performed.wait(), timeout=5)
        assert marker.read_text() == "https://allowed.invalid"

        response = await client.patch(
            f"/api/users/{user_id}", headers=admin, json={"permissions": []}
        )
        assert response.status_code == 200, response.text
        operation = {
            key: value.replace("allowed.invalid", "revoked.invalid")
            if isinstance(value, str)
            else value
            for key, value in operation.items()
        }
        await socket.send_json(operation)
        with pytest.raises(WebSocketDisconnect) as exc:
            while True:
                await socket.receive_json()
        assert exc.value.code == 4003
        assert marker.read_text() == "https://allowed.invalid"
