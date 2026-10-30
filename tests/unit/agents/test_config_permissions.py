"""Behavior tests for per-agent config permission classification."""

from __future__ import annotations

import pytest

from octop.infra.agents.config_permissions import (
    changed_config_permissions,
    require_config_permissions,
)
from octop.infra.errors import ErrorCode, OctopError


def test_sensitive_config_changes_include_removals_and_profile_lists() -> None:
    before = {
        "tools_disabled": ["execute"],
        "plugins": {"sample": {"enabled": False}},
        "skills_disabled": ["draft"],
        "subagents": {"enabled": True},
        "memory": {"enabled": True},
        "mcp_servers": ["calendar"],
        "acp": {"tool_enabled": True},
        "backend": {"type": "filesystem"},
        "knowledge_base_ids": ["kb-1"],
    }
    after = {"plugins": before["plugins"], "memory": before["memory"]}

    assert changed_config_permissions(before, after) == {
        "tools",
        "skills",
        "subagents",
        "connectors",
        "acp",
        "terminal",
        "knowledge_bases",
    }


def test_roundtrip_and_unrelated_chat_fields_need_no_module_grant() -> None:
    config = {"tools_disabled": ["execute"], "default_model": "provider:model"}
    require_config_permissions(config, dict(config), set())
    require_config_permissions(
        {"default_model": "provider:model"},
        {"default_model": "provider:other"},
        set(),
    )
    require_config_permissions(
        {"workspace_dir": "/managed", "system_files_path": ".maitu"},
        {},
        set(),
    )


def test_permission_is_required_for_sensitive_addition_and_removal() -> None:
    cases = (
        ({}, {"tools_disabled": ["execute"]}, "tools"),
        ({"tools_disabled": ["execute"]}, {}, "terminal"),
    )
    for before, after, required_permission in cases:
        with pytest.raises(OctopError) as exc:
            require_config_permissions(before, after, set())
        assert exc.value.code is ErrorCode.FORBIDDEN
        assert exc.value.details["permission"] == required_permission


def test_harness_aliases_and_persona_config_require_their_modules() -> None:
    before = {}
    after = {
        "memory_enabled": False,
        "memory_aux_light_model": "provider:model",
        "subagents_enabled": False,
        "acp_tool_enabled": True,
        "skills_dir": ["skills"],
        "persona": "INTJ",
    }
    assert changed_config_permissions(before, after) == {
        "memory",
        "subagents",
        "acp",
        "skills",
        "mbti",
    }


def test_explicitly_granted_module_allows_sensitive_change() -> None:
    require_config_permissions({}, {"mcp_servers": ["calendar"]}, {"connectors"})


@pytest.mark.parametrize(
    ("before", "after", "permission"),
    [
        ({"web_search_tools": False}, {"web_search_tools": 0}, "browser"),
        (
            {"plugins": {"sample": {"enabled": False}}},
            {"plugins": {"sample": {"enabled": 0}}},
            "agent_plugins",
        ),
        ({"skills_disabled": ["draft", False]}, {"skills_disabled": ["draft", 0]}, "skills"),
    ],
)
def test_boolean_number_substitution_is_not_an_unchanged_config(
    before: dict, after: dict, permission: str
) -> None:
    with pytest.raises(OctopError) as exc:
        require_config_permissions(before, after, set())
    assert exc.value.code is ErrorCode.FORBIDDEN
    assert exc.value.details["permission"] == permission
