"""Integration tests for uploading a plugin ZIP from the dashboard."""

from __future__ import annotations

import io
import zipfile
from pathlib import Path
from typing import Any

from tests.support.auth import create_user

_FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "plugins" / "echo-tool"


def _echo_zip() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        for path in _FIXTURE.rglob("*"):
            if path.is_file():
                zf.write(path, arcname=f"echo-tool/{path.relative_to(_FIXTURE).as_posix()}")
    return buf.getvalue()


def _zip_files() -> dict[str, tuple[str, bytes, str]]:
    return {"file": ("echo-tool.zip", _echo_zip(), "application/zip")}


async def test_upload_plugin_zip_installs(env: Any) -> None:
    client, _srv, auth = env
    r = await client.post("/api/plugins/upload", files=_zip_files(), headers=auth)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["id"] == "echo-tool"
    assert body["kind"] == "tool"

    listing = (await client.get("/api/plugins", headers=auth)).json()
    assert any(p.get("id") == "echo-tool" for p in listing)


async def test_upload_plugin_overwrite_behavior(env: Any) -> None:
    client, _srv, auth = env
    # First install succeeds.
    r1 = await client.post(
        "/api/plugins/upload",
        files=_zip_files(),
        data={"force": "true"},
        headers=auth,
    )
    assert r1.status_code == 200, r1.text

    # Same id without force → conflict.
    r2 = await client.post("/api/plugins/upload", files=_zip_files(), headers=auth)
    assert r2.status_code == 409, r2.text

    # Same id with force → overwrites.
    r3 = await client.post(
        "/api/plugins/upload",
        files=_zip_files(),
        data={"force": "true"},
        headers=auth,
    )
    assert r3.status_code == 200, r3.text


async def test_upload_plugin_rejects_non_zip(env: Any) -> None:
    client, _srv, auth = env
    files = {"file": ("not-a-plugin.zip", b"<!DOCTYPE html>blob page", "application/zip")}
    r = await client.post("/api/plugins/upload", files=files, headers=auth)
    assert r.status_code == 400, r.text


async def test_upload_plugin_requires_the_plugins_key(env_admin_alice: Any) -> None:
    client, _srv, _admin_auth, alice_auth = env_admin_alice
    r = await client.post("/api/plugins/upload", files=_zip_files(), headers=alice_auth)
    assert r.status_code == 403, r.text


async def test_list_plugins_follows_the_plugins_key(env: Any) -> None:
    """The listing is the plugins module itself, in both directions (design §4.4).

    It used to be open to any signed-in account while only the mutations were
    gated; the module key covers the whole surface now, so the list is refused
    without it — and served, unchanged, to a caller an administrator granted it.
    """
    client, _srv, admin_auth = env
    without_key = await create_user(client, admin_auth, username="plugin_reader")
    refused = await client.get("/api/plugins", headers=without_key)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["details"]["permission"] == "plugins"

    with_key = await create_user(
        client, admin_auth, username="plugin_operator", permissions=["plugins"]
    )
    allowed = await client.get("/api/plugins", headers=with_key)
    assert allowed.status_code == 200, allowed.text
    assert isinstance(allowed.json(), list)
