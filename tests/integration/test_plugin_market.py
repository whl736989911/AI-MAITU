"""Integration tests for the shipped-plugin market (Admin → Plugins → Market)."""

from __future__ import annotations

from typing import Any

# No pip requirements: keeps the install path offline and fast.
_MARKET_ID = "fortune"


async def test_market_lists_shipped_plugins(env: Any) -> None:
    client, _srv, auth = env
    r = await client.get("/api/plugins/market", headers=auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    ids = [item["id"] for item in items]
    assert _MARKET_ID in ids
    card = next(item for item in items if item["id"] == _MARKET_ID)
    assert card["name"]["zh"] and card["name"]["en"]
    assert card["description"]["zh"] and card["description"]["en"]
    assert card["kind"] == "tool"
    assert card["version"]
    # Seeding copies shipped plugins into the user dir globally disabled.
    assert card["installed"] is True
    assert card["enabled"] is False


async def test_market_search_filters_by_name(env: Any) -> None:
    client, _srv, auth = env
    r = await client.get("/api/plugins/market", params={"q": "fortune"}, headers=auth)
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert [item["id"] for item in items] == [_MARKET_ID]


async def test_market_install_enables_shipped_plugin(env: Any) -> None:
    client, srv, auth = env
    r = await client.post(f"/api/plugins/market/{_MARKET_ID}/install", headers=auth)
    assert r.status_code == 201, r.text
    assert r.json()["installed"] is True
    assert r.json()["enabled"] is True

    # On disk, enabled, loaded, and visible to the installed tab.
    assert (srv.plugin_manager.plugins_dir / _MARKET_ID / "plugin.yaml").is_file()
    listing = (await client.get("/api/plugins", headers=auth)).json()
    row = next(item for item in listing if item["id"] == _MARKET_ID)
    assert row["enabled"] is True
    assert row["loaded"] is True

    # Detail reports the tools the plugin registered.
    detail = (await client.get(f"/api/plugins/market/{_MARKET_ID}", headers=auth)).json()
    assert detail["installed"] is True
    assert [tool["name"] for tool in detail["tools"]]


async def test_market_install_restores_uninstalled_plugin(env: Any) -> None:
    """Seeding never re-copies an uninstalled id — the market is the way back."""
    client, srv, auth = env
    await client.post(f"/api/plugins/market/{_MARKET_ID}/install", headers=auth)
    r = await client.delete(f"/api/plugins/{_MARKET_ID}", headers=auth)
    assert r.status_code == 200, r.text
    assert not (srv.plugin_manager.plugins_dir / _MARKET_ID).exists()
    gone = (await client.get("/api/plugins/market", headers=auth)).json()["items"]
    assert next(item for item in gone if item["id"] == _MARKET_ID)["installed"] is False

    again = await client.post(f"/api/plugins/market/{_MARKET_ID}/install", headers=auth)
    assert again.status_code == 201, again.text
    assert again.json()["installed"] is True
    assert (srv.plugin_manager.plugins_dir / _MARKET_ID / "plugin.yaml").is_file()


async def test_market_rejects_unknown_plugin(env: Any) -> None:
    client, _srv, auth = env
    r = await client.post("/api/plugins/market/not-a-shipped-plugin/install", headers=auth)
    assert r.status_code == 404, r.text
    assert (
        await client.get("/api/plugins/market/not-a-shipped-plugin", headers=auth)
    ).status_code == 404


async def test_market_requires_plugins_permission(env_admin_alice: Any) -> None:
    client, _srv, _admin_auth, alice_auth = env_admin_alice
    assert (await client.get("/api/plugins/market", headers=alice_auth)).status_code == 403
    r = await client.post(
        f"/api/plugins/market/{_MARKET_ID}/install",
        headers=alice_auth,
    )
    assert r.status_code == 403, r.text
