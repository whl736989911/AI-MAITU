"""A feature's own agent: what it is called, and how it comes into being.

A feature is not a second kind of resource next to the agents the product already
has — it *is* an agent (:data:`~octop.infra.agents.kinds.KIND_FEATURE`), created
together with the feature and owned by whoever defined it. Everything that hangs
off an agent therefore works for it unchanged: the personalization panels (they
were always parameterised on one agent id), the workspace, the skills and
subagents installed into it, the model it defaults to, and the conversation
entry points.

**The author owns it.** ``user_id`` is the person who created the feature, so the
feature's configuration has an owner to be resolved for at runtime — the IM
inbound path resolves the Octop user from the agent's owner, and an ownerless row
resolves to ``user_id = 0``, which the session/thread/knowledge paths have no
guest for. The *marker* that separates a feature's agent from somebody's expert is
``agents.kind``, never the ownership and never the id prefix: the two questions
("who owns this" and "what is this") are answered by two different columns, and a
feature's agent has both answers.

**The id is ``feat-<feature_id>``** with the feature's own id in place of an
ownership check: the id is derived, so a feature has at most one agent and the
derivation is the whole lookup. The two id rules involved are not the same rule,
and the difference is reported rather than papered over: a feature id that cannot
be carried into an agent id (see :func:`feature_agent_id`) refuses with the agent
id rule's own words.

**One creation path.** The row goes through
:meth:`~octop.infra.agents.manager.AgentManager.create` with an
:class:`~octop.infra.agents.manager.AgentCreateSpec` — the same path expert
instantiation uses — so workspace seeding, harness start, ACL defaults and audit
behave exactly as they do for any other agent.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from octop.infra.agents.kinds import KIND_FEATURE, feature_agent_id_for
from octop.infra.agents.manager import AgentCreateSpec, validate_custom_agent_id
from octop.infra.errors import OctopError

if TYPE_CHECKING:
    from octop.infra.db.repos.agents import AgentRow
    from octop.infra.server import OctopServer


def feature_agent_id(feature_id: str) -> str | None:
    """The agent id of *feature_id*, or ``None`` when no agent could carry it.

    ``None`` is a fact about the *name*: the feature's id cannot be carried into an
    agent id (the feature id rule allows characters, lengths and a trailing
    separator that :func:`~octop.infra.agents.manager.validate_custom_agent_id`
    refuses), so such a feature can never have an agent of its own at all.
    """
    try:
        return _checked_agent_id(feature_id)
    except OctopError:
        return None


def _checked_agent_id(feature_id: str) -> str:
    """``feat-<feature_id>``, or the refusal naming the agent id rule it breaks.

    The rule is the manager's own (:func:`validate_custom_agent_id`) rather than a
    copy of it, so the naming and the creation cannot disagree about what a legal
    agent id is — the refusal carries that rule's wording and the id it was applied
    to.
    """
    candidate = feature_agent_id_for(feature_id)
    try:
        return validate_custom_agent_id(candidate)
    except OctopError as exc:
        raise OctopError(
            exc.code,
            f"feature id {feature_id!r} cannot name an agent: {exc} "
            f"(its agent id would have been {candidate!r})",
        ) from exc


async def create_feature_agent(
    server: OctopServer,
    *,
    feature_id: str,
    author_user_id: int,
    name: str,
    description: str | None = None,
    icon_name: str | None = None,
    color: str | None = None,
    default_model: str | None = None,
) -> AgentRow:
    """Create the agent that *feature_id* is, owned by *author_user_id*.

    Called when the feature itself is created, so its agent exists before anybody
    can run it or configure it — a feature always has exactly one agent, and there
    is no second, lazily-built one to disagree with it.

    Only what the feature states is carried over and nothing is invented: *name* is
    the feature's own name (what the caller already resolved for its locale),
    *description*/*icon_name*/*color* are what the definition declares, and a
    declared *default_model* becomes the agent's default so its panels open on the
    model the feature runs with.

    Refusals come from :meth:`AgentManager.create` itself: an id already taken by
    somebody else's agent (``AGENT_ID_TAKEN``) is never adopted and never
    overwritten, and a name the author already used is refused there too
    (``AGENT_NAME_TAKEN``) rather than silently renamed.
    """
    assert server.app_runtime is not None
    agent_id = _checked_agent_id(feature_id)
    return await server.app_runtime.agent_registry.create(
        AgentCreateSpec(
            agent_id=agent_id,
            name=name,
            user_id=author_user_id,
            kind=KIND_FEATURE,
            description=description,
            icon_name=icon_name,
            color=color,
            default_model=default_model,
        )
    )


__all__ = ["create_feature_agent", "feature_agent_id"]
