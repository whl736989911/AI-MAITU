"""Wikipedia language codes must not turn a fixed endpoint into arbitrary URLs."""

from __future__ import annotations

import importlib.util
import json
from typing import Any

from octop.infra.agents.plugins.bundled import default_bundled_plugins_root


def _wiki_module() -> Any:
    source = default_bundled_plugins_root() / "wiki-summary" / "main.py"
    spec = importlib.util.spec_from_file_location("bundled_wiki_summary", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


async def test_language_cannot_inject_an_outbound_host(monkeypatch: Any) -> None:
    module = _wiki_module()
    attempts: list[str] = []

    def unexpected_client(*_args: Any, **_kwargs: Any) -> None:
        attempts.append("outbound")
        raise RuntimeError("network must not be contacted")

    monkeypatch.setattr(module.httpx, "AsyncClient", unexpected_client)
    response = json.loads(await module.wiki_summary("entry", lang="metadata.internal/path"))
    assert attempts == []
    assert response["data"]["error"]
