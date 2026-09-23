"""The capability matrix, in one place: who writes what, on which kind of agent.

The table these assert is stated once, in
:func:`octop.api.common.agent.agent_capability_refusal`, and every endpoint that
writes one of the groups asks it. Two axes meet there — the kind of agent, and
who is calling — and both are swept here for all four groups, so a row that
changes has to change here too.
"""

from __future__ import annotations

from dataclasses import dataclass

import pytest

from octop.api.common.agent import (
    AgentCapability,
    agent_capability_refusal,
    assert_agent_capability_write,
    user_owns_agent,
)
from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.errors import ErrorCode, OctopError

AUTHOR_ID = 7
"""The owner of the feature agent under test — its author."""

CALLER_ID = 8
"""A regular user who is neither the author nor an administrator."""

GROUPS = tuple(AgentCapability)


@dataclass(frozen=True)
class _Row:
    agent_id: str
    user_id: int | None
    kind: str = KIND_AGENT


@dataclass(frozen=True)
class _User:
    id: int
    is_admin: bool = False


AUTHOR = _User(AUTHOR_ID)
CALLER = _User(CALLER_ID)
ADMIN = _User(1, is_admin=True)

FEATURE_AGENT = _Row("feat-weekly-report", AUTHOR_ID, KIND_FEATURE)
EXPERT = _Row("01EXPERT", AUTHOR_ID, KIND_AGENT)
APP_OWNED_EXPERT = _Row("01SYSTEM", None, KIND_AGENT)


@pytest.mark.parametrize("capability", GROUPS)
def test_a_feature_agents_author_writes_every_group_but_its_memory(
    capability: AgentCapability,
) -> None:
    """The author configures the feature; its memory is nobody's to write."""
    refusal = agent_capability_refusal(FEATURE_AGENT, AUTHOR, capability)

    if capability is AgentCapability.MEMORY:
        assert refusal is not None
        assert "never written" in refusal
    else:
        assert refusal is None


@pytest.mark.parametrize("capability", GROUPS)
def test_an_ordinary_caller_writes_none_of_a_feature_agents_groups(
    capability: AgentCapability,
) -> None:
    """Read-only for everybody else, and said so rather than answered silently."""
    refusal = agent_capability_refusal(FEATURE_AGENT, CALLER, capability)

    assert refusal is not None
    if capability is AgentCapability.MEMORY:
        assert "never written" in refusal
    else:
        assert "read-only" in refusal


@pytest.mark.parametrize("capability", GROUPS)
def test_an_administrator_may_still_step_in_but_never_for_memory(
    capability: AgentCapability,
) -> None:
    """The admin bypass survives on the configuration groups, and stops at memory."""
    refusal = agent_capability_refusal(FEATURE_AGENT, ADMIN, capability)

    if capability is AgentCapability.MEMORY:
        assert refusal is not None
    else:
        assert refusal is None


@pytest.mark.parametrize("capability", GROUPS)
def test_an_expert_is_unchanged_whatever_the_group(capability: AgentCapability) -> None:
    """The owner writes everything their expert has; its memory included.

    The byte-identical old rule: no group is special for an ordinary agent, which
    is what makes this a *copy* of the expert behaviour rather than a second one.
    """
    assert agent_capability_refusal(EXPERT, AUTHOR, capability) is None
    assert agent_capability_refusal(EXPERT, ADMIN, capability) is None
    assert agent_capability_refusal(EXPERT, CALLER, capability) == "agent not owned by user"


@pytest.mark.parametrize("capability", GROUPS)
def test_an_app_owned_row_that_is_not_a_feature_is_nobodys(
    capability: AgentCapability,
) -> None:
    """``kind`` decides, not ownership: an app-owned ordinary agent keeps the old answer.

    Nobody owns such a row, so only an administrator writes it — exactly what
    ``assert_agent_owner`` has always said, and what the freeze no longer reads
    ownership to decide.
    """
    assert agent_capability_refusal(APP_OWNED_EXPERT, ADMIN, capability) is None
    assert (
        agent_capability_refusal(APP_OWNED_EXPERT, AUTHOR, capability) == "agent not owned by user"
    )


def test_the_refusal_is_raised_as_a_forbidden_error_not_swallowed() -> None:
    with pytest.raises(OctopError) as excinfo:
        assert_agent_capability_write(FEATURE_AGENT, CALLER, AgentCapability.CONFIGURATION)

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    assert str(AUTHOR_ID) in str(excinfo.value)
    with pytest.raises(OctopError):
        assert_agent_capability_write(FEATURE_AGENT, AUTHOR, AgentCapability.MEMORY)


def test_ownership_is_still_only_about_who_owns_the_row() -> None:
    """``user_owns_agent`` answers ownership; the matrix asks it, it does not merge it."""
    assert user_owns_agent(FEATURE_AGENT, AUTHOR)
    assert not user_owns_agent(FEATURE_AGENT, CALLER)
    assert not user_owns_agent(APP_OWNED_EXPERT, ADMIN)
