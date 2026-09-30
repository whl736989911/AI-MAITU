"""Team hosts, persisted workspace rosters, and room streaming."""

from octop.infra.agents.teams.jobs import TeamJobTracker
from octop.infra.agents.teams.service import (
    HOST_TOOLS_ALLOWED,
    HOST_TOOLS_DISABLED,
    TEAM_AVATAR_URL,
    TEAM_KIND,
    TEAM_MANIFEST_WORKSPACE,
    TEAM_MIN_MEMBERS,
    TEAM_TEMPLATE_NAME,
    TEMPLATE_DIR,
    TeamService,
    host_tools_disabled,
    is_team_agent,
    seed_team_template,
    team_icon_url,
)
from octop.infra.agents.teams.team_manager import (
    TeamManager,
    host_system_prompt,
    stamp_stream_speaker,
    stamp_team_host_chunk,
    wire_host_dispatch,
)

__all__ = [
    "HOST_TOOLS_ALLOWED",
    "HOST_TOOLS_DISABLED",
    "TEAM_AVATAR_URL",
    "TEAM_KIND",
    "TEAM_MANIFEST_WORKSPACE",
    "TEAM_MIN_MEMBERS",
    "TEAM_TEMPLATE_NAME",
    "TEMPLATE_DIR",
    "TeamJobTracker",
    "TeamManager",
    "TeamService",
    "host_system_prompt",
    "host_tools_disabled",
    "is_team_agent",
    "seed_team_template",
    "stamp_stream_speaker",
    "stamp_team_host_chunk",
    "team_icon_url",
    "wire_host_dispatch",
]
