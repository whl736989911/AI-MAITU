"""Discord configuration forwarding and gateway diagnostics."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import discord
import pytest
from harness_gateway.channels.discord import DiscordChannel, DiscordConfig
from tests.unit.gateway.test_gateway_runtime_status import _make_gateway

from octop.infra.gateway.gateway import Gateway


@pytest.mark.asyncio
async def test_discord_allow_all_and_allowlist_scope_messages_by_channel_and_user() -> None:
    def message(
        message_id: str,
        *,
        channel_id: str,
        guild: bool,
        author_id: str = "10",
        parent_id: str | None = None,
    ) -> SimpleNamespace:
        return SimpleNamespace(
            id=message_id,
            author=SimpleNamespace(
                id=author_id,
                bot=False,
                display_name=f"user-{author_id}",
            ),
            webhook_id=None,
            type=discord.MessageType.default,
            channel=SimpleNamespace(id=channel_id, parent_id=parent_id, name="test"),
            guild=SimpleNamespace(id="guild") if guild else None,
            content="hello",
            attachments=[],
            created_at=datetime.now(),
        )

    processor = MagicMock()
    unrestricted = DiscordChannel(
        processor,
        config=DiscordConfig.from_dict({"bot_token": "fake-token"}),
        channel_id="discord-all",
    )
    received: list[object] = []
    unrestricted.enqueue = received.append
    await unrestricted.on_message(message("1", channel_id="elsewhere", guild=True))
    assert len(received) == 1

    restricted = DiscordChannel(
        processor,
        config=DiscordConfig.from_dict(
            {
                "bot_token": "fake-token",
                "allow_all_channels": False,
                "allowed_channel_ids": ["200"],
                "allowed_user_ids": ["10"],
            }
        ),
        channel_id="discord-restricted",
    )
    received.clear()
    restricted.enqueue = received.append
    await restricted.on_message(message("2", channel_id="200", guild=True))
    await restricted.on_message(message("3", channel_id="thread", parent_id="200", guild=True))
    await restricted.on_message(message("4", channel_id="201", guild=True))
    await restricted.on_message(message("5", channel_id="10", guild=False, author_id="10"))
    await restricted.on_message(message("6", channel_id="10", guild=False, author_id="11"))
    assert [event.metadata["message_id"] for event in received] == ["2", "3", "5"]


def test_discord_config_defaults_to_all_guild_channels_and_supports_restrictions() -> None:
    default = DiscordConfig.from_dict({"bot_token": "fake-token"})
    restricted = DiscordConfig.from_dict(
        {
            "bot_token": "fake-token",
            "http_proxy": "http://proxy.example:8080",
            "http_proxy_auth": "user:secret",
            "allow_all_channels": False,
            "allowed_channel_ids": ["200", "300"],
            "allowed_user_ids": ["10"],
        }
    )

    assert default.allow_all_channels is True
    assert default.allowed_channel_ids == []
    assert restricted.allow_all_channels is False
    assert restricted.allowed_channel_ids == ["200", "300"]
    assert restricted.allowed_user_ids == ["10"]
    assert restricted.http_proxy == "http://proxy.example:8080"
    assert restricted.http_proxy_auth == "user:secret"


def test_discord_runtime_status_tracks_live_connection_and_localizes_errors(
    tmp_path: Path,
) -> None:
    gateway = _make_gateway(tmp_path)
    channel = SimpleNamespace(is_connected=True, runtime_error=None)
    gateway._channel_manager = SimpleNamespace(get_channel=lambda _: channel)
    gateway._set_runtime_status("discord1", connected=True)

    channel.is_connected = False
    channel.runtime_error = "discord_disconnected"
    status = gateway.runtime_status_to_dict("discord1", locale="zh")
    assert status is not None and status["connected"] is False
    assert "重连" in status["error"]

    channel.is_connected = True
    channel.runtime_error = None
    status = gateway.runtime_status_to_dict("discord1", locale="zh")
    assert status is not None and status["connected"] is True
    assert status["error"] is None


def test_discord_setup_diagnostics_are_localized() -> None:
    assert "Bot Token" in Gateway._format_probe_error(RuntimeError("discord_invalid_token"), "en")
    assert "Message Content Intent" in Gateway._format_probe_error(
        RuntimeError("discord_intents_required"), "en"
    )
