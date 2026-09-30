"""Memory maintenance slash commands require an authenticated dashboard/CLI owner."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from octop.infra.gateway.slash import build_default_dispatcher
from octop.infra.gateway.slash.ctx import SlashCtx
from octop.infra.gateway.slash.runner import try_handle_slash


@pytest.fixture
def ctx():
    coordinator = MagicMock()
    coordinator.start_chat.return_value = 1
    coordinator.preview_chat.return_value = [{"agent_id": "mine", "name": "Mine"}]
    return SlashCtx(
        agent_id="mine",
        user_id=1,
        channel_type="dashboard",
        session_key="key",
        thread_registry=MagicMock(),
        user_repo=SimpleNamespace(get=lambda _: SimpleNamespace(disabled=0, locale="en")),
        agent_manager=SimpleNamespace(memory_slim=coordinator),
    )


@pytest.mark.asyncio
async def test_confirmed_slash_starts_owned_maintenance(ctx):
    handled, lines, actions = await try_handle_slash(
        "/memory slim --confirm", dispatcher=build_default_dispatcher(), ctx=ctx
    )
    assert handled and not actions
    assert any("/memory status" in line for line in lines)
    assert ctx.agent_manager.memory_slim.start_chat.call_count == 1


@pytest.mark.asyncio
async def test_external_im_identity_cannot_authorize_memory_maintenance(ctx):
    ctx.channel_type = "feishu"
    handled, _, _ = await try_handle_slash(
        "/memory slim --confirm", dispatcher=build_default_dispatcher(), ctx=ctx
    )
    assert handled
    ctx.agent_manager.memory_slim.start_chat.assert_not_called()


@pytest.mark.asyncio
async def test_confirmation_preview_does_not_schedule_job(ctx):
    handled, lines, _ = await try_handle_slash(
        "/memory slim", dispatcher=build_default_dispatcher(), ctx=ctx
    )
    assert handled
    assert any("Mine" in line for line in lines)
    assert any("/memory slim --confirm" in line for line in lines)
    ctx.agent_manager.memory_slim.start_chat.assert_not_called()
