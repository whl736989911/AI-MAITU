"""ACP runner configuration API.

Runners are stored globally per user (``settings`` table). Each agent only
stores ``acp.tool_enabled`` for the ``acp_runner`` built-in tool.

Two questions, two gates (design §4.4)
--------------------------------------
A runner *definition* is a **host command line** — ``command`` + ``args`` +
``env`` for a program Octop spawns on this machine, kept per account and shared
by every agent of it. So the surface answers to two different levels:

* ``acp`` — may this account use the ACP surface: read the runner definitions,
  and enable ``acp_runner`` on an agent it owns. The key replaces the fixed
  administrator check the global entry used to carry.
* ``acp`` **and** the system administrator role — may it write a runner
  *definition*. Naming the program this host executes is a system-level
  operation, which is exactly what design §4.4 keeps the extra check for
  (涉及系统级危险操作的接口仍可额外要求系统管理员), the same reason
  ``filesystem.py`` (host paths) and the sharing approval queue stayed on
  ``require_admin()``.

Per route::

  GET    /api/acp                               acp
  PUT    /api/acp                               acp + system administrator
  GET    /api/acp/{runner_name}                 acp
  PUT    /api/acp/{runner_name}                 acp + system administrator
  DELETE /api/acp/{runner_name}                 acp + system administrator
  GET    /api/agents/{agent_id}/acp             acp (+ agent ownership)
  PUT    /api/agents/{agent_id}/acp             acp (+ ownership); its optional
                                                ``runners`` leg is a definition
                                                write → system administrator
  PUT    /api/agents/{agent_id}/acp/tool        acp (+ agent ownership)
  GET    /api/agents/{agent_id}/acp/{runner}    acp (+ agent ownership)
  PUT    /api/agents/{agent_id}/acp/{runner}    acp + system administrator
  DELETE /api/agents/{agent_id}/acp/{runner}    acp + system administrator

Deleting a *built-in* runner stays refused for everyone (``_BUILTIN_RUNNERS``);
so does the agent-ownership check, which a system administrator bypasses
(``api/common/agent.py``).
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Body, Depends
from pydantic import BaseModel, Field

from octop.api.common.agent import assert_agent_owner as _assert_agent_owner
from octop.api.deps import get_server, require_admin, require_permission
from octop.infra.errors import ErrorCode, OctopError

logger = logging.getLogger(__name__)

router = APIRouter()

_BUILTIN_RUNNERS = frozenset(
    {"opencode", "codebuddy", "claude_code", "codex", "kimi_code", "cursor_cli", "pi"}
)
_ALLOWED_PARSE_MODES = frozenset({"call_title", "update_detail", "call_detail"})
_DEFAULT_STDIO_BUFFER = 50 * 1024 * 1024


class ACPRunnerBody(BaseModel):
    enabled: bool = False
    command: str = ""
    args: list[str] = Field(default_factory=list)
    env: dict[str, str] = Field(default_factory=dict)
    trusted: bool = False
    tool_parse_mode: str = "update_detail"
    stdio_buffer_limit_bytes: int = _DEFAULT_STDIO_BUFFER


class ACPRunnersBody(BaseModel):
    runners: dict[str, ACPRunnerBody] = Field(default_factory=dict)


class ACPConfigBody(BaseModel):
    tool_enabled: bool = Field(
        default=False,
        description="Register the acp_runner built-in tool on this agent",
    )
    runners: dict[str, ACPRunnerBody] | None = Field(
        default=None,
        description="Optional; updates the user's global runner definitions",
    )


class ACPAgentToolBody(BaseModel):
    tool_enabled: bool = Field(
        default=False,
        description="Register the acp_runner built-in tool on this agent",
    )


def _registry(server: Any) -> Any:
    assert server.app_runtime is not None
    return server.app_runtime.agent_registry


def _validate_runner(name: str, runner: ACPRunnerBody) -> None:
    if runner.tool_parse_mode not in _ALLOWED_PARSE_MODES:
        raise OctopError(
            ErrorCode.SLASH_BAD_ARGS,
            f"runner {name!r}: tool_parse_mode must be one of {sorted(_ALLOWED_PARSE_MODES)}",
        )


def _runners_payload(body: ACPRunnersBody) -> dict[str, dict[str, Any]]:
    runners: dict[str, dict[str, Any]] = {}
    for name, runner in body.runners.items():
        key = name.strip()
        if not key:
            raise OctopError(ErrorCode.SLASH_BAD_ARGS, "runner name cannot be empty")
        _validate_runner(key, runner)
        runners[key] = runner.model_dump()
    return runners


def _read_agent_tool_enabled(cfg: dict[str, Any]) -> bool:
    acp = cfg.get("acp")
    if not isinstance(acp, dict):
        return False
    return bool(acp.get("tool_enabled", False))


def _combined_acp_view(*, registry: Any, user_id: int, agent_cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "tool_enabled": _read_agent_tool_enabled(agent_cfg),
        "runners": registry.acp_settings.load_runners(user_id),
    }


async def _persist_agent_tool_enabled(
    server: Any,
    *,
    agent_id: str,
    tool_enabled: bool,
) -> None:
    registry = _registry(server)
    if tool_enabled:
        row = registry.get_row(agent_id)
        if row is None:
            raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
        backend = registry._backend_spec_for_row(row)
        workspace_dir = registry.resolve_workspace_dir(agent_id)
        if registry._backend_blocks_acp_outbound(backend, workspace_dir=workspace_dir):
            raise OctopError(
                ErrorCode.ACP_BACKEND_UNSUPPORTED,
                "outbound acp_runner is unavailable under a directory sandbox",
            )
    cfg = registry.get_config(agent_id)
    acp = cfg.get("acp")
    if not isinstance(acp, dict):
        acp = {}
    slim = {k: v for k, v in acp.items() if k != "runners"}
    if tool_enabled:
        slim["tool_enabled"] = True
    else:
        slim.pop("tool_enabled", None)
    if slim:
        cfg["acp"] = slim
    else:
        cfg.pop("acp", None)
    await registry.update_config_json(agent_id, json.dumps(cfg, ensure_ascii=False))


def _schedule_reload_user_agents(server: Any, user_id: int) -> None:
    import asyncio

    registry = _registry(server)

    async def _reload() -> None:
        for row in registry.list_agents(user_id):
            try:
                await registry.reload(row.agent_id)
            except Exception:
                logger.exception("ACP reload failed for agent %s", row.agent_id)

    asyncio.get_running_loop().create_task(_reload())


def _agent_row(server: Any, agent_id: str, user: Any) -> Any:
    registry = _registry(server)
    row = registry.get_row(agent_id)
    if row is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
    _assert_agent_owner(row, user)
    return row


def _assert_may_define_runners(user: Any) -> None:
    """Refuse writing a runner *definition* without the system administrator role.

    The one route that can carry a definition write without being a definition
    route of its own — ``PUT /agents/{agent_id}/acp`` takes the same ``runners``
    payload inline — checks it here; everywhere else the ``require_admin()``
    dependency is the check, so the refusal lands before any work.
    """
    if not getattr(user, "is_admin", False):
        raise OctopError(
            ErrorCode.FORBIDDEN,
            "admin required to define ACP runner commands",
            details={"reason": "a runner definition is a command this host executes"},
        )


@router.get("/acp", summary="Get global ACP runners")
async def get_global_acp_runners(
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return ACP runner definitions shared by all agents for the current user."""
    registry = _registry(server)
    return {"runners": registry.acp_settings.load_runners(user.id)}


@router.put("/acp", summary="Update global ACP runners")
async def put_global_acp_runners(
    body: ACPRunnersBody,
    user: Any = Depends(require_permission("acp")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Replace the current user's global ACP runner configuration.

    A definition write: names the commands this host will execute, so it carries
    the system administrator check on top of ``acp`` (see the module docstring).
    """
    registry = _registry(server)
    runners = _runners_payload(body)
    saved = registry.acp_settings.save_runners(user.id, runners)
    _schedule_reload_user_agents(server, user.id)
    return {"runners": saved}


@router.get("/acp/{runner_name}", summary="Get global ACP runner")
async def get_global_acp_runner(
    runner_name: str,
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    registry = _registry(server)
    runner = registry.acp_settings.load_runners(user.id).get(runner_name)
    if runner is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"ACP runner {runner_name!r} not found")
    return dict(runner)


@router.put("/acp/{runner_name}", summary="Update global ACP runner")
async def put_global_acp_runner(
    runner_name: str,
    body: ACPRunnerBody = Body(...),
    user: Any = Depends(require_permission("acp")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    key = runner_name.strip()
    if not key:
        raise OctopError(ErrorCode.SLASH_BAD_ARGS, "runner name cannot be empty")
    _validate_runner(key, body)
    registry = _registry(server)
    runners = registry.acp_settings.load_runners(user.id)
    runners[key] = body.model_dump()
    registry.acp_settings.save_runners(user.id, runners)
    _schedule_reload_user_agents(server, user.id)
    return dict(runners[key])


@router.delete("/acp/{runner_name}", status_code=204, summary="Delete global ACP runner")
async def delete_global_acp_runner(
    runner_name: str,
    user: Any = Depends(require_permission("acp")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> None:
    if runner_name in _BUILTIN_RUNNERS:
        raise OctopError(ErrorCode.FORBIDDEN, f"built-in runner {runner_name!r} cannot be deleted")
    registry = _registry(server)
    runners = registry.acp_settings.load_runners(user.id)
    if runner_name not in runners:
        raise OctopError(ErrorCode.NOT_FOUND, f"ACP runner {runner_name!r} not found")
    del runners[runner_name]
    registry.acp_settings.save_runners(user.id, runners)
    _schedule_reload_user_agents(server, user.id)


@router.get("/agents/{agent_id}/acp", summary="Get ACP config")
async def get_acp_config(
    agent_id: str,
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return global runners plus per-agent ``acp_runner`` tool toggle."""
    _agent_row(server, agent_id, user)
    registry = _registry(server)
    cfg = registry.get_config(agent_id)
    return _combined_acp_view(registry=registry, user_id=user.id, agent_cfg=cfg)


@router.put("/agents/{agent_id}/acp", summary="Update ACP config")
async def put_acp_config(
    agent_id: str,
    body: ACPConfigBody,
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Update per-agent tool toggle; optional ``runners`` updates global definitions.

    Toggling is the ``acp`` key plus ownership. The optional ``runners`` field is
    a *definition* write like ``PUT /acp``, so it checks the system administrator
    role itself (``_assert_may_define_runners``) — this route cannot take the
    ``require_admin()`` dependency, because the toggle it also carries is not a
    system-level operation.
    """
    _agent_row(server, agent_id, user)
    registry = _registry(server)
    if body.runners is not None:
        _assert_may_define_runners(user)
        runners = _runners_payload(ACPRunnersBody(runners=body.runners))
        registry.acp_settings.save_runners(user.id, runners)
        _schedule_reload_user_agents(server, user.id)
    await _persist_agent_tool_enabled(server, agent_id=agent_id, tool_enabled=body.tool_enabled)
    cfg = registry.get_config(agent_id)
    return _combined_acp_view(registry=registry, user_id=user.id, agent_cfg=cfg)


@router.put("/agents/{agent_id}/acp/tool", summary="Update acp_runner tool toggle")
async def put_acp_tool_toggle(
    agent_id: str,
    body: ACPAgentToolBody,
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Enable or disable the ``acp_runner`` tool for one agent only."""
    _agent_row(server, agent_id, user)
    await _persist_agent_tool_enabled(server, agent_id=agent_id, tool_enabled=body.tool_enabled)
    registry = _registry(server)
    cfg = registry.get_config(agent_id)
    return {"tool_enabled": _read_agent_tool_enabled(cfg)}


@router.get("/agents/{agent_id}/acp/{runner_name}", summary="Get ACP runner")
async def get_acp_runner(
    agent_id: str,
    runner_name: str,
    user: Any = Depends(require_permission("acp")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    _agent_row(server, agent_id, user)
    return await get_global_acp_runner(runner_name, user=user, server=server)


@router.put("/agents/{agent_id}/acp/{runner_name}", summary="Update ACP runner")
async def put_acp_runner(
    agent_id: str,
    runner_name: str,
    body: ACPRunnerBody = Body(...),
    user: Any = Depends(require_permission("acp")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    _agent_row(server, agent_id, user)
    return await put_global_acp_runner(runner_name, body=body, user=user, server=server)


@router.delete(
    "/agents/{agent_id}/acp/{runner_name}",
    status_code=204,
    summary="Delete ACP runner",
)
async def delete_acp_runner(
    agent_id: str,
    runner_name: str,
    user: Any = Depends(require_permission("acp")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> None:
    _agent_row(server, agent_id, user)
    await delete_global_acp_runner(runner_name, user=user, server=server)
