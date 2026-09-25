"""Shared agent ownership / existence checks for HTTP routers.

Two questions live here, and they are not the same question:

- **who may use this agent** — :func:`require_agent_row`, decided by
  ``resource_acl``;
- **who may write one of its capabilities** — :func:`assert_agent_capability_write`,
  decided by the capability matrix below, on the row's own ``kind``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from octop.infra.agents.kinds import feature_id_of_agent, is_feature_agent
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.sharing import AclEntry, can_access


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
    """Shared training memory, published shared memory, or caller-private memory."""

    CHANNELS = "channels"
    """Its conversation entry points — the channels bound to it."""

    @property
    def label(self) -> str:
        """How a refusal names this group."""
        return self.value.replace("_", " ")


_FEATURE_MEMORY_REFUSAL = (
    "Feature memory requires its stage and scope: the author can edit shared "
    "memory during training, but published shared memory is read-only. A published "
    "feature's private memory belongs to the authenticated caller."
)

_FEATURE_WRITE_REFUSAL = (
    "This agent belongs to a feature, and a feature is configured by whoever defined "
    "it (user {author}): {capability} is read-only for every other caller. Ask its "
    "author to change it."
)


def agent_capability_refusal(
    row: Any,
    user: Any,
    capability: AgentCapability,
    *,
    memory_stage: Literal["draft", "active"] | None = None,
    memory_scope: Literal["shared", "private"] = "shared",
) -> str | None:
    """Return a write refusal for this row, caller, capability and memory phase.

    Without a verified stage, feature-memory writes fail closed. Only the
    memory endpoints may provide a stage after reading the workflow definition;
    the ordinary configuration and persona rules do not depend on it.
    """
    if not is_feature_agent(row.kind):
        if user.is_admin or user_owns_agent(row, user):
            return None
        return "agent not owned by user"
    if capability is AgentCapability.MEMORY:
        if memory_stage == "active" and memory_scope == "private":
            return None  # Access to this private namespace was checked by its caller.
        if memory_stage == "draft" and memory_scope == "shared":
            if user.is_admin or user_owns_agent(row, user):
                return None
            return _FEATURE_WRITE_REFUSAL.format(author=row.user_id, capability=capability.label)
        return _FEATURE_MEMORY_REFUSAL
    if user.is_admin or user_owns_agent(row, user):
        return None
    return _FEATURE_WRITE_REFUSAL.format(author=row.user_id, capability=capability.label)


def assert_agent_capability_write(
    row: Any,
    user: Any,
    capability: AgentCapability,
    *,
    memory_stage: Literal["draft", "active"] | None = None,
    memory_scope: Literal["shared", "private"] = "shared",
) -> None:
    """Raise if *user* may not write this capability in its verified memory scope."""
    reason = agent_capability_refusal(
        row, user, capability, memory_stage=memory_stage, memory_scope=memory_scope
    )
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


def agent_access_entries(row: Any, *, acl: ResourceAclRepo) -> list[AclEntry]:
    """Every ACL entry that decides this agent row.

    Normally one: the row's own ``agent`` entry. A feature's agent has a second,
    filed under ``resource_type='feature'`` and keyed by the *feature* id its
    agent id carries (``feat-<feature_id>``): a feature *is* its agent
    (:mod:`octop.infra.agents.kinds`), so the entry
    ``POST /api/sharing/acl/feature/{id}`` writes governs the same resource, and
    until this was read the sharing API accepted such a grant and nothing
    anywhere answered it — the one resource type whose entry had no reader.

    Both entries are read and neither narrows the other: ``resource_acl`` states
    that grants only ever widen access, so the verdict is the union, with no
    precedence between an agent share and a feature share to get wrong. The
    rule itself is not restated here — every entry goes through
    :func:`~octop.infra.sharing.can_access`.
    """
    entries = [entry for entry in (acl.get("agent", row.agent_id),) if entry is not None]
    if is_feature_agent(row.kind):
        feature_id = feature_id_of_agent(row.agent_id)
        if feature_id is not None:
            feature_entry = acl.get("feature", feature_id)
            if feature_entry is not None:
                entries.append(feature_entry)
    return entries


def _user_may_access(row: Any, user: Any, *, acl: ResourceAclRepo) -> bool:
    """Whether *user* may use this agent, decided by ``resource_acl`` alone.

    The legacy share boolean on the ``agents`` row is a write-only mirror of the
    ACL entry: a share applied through ``SharingService`` never touches it, so
    reading the column would deny someone the ACL already published (visible in
    a list, 403 on open).
    """
    role, unit_keys = acl.scope_for_user(int(user.id))
    return any(
        can_access(entry, user_id=int(user.id), role=role, unit_keys=unit_keys)
        for entry in agent_access_entries(row, acl=acl)
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
