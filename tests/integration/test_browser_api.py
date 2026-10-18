"""tests/integration/test_browser_api.py — remote browser endpoints.

We deliberately don't spawn a real Chrome in the test suite. These
cases drive env-status, ``_probe_env``, install SSE, harness-sessions,
and shutdown gates. Live screencast is covered by dashboard + WS unit
tests.
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.support.auth import create_user

# --- env-status -------------------------------------------------------------


def test_probe_env_when_playwright_missing() -> None:
    """``_probe_env`` should not raise even with playwright import broken."""
    import builtins

    from octop.api.routers.browser import env as br

    original_import = builtins.__import__

    def fake_import(name: str, *args: Any, **kw: Any) -> Any:
        if name == "playwright":
            raise ImportError("simulated")
        return original_import(name, *args, **kw)

    with patch("builtins.__import__", side_effect=fake_import):
        out = br._probe_env()
    assert out["playwright"] is False
    if not out.get("harness_browser"):
        assert out["browsers_ok"] is False
        assert out["error"]


def test_probe_env_harness_browser_without_chromium() -> None:
    """``browsers_ok`` must stay false when no Chrome/Chromium is available."""
    from octop.api.routers.browser import env as br

    try:
        import harness_browser  # noqa: F401
    except ImportError:
        return

    with patch(
        "harness_browser.cdp.launcher.find_chrome",
        return_value=None,
    ):
        out = br._probe_env()
    assert out["harness_browser"] is True
    assert out["browsers_ok"] is False
    assert out["error"]


def test_probe_env_accepts_system_chrome() -> None:
    """System Chrome via find_chrome is enough for browsers_ok (same as launch)."""
    from octop.api.routers.browser import env as br

    try:
        import harness_browser  # noqa: F401
    except ImportError:
        return

    with (
        patch(
            "harness_browser.cdp.launcher.find_chrome",
            return_value="/usr/bin/google-chrome",
        ),
        patch(
            "octop.infra.browser.setup.playwright_chromium_installed",
            return_value=False,
        ),
        patch(
            "octop.infra.browser.setup.chrome_source_for_path",
            return_value="system",
        ),
    ):
        out = br._probe_env()
    assert out["harness_browser"] is True
    assert out["browsers_ok"] is True
    assert out["chrome_path"] == "/usr/bin/google-chrome"
    assert out["chrome_source"] == "system"
    assert out["playwright_chromium"] is False


def test_verify_browser_binary_ok(tmp_path: Path) -> None:
    from octop.api.routers.browser import env as br

    if os.name == "nt":
        # Windows cannot exec a shebang script; use a .bat that ignores args.
        exe = tmp_path / "fake-chrome.bat"
        exe.write_text("@echo Chrome 1.0\r\n", encoding="utf-8")
    else:
        exe = tmp_path / "fake-chrome"
        exe.write_text("#!/bin/sh\necho 'Chrome 1.0'\n", encoding="utf-8")
        exe.chmod(0o755)
    ok, msg = br._verify_browser_binary(str(exe))
    assert ok is True
    assert "Chrome" in msg


async def test_harness_list_tabs_uses_sticky_target_id(monkeypatch) -> None:
    import aiohttp

    from octop.api.routers.browser import harness as harness_router

    class FakeProfile:
        cdp_port = 9222

        def load_target(self) -> str:
            return "TAB-2"

    class FakeInternal:
        _cfg = SimpleNamespace(cdp_host="localhost")
        _profile = FakeProfile()

    class FakeSession:
        _internal = FakeInternal()

    class FakeResponse:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        async def json(self, content_type=None):
            return [
                {
                    "type": "page",
                    "id": "TAB-1",
                    "url": "https://www.bilibili.com/",
                    "title": "B站",
                },
                {
                    "type": "page",
                    "id": "TAB-2",
                    "url": "https://weibo.com/",
                    "title": "微博",
                },
            ]

    class FakeClientSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *exc):
            return False

        def get(self, _url: str, timeout=None):
            return FakeResponse()

    monkeypatch.setattr(aiohttp, "ClientSession", lambda: FakeClientSession())

    tabs = await harness_router.harness_list_tabs(FakeSession())

    assert [tab["active"] for tab in tabs] == [False, True]


async def test_managed_browser_routes_are_denied_before_probe_install_or_shutdown(
    env: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    c, _srv, admin_auth = env
    member_auth = await create_user(c, admin_auth, username="browser-member")
    from octop.api.routers.browser import env as browser_env
    from octop.api.routers.browser import harness as browser_harness

    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))

    with (
        patch.object(browser_env, "_probe_env") as probe,
        patch.object(browser_env, "_verify_browser_binary") as verify,
        patch.object(browser_harness, "user_browser_profile") as profile,
        patch("harness_browser.tool_interface.browser_tool", new=AsyncMock()) as browser_tool,
        patch("harness_browser.cdp.launcher.find_chrome", return_value="/fake/chrome"),
    ):
        verify.return_value = (True, "Chrome")
        for auth in (admin_auth, member_auth):
            for method, path in (
                ("GET", "/api/browser/env-status"),
                ("GET", "/api/browser/harness-sessions"),
                ("POST", "/api/browser/install"),
                ("POST", "/api/browser/shutdown"),
            ):
                response = await c.request(method, path, headers=auth)
                assert response.status_code == 403, response.text
                assert response.json()["error"]["code"] == "FORBIDDEN"

    probe.assert_not_called()
    verify.assert_not_called()
    profile.assert_not_called()
    browser_tool.assert_not_awaited()
