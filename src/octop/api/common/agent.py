"""Shared agent ownership / existence checks for HTTP routers.

Two questions live here, and they are not the same question:

- **who may use this agent** — :func:`require_agent_row`, decided by
  ``resource_acl``;
- **who may write one of its capabilities** — :func:`assert_agent_capability_write`,
  decided by the capability matrix below, on the row's own ``kind``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from octop.infra.agents.kinds import is_feature_agent
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.sharing import can_access, user_scope


def user_owns_agent(row: Any, user: Any) -> bool:
    return row.user_id is not None and row.user_id == user.id


def assert_agent_owner(row: Any, user: Any) -> None:
    """Raise if the user may not mutate this agent row (admin bypasses)."""
    if user.is_admin:
        return
    if row.user_id is None or row.user_id != user.id:
        raise OctopError(ErrorCode.FORBIDDEN, "agent not owned by user")


class AgentCapability(StrEnum):
    """One group of an agent's resources, as the capability matrix sees it.

    An endpoint names the group it writes instead of deciding for itself who may
    write it. :attr:`label` is the word a refusal uses for it.
    """

    CONFIGURATION = "configuration"
    """Skills, subagents, built-in tools, plugin tools, MBTI — what the agent *can* do."""

    PERSONA_FILES = "persona_files"
    """Its workspace persona files: the markdown the agent is run with."""

    MEMORY = "memory"
    """Its memory: the workspace ``MEMORY.md`` and the memory store behind it."""

    CHANNELS = "channels"
    """Its conversation entry points — the channels bound to it."""

    @property
    def label(self) -> str:
        """How a refusal names this group."""
        return self.value.replace("_", " ")


_FEATURE_MEMORY_REFUSAL = (
    "This agent belongs to a feature, so every caller of that feature runs on the "
    "same memory: it is read but never written — not by a run, and not by you. A "
    "feature's memory staying as it is *is* the design, not a permission you are "
    "missing."
)

_FEATURE_WRITE_REFUSAL = (
    "This agent belongs to a feature, and a feature is configured by whoever defined "
    "it (user {author}): {capability} is read-only for every other caller. Ask its "
    "author to change it."
)


def agent_capability_refusal(row: Any, user: Any, capability: AgentCapability) -> str | None:
    """Why *user* may not write *capability* of *row*, or ``None`` when they may.

    **This is the capability matrix, stated once.**

    An ordinary agent — the user's own expert — is unchanged: its owner writes
    everything it has, and an administrator may always step in.

    A feature's agent is owned by whoever defined the feature, and:

    * ``CONFIGURATION`` and ``PERSONA_FILES`` are written by that author, or an
      administrator — the same "the owner writes it" rule, read on a row whose
      owner *is* the author — and are read-only for every other caller, who
      reaches them by running the feature rather than by configuring it;
    * ``CHANNELS`` follows that rule too: the author may bind one, because a
      feature's agent *is* a conversation entry point;
    * ``MEMORY`` is written by nobody at all, an administrator included. One agent
      serves every caller of the feature, so one caller's run would leave its
      context for the next one's — that is not personalization, and neither is a
      person editing the file.

    The row and the caller are the only inputs: no id convention, no definition
    lookup, nothing that can drift from what the row says.
    """
    if not is_feature_agent(row.kind):
        if user.is_admin or user_owns_agent(row, user):
            return None
        return "agent not owned by user"
    if capability is AgentCapability.MEMORY:
        return _FEATURE_MEMORY_REFUSAL
    if user.is_admin or user_owns_agent(row, user):
        return None
    return _FEATURE_WRITE_REFUSAL.format(author=row.user_id, capability=capability.label)


def assert_agent_capability_write(row: Any, user: Any, capability: AgentCapability) -> None:
    """Raise if *user* may not write *capability* of this agent. Never silent.

    A client response is localized by error *code*, so ``FORBIDDEN`` alone would
    arrive as the generic "no permission" sentence and the matrix's own words
    would be dropped. They ride in ``details`` as well, which is how every refusal
    that has something specific to say does it here.
    """
    reason = agent_capability_refusal(row, user, capability)
    if reason is None:
        return
    if is_feature_agent(row.kind):
        raise OctopError(ErrorCode.FORBIDDEN, reason, details={"reason": reason})
    raise OctopError(ErrorCode.FORBIDDEN, reason)


def require_agent_capability_row(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
    capability: AgentCapability,
) -> Any:
    """Load an agent row and require write access to one of its capabilities.

    The counterpart of :func:`require_agent_owner_row` for endpoints that write a
    *group* of the agent's resources rather than the agent row itself: the group is
    named where the write happens, and the rule stays in
    :func:`agent_capability_refusal`.
    """
    row = require_agent_row(agent_id, user=user, as_user=as_user, server=server)
    assert_agent_capability_write(row, user, capability)
    return row


def _user_may_access(row: Any, user: Any, *, acl: ResourceAclRepo) -> bool:
    """Whether *user* may use this agent, decided by ``resource_acl`` alone.

    The legacy share boolean on the ``agents`` row is a write-only mirror of the
    ACL entry: a share applied through ``SharingService`` never touches it, so
    reading the column would deny someone the ACL already published (visible in
    a list, 403 on open).
    """
    entry = acl.get("agent", row.agent_id)
    role, unit_key = user_scope(user)
    return entry is not None and can_access(
        entry, user_id=int(user.id), role=role, unit_key=unit_key
    )


def assert_agent_access_row(row: Any, user: Any, *, acl: ResourceAclRepo) -> None:
    if not _user_may_access(row, user, acl=acl):
        raise OctopError(ErrorCode.FORBIDDEN, "agent not accessible to user")


def require_agent_row(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
) -> Any:
    """Load an agent row after the ACL / admin ``as_user`` checks."""
    assert server.app_runtime is not None
    row = server.app_runtime.agent_registry.get_row(agent_id)
    if row is None:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"agent {agent_id!r} not found")
    if as_user is not None and as_user != user.id:
        if not user.is_admin:
            raise OctopError(ErrorCode.FORBIDDEN, "as_user requires admin")
        target = server.user_manager.get_by_id(as_user)
        if target is None:
            raise OctopError(ErrorCode.NOT_FOUND, f"user {as_user} not found")
        if row.user_id is not None and row.user_id != as_user:
            raise OctopError(ErrorCode.FORBIDDEN, "agent not owned by as_user")
    else:
        assert_agent_access_row(row, user, acl=server.services.repos.resource_acl_repo)
    return row


def require_agent_owner_row(
    agent_id: str,
    *,
    user: Any,
    as_user: int | None,
    server: Any,
) -> Any:
    """Load an agent row and require owner-level access."""
    row = require_agent_row(agent_id, user=user, as_user=as_user, server=server)
    assert_agent_owner(row, user)
    return row


def assert_agent_access(server: Any, agent_id: str, user: Any) -> None:
    """Ensure agent exists and is accessible to the current user."""
    require_agent_row(agent_id, user=user, as_user=None, server=server)
