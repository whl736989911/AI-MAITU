"""Team roster persistence and host runtime policy."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from octop.infra.agents.kinds import KIND_AGENT, KIND_TEAM
from octop.infra.agents.teams.jobs import TeamJobTracker
from octop.infra.agents.tool_catalog import BUILTIN_TOOL_CATALOG
from octop.infra.db.repos.agents import AgentRow
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError

TEAM_KIND = KIND_TEAM
TEAM_TEMPLATE_NAME = "team-host"
TEAM_MIN_MEMBERS = 2
TEAM_AVATAR_URL = "/experts/avatars/team-host.svg"
TEMPLATE_DIR = Path(__file__).resolve().parent / "template"
TEAM_MANIFEST_WORKSPACE = ".octop/manifest.json"

# Hosts only dispatch and keep light memory/time — members do the work.
HOST_TOOLS_ALLOWED: frozenset[str] = frozenset(
    {"agent_list", "ask_agent", "memory_search", "memory_get", "current_time"}
)
HOST_TOOLS_DISABLED: frozenset[str] = frozenset(
    entry.name for entry in BUILTIN_TOOL_CATALOG if entry.name not in HOST_TOOLS_ALLOWED
)


def host_tools_disabled(extra: frozenset[str] | set[str] | tuple[str, ...] = ()) -> frozenset[str]:
    return frozenset(extra) | HOST_TOOLS_DISABLED


def is_team_agent(row: AgentRow | None) -> bool:
    return row is not None and row.kind == KIND_TEAM


_LEGACY_TEAM_AVATARS = frozenset({"/experts/avatars/multi-agent-orchestrator.svg"})


def team_icon_url(stored: str | None) -> str:
    text = str(stored or "").strip()
    return TEAM_AVATAR_URL if not text or text in _LEGACY_TEAM_AVATARS else text


def _normalize_member_ids(raw: Any) -> list[str]:
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for item in raw:
        member_id = str(item or "").strip()
        if member_id and member_id not in seen:
            seen.add(member_id)
            out.append(member_id)
    return out


def _manifest_members(data: dict[str, Any]) -> list[str]:
    return _normalize_member_ids(data.get("members") or data.get("member"))


class TeamService:
    """Owns team roster state through the host's BackendWorkspace."""

    def __init__(
        self,
        repos: RepoBundle,
        jobs: TeamJobTracker | None = None,
        workspace_for: Callable[[str], Any | None] | None = None,
        mirror_workspace_for: Callable[[str], Any | None] | None = None,
    ) -> None:
        self._repos = repos
        self.jobs = jobs or TeamJobTracker()
        self._workspace_for = workspace_for
        self._mirror_workspace_for = mirror_workspace_for

    def _workspace(self, team_agent_id: str) -> Any:
        workspace = self._workspace_for(team_agent_id) if self._workspace_for else None
        if workspace is None:
            raise OctopError(
                ErrorCode.AGENT_NOT_FOUND,
                f"workspace unavailable for team {team_agent_id!r}",
            )
        return workspace

    async def _read_manifest(
        self, team_agent_id: str, workspace: Any | None = None
    ) -> dict[str, Any]:
        active = workspace or self._workspace(team_agent_id)
        text = await active.aread_text(TEAM_MANIFEST_WORKSPACE)
        if text is None or not str(text).strip():
            return {}
        try:
            value = json.loads(str(text))
        except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError):
            return {}
        return value if isinstance(value, dict) else {}

    async def _write_manifest(
        self,
        team_agent_id: str,
        member_ids: list[str],
        workspace: Any | None = None,
    ) -> None:
        active = workspace or self._workspace(team_agent_id)
        data = await self._read_manifest(team_agent_id, active)
        data["kind"] = TEAM_KIND
        data["members"] = list(member_ids)
        payload = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        await active.awrite_text(TEAM_MANIFEST_WORKSPACE, payload, force=True)
        mirror = self._mirror_workspace_for(team_agent_id) if self._mirror_workspace_for else None
        if mirror is not None and mirror is not active:
            await mirror.awrite_text(TEAM_MANIFEST_WORKSPACE, payload, force=True)

    async def member_ids(
        self,
        team_agent_id: str,
        *,
        workspace: Any | None = None,
    ) -> list[str]:
        return _manifest_members(await self._read_manifest(team_agent_id, workspace))

    async def visible_member_ids(self, team_agent_id: str) -> list[str]:
        """Persisted roster minus deleted, disabled, feature, and team rows."""
        visible: list[str] = []
        for member_id in await self.member_ids(team_agent_id):
            row = self._repos.agent_repo.get(member_id)
            if row is not None and row.enabled and row.kind == KIND_AGENT:
                visible.append(member_id)
        return visible

    def validate_member_ids(self, user: Any, member_ids: list[str]) -> list[str]:
        """Validate a complete replacement roster against the caller's ACL view."""
        seen: set[str] = set()
        normalized: list[str] = []
        invalid: list[str] = []
        for raw in member_ids:
            member_id = str(raw or "").strip()
            if not member_id or member_id in seen:
                continue
            seen.add(member_id)
            normalized.append(member_id)

        allowed = {row.agent_id for row in self._repos.agent_repo.list_visible(int(user.id))}
        owned = {
            row.agent_id for row in self._repos.agent_repo.list_by_user(int(user.id)) if row.enabled
        }
        allowed.update(owned)
        out: list[str] = []
        for member_id in normalized:
            row = self._repos.agent_repo.get(member_id)
            if row is None or row.kind != KIND_AGENT or not row.enabled or member_id not in allowed:
                invalid.append(member_id)
            else:
                out.append(member_id)
        if invalid:
            raise OctopError(
                ErrorCode.FORBIDDEN,
                "one or more team members are unavailable to this user",
                details={"member_agent_ids": invalid},
            )
        if len(out) < TEAM_MIN_MEMBERS:
            raise OctopError(
                ErrorCode.TEAM_MEMBERS_TOO_FEW,
                f"a team needs at least {TEAM_MIN_MEMBERS} members",
                details={"min_members": TEAM_MIN_MEMBERS},
            )
        return out

    async def assert_roster_writable(
        self,
        team_agent_id: str,
        next_member_ids: list[str],
    ) -> None:
        removed = set(await self.member_ids(team_agent_id)) - set(next_member_ids)
        blocked = sorted(removed & self.jobs.busy_member_ids(team_agent_id))
        if blocked:
            raise OctopError(
                ErrorCode.TEAM_MEMBER_BUSY,
                "cannot remove a member with an in-flight team job",
                details={"member_agent_ids": blocked},
            )

    def assert_can_delete_agent(self, agent_id: str) -> None:
        row = self._repos.agent_repo.get(agent_id)
        if row is None:
            raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
        if is_team_agent(row):
            busy = self.jobs.busy_member_ids(agent_id)
            if busy:
                raise OctopError(
                    ErrorCode.TEAM_MEMBER_BUSY,
                    "cannot delete a team while members have in-flight jobs",
                    details={"member_agent_ids": sorted(busy)},
                )
        elif self.jobs.is_member_busy(agent_id):
            raise OctopError(
                ErrorCode.TEAM_MEMBER_BUSY,
                "cannot delete an expert with an in-flight team job",
                details={"member_agent_id": agent_id},
            )

    async def areplace_members(
        self,
        team_agent_id: str,
        member_ids: list[str],
        *,
        workspace: Any | None = None,
    ) -> None:
        await self._write_manifest(team_agent_id, member_ids, workspace)

    async def adrop_member(self, member_id: str) -> list[str]:
        """Remove a deleted member from all team manifests, including remote workspaces."""
        target = str(member_id or "").strip()
        if not target:
            return []
        changed: list[str] = []
        for row in self._repos.agent_repo.list_all():
            if not is_team_agent(row):
                continue
            roster = await self.member_ids(row.agent_id)
            if target in roster:
                await self.areplace_members(
                    row.agent_id,
                    [item for item in roster if item != target],
                )
                changed.append(row.agent_id)
        return changed

    async def team_payload(self, row: AgentRow) -> dict[str, Any]:
        ids = await self.visible_member_ids(row.agent_id)
        members = [self._repos.agent_repo.get(member_id) for member_id in ids]
        public_ids = self._repos.agent_repo.public_agent_ids(ids)
        return {
            "team_id": row.agent_id,
            "agent_id": row.agent_id,
            "name": row.name,
            "description": row.description,
            "default_model": row.default_model,
            "color": row.color,
            "icon_name": row.icon_name,
            "icon_url": team_icon_url(row.icon_url),
            "welcome_message": row.welcome_message,
            "state": row.last_state or "unknown",
            "kind": TEAM_KIND,
            "member_ids": ids,
            "members": [
                {
                    "agent_id": member.agent_id,
                    "name": member.name,
                    "color": member.color,
                    "icon_name": member.icon_name,
                    "icon_url": member.icon_url,
                    "state": member.last_state or "unknown",
                    "is_shared": member.agent_id in public_ids,
                    "user_id": member.user_id,
                }
                for member in members
                if member is not None
            ],
        }

    async def list_for_user(self, user_id: int) -> list[dict[str, Any]]:
        teams = [row for row in self._repos.agent_repo.list_by_user(user_id) if is_team_agent(row)]
        return [await self.team_payload(row) for row in teams]


def _team_template_pairs(member_ids: list[str] | None) -> list[tuple[str, bytes]]:
    """Read packaged template files without blocking an async request."""
    if not TEMPLATE_DIR.is_dir():
        return []
    roster = _normalize_member_ids(member_ids) if member_ids is not None else None
    pairs: list[tuple[str, bytes]] = []
    for path in sorted(TEMPLATE_DIR.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.name == "manifest.json":
            try:
                data = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                data = {}
            if not isinstance(data, dict):
                data = {}
            data["kind"] = TEAM_KIND
            data["members"] = roster or []
            pairs.append(
                (
                    TEAM_MANIFEST_WORKSPACE,
                    (json.dumps(data, ensure_ascii=False, indent=2) + "\n").encode("utf-8"),
                )
            )
        else:
            pairs.append((path.name, path.read_bytes()))
    return pairs


async def seed_team_template(workspace: Any, *, member_ids: list[str] | None = None) -> None:
    """Seed packaged team persona files and the initial workspace manifest."""
    pairs = await asyncio.get_running_loop().run_in_executor(None, _team_template_pairs, member_ids)
    if pairs:
        await workspace.aupload_many(pairs)
