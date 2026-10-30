"""Authorization and transport coverage for unified text assistance."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from tests.support.auth import create_user


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "permission"),
    [
        ("expert.description", "experts"),
        ("team.description", "teams"),
        ("skill_package.description", "skill_packages"),
        ("skill.description", "skills"),
        ("knowledge_base.description", "knowledge_bases"),
        ("feature.description", "features"),
    ],
)
async def test_form_text_assist_requires_surface_permission(
    env: Any, kind: str, permission: str
) -> None:
    client, _server, admin_auth = env
    member_auth = await create_user(
        client,
        admin_auth,
        username=f"text-assist-{kind.replace('.', '-')}",
        permissions=[],
    )
    with patch(
        "octop.api.routers.text_assist.run_text_assist",
        new=AsyncMock(return_value="must not run"),
    ) as assist:
        response = await client.post(
            "/api/text/assist",
            headers=member_auth,
            json={"kind": kind, "context": {"name": "Example"}},
        )
    assert response.status_code == 403, response.text
    assert response.json()["error"]["details"]["permission"] == permission
    assist.assert_not_awaited()


@pytest.mark.asyncio
async def test_chat_prompt_assist_requires_agent_scope(env: Any) -> None:
    client, _server, admin_auth = env
    response = await client.post(
        "/api/text/assist",
        headers=admin_auth,
        json={"kind": "chat.prompt", "text": "Draft prompt"},
    )
    assert response.status_code == 400, response.text
