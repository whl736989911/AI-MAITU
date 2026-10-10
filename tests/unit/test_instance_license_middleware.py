from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route, WebSocketRoute
from starlette.testclient import TestClient, WebSocketDisconnect

from octop.api.middleware.instance_license import install
from octop.infra.errors import ErrorCode, OctopError
from tests.support.licensing import provision_license, sign_license


class _License:
    def __init__(self, allowed: bool) -> None:
        self.allowed = allowed
        self.connections = set()

    def require_business_access(self) -> None:
        if not self.allowed:
            raise OctopError(ErrorCode.INSTANCE_LICENSE_LOCKED, "locked")

    @asynccontextmanager
    async def business_task(self) -> AsyncIterator[None]:
        self.require_business_access()
        yield

    def status(self) -> dict[str, object]:
        return {
            "state": "ACTIVE" if self.allowed else "LOCKED",
            "allowed": self.allowed,
            "reason": None if self.allowed else "expired",
        }

    def register_connection(self, callback):
        self.connections.add(callback)
        return lambda: self.connections.discard(callback)


async def _ok(request: Request) -> JSONResponse:
    return JSONResponse({"ok": True})


async def _websocket(websocket) -> None:
    await websocket.accept()
    async for _message in websocket.iter_text():
        pass


def _app(license: _License) -> Starlette:
    app = Starlette(
        routes=[
            Route("/api/license/status", _ok),
            Route("/api/business", _ok),
            Route("/api/plugins/install", _ok, methods=["POST"]),
            Route("/api/envs", _ok, methods=["GET", "PUT"]),
            Route("/api/desktop/status", _ok),
            Route("/api/filesystem/dirs", _ok),
            WebSocketRoute("/api/business/ws", _websocket),
        ]
    )
    install(app, license)
    return app


def _revoke(service, signer: Ed25519PrivateKey) -> None:
    now = int(time.time())
    details = service.details()
    challenge = secrets.token_urlsafe(32)
    service._accept_token(
        sign_license(
            signer,
            {
                "protocol_version": 1,
                "product": "maitu",
                "iss": "maitu-license-center",
                "aud": details["instance_id"],
                "instance_id": details["instance_id"],
                "instance_key_fingerprint": details["public_key_fingerprint"],
                "challenge": challenge,
                "license_id": "test-license",
                "license_revision": details["license_revision"] + 1,
                "status": "revoked",
                "iat": now,
                "exp": now + 300,
                "license_expires_at": details["license_expires_at"],
            },
        ),
        expected_challenge=challenge,
    )


def _active_service(tmp_path):
    signer = Ed25519PrivateKey.generate()
    service = provision_license(tmp_path, signer=signer)
    return service, signer


@pytest.mark.asyncio
async def test_real_service_revocation_cancels_inflight_http_and_keeps_status_available(
    tmp_path,
) -> None:
    service, signer = _active_service(tmp_path)
    started = asyncio.Event()
    cancelled = asyncio.Event()
    commits: list[str] = []

    async def business(request: Request) -> JSONResponse:
        operation_done = asyncio.Event()

        async def operation() -> None:
            async with service.business_task():
                started.set()
                try:
                    await asyncio.Event().wait()
                except asyncio.CancelledError:
                    cancelled.set()
                finally:
                    operation_done.set()

        operation_task = asyncio.create_task(operation())
        await operation_done.wait()
        await operation_task
        commits.append("committed")
        return JSONResponse({"ok": True})

    async def status(request: Request) -> JSONResponse:
        return JSONResponse(service.status())

    app = Starlette(
        routes=[
            Route("/api/license/status", status),
            Route("/api/business/commit", business, methods=["POST"]),
        ]
    )
    install(app, service)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://testserver"
    ) as client:
        request = asyncio.create_task(client.post("/api/business/commit"))
        await asyncio.wait_for(started.wait(), timeout=2)
        _revoke(service, signer)
        await service._notify()
        denied, public_status = await asyncio.wait_for(
            asyncio.gather(request, client.get("/api/license/status")), timeout=2
        )

    assert cancelled.is_set()
    assert commits == []
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == ErrorCode.INSTANCE_LICENSE_LOCKED.value
    assert public_status.status_code == 200
    assert public_status.json()["allowed"] is False


@pytest.mark.asyncio
async def test_real_service_callback_closes_websocket_without_business_message(tmp_path) -> None:
    service, signer = _active_service(tmp_path)
    received: list[str] = []
    incoming: asyncio.Queue[dict[str, object]] = asyncio.Queue()
    await incoming.put({"type": "websocket.connect"})
    accepted = asyncio.Event()
    closed = asyncio.Event()
    outbound: list[dict[str, object]] = []

    async def websocket_endpoint(websocket) -> None:
        await websocket.accept()
        async for message in websocket.iter_text():
            received.append(message)

    app = Starlette(routes=[WebSocketRoute("/api/business/ws", websocket_endpoint)])
    install(app, service)

    async def receive() -> dict[str, object]:
        return await incoming.get()

    async def send(message: dict[str, object]) -> None:
        outbound.append(message)
        if message["type"] == "websocket.accept":
            accepted.set()
        elif message["type"] == "websocket.close":
            closed.set()

    scope = {
        "type": "websocket",
        "asgi": {"version": "3.0", "spec_version": "2.3"},
        "http_version": "1.1",
        "scheme": "ws",
        "server": ("testserver", 80),
        "client": ("testclient", 123),
        "root_path": "",
        "path": "/api/business/ws",
        "raw_path": b"/api/business/ws",
        "query_string": b"",
        "headers": [],
        "subprotocols": [],
        "state": {},
    }
    session = asyncio.create_task(app(scope, receive, send))
    await asyncio.wait_for(accepted.wait(), timeout=2)
    _revoke(service, signer)
    await service._notify()
    await asyncio.wait_for(closed.wait(), timeout=2)
    await incoming.put({"type": "websocket.receive", "text": "business command"})
    await asyncio.wait_for(session, timeout=2)

    close = next(message for message in outbound if message["type"] == "websocket.close")
    reason = close["reason"]
    assert close["code"] == 4403
    assert len(reason.encode("utf-8")) <= 123
    assert json.loads(reason)["error"]["code"] == ErrorCode.INSTANCE_LICENSE_LOCKED.value
    assert received == []


def test_locked_http_business_request_returns_typed_403_but_status_is_public() -> None:
    with TestClient(_app(_License(allowed=False))) as client:
        status = client.get("/api/license/status")
        denied = client.get("/api/business")

    assert status.status_code == 200
    assert denied.status_code == 403
    assert denied.json()["error"]["code"] == ErrorCode.INSTANCE_LICENSE_LOCKED.value
    assert denied.json()["error"]["details"]["reason"] == "expired"


def test_unsafe_host_management_is_denied_even_with_active_license() -> None:
    with TestClient(_app(_License(allowed=True))) as client:
        plugin_install = client.post("/api/plugins/install")
        env_read = client.get("/api/envs")
        desktop_status = client.get("/api/desktop/status")
        host_filesystem = client.get("/api/filesystem/dirs")
        ordinary_business = client.get("/api/business")

    assert plugin_install.status_code == 403
    assert plugin_install.json()["error"]["code"] == ErrorCode.INSTANCE_DEPLOYMENT_RESTRICTED.value
    assert env_read.status_code == 403
    assert desktop_status.status_code == 403
    assert host_filesystem.status_code == 403
    assert ordinary_business.status_code == 200


def test_locked_websocket_handshake_closes_with_instance_lock_code() -> None:
    with (
        TestClient(_app(_License(allowed=False))) as client,
        pytest.raises(WebSocketDisconnect) as disconnected,
        client.websocket_connect("/api/business/ws"),
    ):
        pass

    assert disconnected.value.code == 4403
    assert (
        json.loads(disconnected.value.reason)["error"]["code"]
        == ErrorCode.INSTANCE_LICENSE_LOCKED.value
    )


def test_existing_websocket_stops_receiving_business_messages_after_lock() -> None:
    service = _License(allowed=True)
    with (
        TestClient(_app(service)) as client,
        client.websocket_connect("/api/business/ws") as websocket,
    ):
        service.allowed = False
        websocket.send_text("business command")
        with pytest.raises(WebSocketDisconnect) as disconnected:
            websocket.receive_text()

    assert disconnected.value.code == 4403
