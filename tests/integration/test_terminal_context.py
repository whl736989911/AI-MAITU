"""Deployment lockdown regression for host terminal context."""

from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
async def test_terminal_context_is_denied_before_host_inspection(env: Any) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(client, admin_auth, username="terminal-member")

    with patch("octop.api.routers.terminal.platform.system") as host_probe:
        for auth in (admin_auth, member_auth):
            response = await client.get(
                "/api/agents/agent-not-created/terminal/context", headers=auth
            )
            assert response.status_code == 403, response.text
            assert response.json()["error"]["code"] == "INSTANCE_DEPLOYMENT_RESTRICTED"

    host_probe.assert_not_called()
