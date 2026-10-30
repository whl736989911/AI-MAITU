"""Permission policy for changes to per-agent configuration."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from octop.infra.errors import ErrorCode, OctopError

# Internal workspace layout keys are excluded: AgentManager pins these on writes.
_CONFIG_PERMISSION_BY_KEY: dict[str, str] = {
    "tools_disabled": "tools",
    "plugins": "agent_plugins",
    "skills": "skills",
    "skills_disabled": "skills",
    "skills_dir": "skills",
    "skills_dirs": "skills",
    "skill_package_ids": "skills",
    "subagents": "subagents",
    "heartbeat": "subagents",
    "memory": "memory",
    "memory_backend": "memory",
    "memory_enabled": "memory",
    "memory_extract_on_session_end": "memory",
    "memory_extract_trigger_mode": "memory",
    "memory_extract_idle_seconds": "memory",
    "memory_extract_interval_seconds": "memory",
    "memory_aux_light_model": "memory",
    "memory_aux_heavy_model": "memory",
    "mcp_servers": "connectors",
    "acp": "acp",
    "backend": "terminal",
    "env": "terminal",
    "environment": "terminal",
    "execution": "terminal",
    "shell": "terminal",
    "knowledge_base_ids": "knowledge_bases",
    "web_search_tools": "browser",
    "persona": "mbti",
    "persona_mbti": "mbti",
}


def _permission_for_config_key(key: str) -> str | None:
    if key.startswith("memory_"):
        return "memory"
    if key.startswith("subagents_"):
        return "subagents"
    if key.startswith("acp_"):
        return "acp"
    if key.startswith("skill_dir") or key.startswith("skills_dir"):
        return "skills"
    return _CONFIG_PERMISSION_BY_KEY.get(key)


def _same_json_value(before: object, after: object) -> bool:
    if type(before) is not type(after):
        return False
    if isinstance(before, dict) and isinstance(after, dict):
        return before.keys() == after.keys() and all(
            _same_json_value(value, after[key]) for key, value in before.items()
        )
    if isinstance(before, list) and isinstance(after, list):
        return len(before) == len(after) and all(
            _same_json_value(left, right) for left, right in zip(before, after, strict=True)
        )
    return before == after


def changed_config_permissions(before: Mapping[str, Any], after: Mapping[str, Any]) -> set[str]:
    """Return module permissions required by actual config value/presence changes."""
    required: set[str] = set()
    for key in before.keys() | after.keys():
        if key in before and key in after and _same_json_value(before[key], after[key]):
            continue
        permission = _permission_for_config_key(key)
        if permission is not None:
            required.add(permission)
    return required


def _newly_enabled_tool_permissions(
    before: Mapping[str, Any], after: Mapping[str, Any]
) -> set[str]:
    old_disabled = before.get("tools_disabled")
    new_disabled = after.get("tools_disabled")
    if old_disabled == new_disabled:
        return set()

    from octop.infra.agents.tool_catalog import normalize_tools_disabled

    newly_enabled = set(normalize_tools_disabled(old_disabled)) - set(
        normalize_tools_disabled(new_disabled)
    )
    if not newly_enabled:
        return set()

    from octop.infra.agents.tool_catalog import required_permission_for_tool

    return {
        permission
        for name in newly_enabled
        if (permission := required_permission_for_tool(name)) is not None
    }


def require_config_permissions(
    before: Mapping[str, Any],
    after: Mapping[str, Any],
    permissions: Iterable[str],
    *,
    top_level_changes: Mapping[str, str] | None = None,
) -> None:
    """Reject changed protected configuration absent its effective module grant.

    ``top_level_changes`` maps already-detected changed API fields to their module
    key, allowing config-backed profile fields to use the same check without
    treating unchanged roundtrips as writes.
    """
    effective = set(permissions)
    required = changed_config_permissions(before, after)
    required.update(_newly_enabled_tool_permissions(before, after))
    if top_level_changes:
        required.update(top_level_changes.values())
    missing = sorted(required - effective)
    if missing:
        key = missing[0]
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "permission required",
            details={"permission": key},
        )


__all__ = ["changed_config_permissions", "require_config_permissions"]
