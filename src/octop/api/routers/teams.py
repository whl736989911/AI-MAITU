"""Create and manage team hosts whose roster lives in their workspace manifest."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.common.agent import assert_agent_owner
from octop.api.deps import get_server, require_permission
from octop.infra.agents.manager import AgentCreateSpec, AgentManager
from octop.infra.agents.teams import (
    TEAM_KIND,
    TEAM_TEMPLATE_NAME,
    TEMPLATE_DIR,
    TeamService,
    is_team_agent,
    team_icon_url,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer

router = APIRouter()


class TeamCreateBody(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = None
    default_model: str | None = None
    color: str | None = None
    icon_name: str | None = None
    icon_url: str | None = None
    welcome_message: str | None = None
    member_ids: list[str] = Field(default_factory=list)


class TeamPatchBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = None
    default_model: str | None = None
    color: str | None = None
    icon_name: str | None = None
    welcome_message: str | None = None
    member_ids: list[str] | None = None


class TeamMemberResponse(BaseModel):
    agent_id: str
    name: str
    color: str | None
    icon_name: str | None
    icon_url: str | None
    state: str
    is_shared: bool
    user_id: int | None


class TeamResponse(BaseModel):
    team_id: str = Field(description="Public ID of the team host agent.")
    agent_id: str
    name: str
    description: str | None
    default_model: str | None
    color: str | None
    icon_name: str | None
    icon_url: str | None
    welcome_message: str | None
    state: str
    kind: Literal["team"]
    member_ids: list[str] = Field(description="Roster in host assignment order.")
    members: list[TeamMemberResponse]


class TeamTemplateFileResponse(BaseModel):
    name: str
    content: str


def _registry(server: OctopServer) -> AgentManager:
    assert server.app_runtime is not None
    return server.app_runtime.agent_registry


def _teams(server: Any) -> TeamService:
    return _registry(server).teams


def _require_owned_team(server: Any, user: Any, team_id: str) -> Any:
    row = _registry(server).get_row(team_id)
    if row is None or not is_team_agent(row):
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"team {team_id!r} not found")
    assert_agent_owner(row, user)
    return row


@router.get("/teams", summary="List my teams", response_model=list[TeamResponse])
async def list_teams(
    user: Any = Depends(require_permission("teams")),
    server: Any = Depends(get_server),
) -> list[dict[str, Any]]:
    return await _teams(server).list_for_user(user.id)


@router.post("/teams", status_code=201, summary="Create a team", response_model=TeamResponse)
async def create_team(
    body: TeamCreateBody,
    user: Any = Depends(require_permission("teams")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    registry = _registry(server)
    teams = _teams(server)
    member_ids = teams.validate_member_ids(user, body.member_ids)
    spec = AgentCreateSpec(
        name=body.name.strip(),
        user_id=user.id,
        description=body.description,
        default_model=body.default_model,
        color=body.color,
        icon_name=body.icon_name or "users",
        icon_url=team_icon_url(body.icon_url),
        welcome_message=body.welcome_message,
        template_name=TEAM_TEMPLATE_NAME,
        kind=TEAM_KIND,
        member_ids=member_ids,
        mcp_servers=[],
    )
    created = await registry.create(spec)
    row = registry.get_row(created.agent_id) or created
    return await teams.team_payload(row)


@router.get(
    "/teams/template",
    summary="Preview team workspace files",
    response_model=list[TeamTemplateFileResponse],
)
def team_template_files(
    _user: Any = Depends(require_permission("teams")),
) -> list[dict[str, str]]:
    if not TEMPLATE_DIR.is_dir():
        return []
    return [
        {"name": path.name, "content": path.read_text(encoding="utf-8")}
        for path in sorted(TEMPLATE_DIR.iterdir())
        if path.is_file() and path.suffix.lower() == ".md" and not path.name.startswith(".")
    ]


@router.get("/teams/{team_id}", summary="Get a team", response_model=TeamResponse)
async def get_team(
    team_id: str,
    user: Any = Depends(require_permission("teams")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    row = _require_owned_team(server, user, team_id)
    return await _teams(server).team_payload(row)


@router.patch("/teams/{team_id}", summary="Update a team", response_model=TeamResponse)
async def patch_team(
    team_id: str,
    body: TeamPatchBody,
    user: Any = Depends(require_permission("teams")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    registry = _registry(server)
    _require_owned_team(server, user, team_id)
    teams = _teams(server)
    roster_changed = False
    if body.member_ids is not None:
        next_ids = teams.validate_member_ids(user, body.member_ids)
        await teams.assert_roster_writable(team_id, next_ids)
        await teams.areplace_members(team_id, next_ids)
        roster_changed = True
    updates: dict[str, Any] = {}
    if body.name is not None:
        updates["name"] = body.name.strip()
    if body.description is not None:
        updates["description"] = body.description
    if body.default_model is not None:
        updates["default_model"] = body.default_model
    if body.color is not None:
        updates["color"] = body.color
    if body.icon_name is not None:
        updates["icon_name"] = body.icon_name
    if body.welcome_message is not None:
        updates["welcome_message"] = body.welcome_message
    if updates:
        await registry.update(team_id, **updates)
    if roster_changed:
        await registry.reload(team_id)
    row = registry.get_row(team_id)
    if row is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"team {team_id!r} not found")
    return await teams.team_payload(row)


@router.delete("/teams/{team_id}", status_code=204, summary="Delete a team")
async def delete_team(
    team_id: str,
    user: Any = Depends(require_permission("teams")),
    server: Any = Depends(get_server),
) -> None:
    _require_owned_team(server, user, team_id)
    await _registry(server).delete(team_id)
