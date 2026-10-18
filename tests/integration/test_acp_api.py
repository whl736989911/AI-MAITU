"""Deployment lockdown regressions for host-side ACP configuration."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import Mock

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_managed_acp_configuration_mutations_are_denied_before_registry_access(
    env: Any, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    client, _srv, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="acp-member")
    monkeypatch.setenv("MAITU_LICENSE_TRUSTED_KEYS_FILE", str(tmp_path / "trusted-keys.json"))
    registry_access = Mock(side_effect=AssertionError("restricted ACP handler was reached"))

    from octop.api.routers import acp

    with pytest.MonkeyPatch.context() as patcher:
        patcher.setattr(acp, "_registry", registry_access)
        for auth in (admin_auth, member_auth):
            for method, path, payload in (
                ("PUT", "/api/acp", {"runners": {}}),
                ("PUT", "/api/agents/host-lockdown/acp/tool", {"tool_enabled": True}),
                ("DELETE", "/api/acp/custom-runner", None),
            ):
                response = await client.request(method, path, headers=auth, json=payload)
                assert response.status_code == 403, response.text
                assert response.json()["error"]["code"] == "FORBIDDEN"

    registry_access.assert_not_called()
