"""A publisher must never reuse another official account's cached token."""

from __future__ import annotations

import importlib.util
import json
import time
from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.infra.agents.experts.catalog import default_library_root


@pytest.mark.parametrize("cached_app_id", [None, "other-account", "current-account"])
def test_token_cache_is_scoped_to_app_id(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, cached_app_id: str | None
) -> None:
    script = (
        default_library_root()
        / "wechat-ops"
        / "skills"
        / "publisher-multi-platform"
        / "scripts"
        / "wechat_publish.py"
    )
    spec = importlib.util.spec_from_file_location("wechat_publish_cache_regression", script)
    assert spec is not None and spec.loader is not None
    publisher = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(publisher)
    cache_file = tmp_path / "token.json"
    cache_file.write_text(
        json.dumps(
            {
                "app_id": cached_app_id,
                "access_token": "cached-token",
                "expires_at": time.time() + 7200,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(publisher, "TOKEN_CACHE_FILE", str(cache_file))
    monkeypatch.setenv("WECHAT_APP_ID", "current-account")
    monkeypatch.setenv("WECHAT_APP_SECRET", "test-secret")
    requests: list[dict[str, str]] = []

    def fetch_token(url: str, *, params: dict[str, str], **kwargs: Any) -> httpx.Response:
        requests.append(params)
        return httpx.Response(200, json={"access_token": "current-token", "expires_in": 7200})

    monkeypatch.setattr(publisher.httpx, "get", fetch_token)
    token = publisher.get_wechat_access_token()
    if cached_app_id == "current-account":
        assert token == "cached-token"
        assert requests == []
    else:
        assert token == "current-token"
        assert requests[0]["appid"] == "current-account"
        saved = json.loads(cache_file.read_text(encoding="utf-8"))
        assert saved["app_id"] == "current-account"
        assert saved["access_token"] == "current-token"
