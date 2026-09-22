"""The feature's own agent — a definition's personalization, materialized lazily.

Design 5.1 gives every feature an agent of its own: somewhere for the panels that
only exist *against a live agent* to attach — workspace persona files, installed
skills and subagents, plugin switches, MBTI, the default model. Design 5.2 keeps
that agent a *configuration carrier*: what a run reads per caller (connectors,
knowledge bases, turn-scoped tools, the session) is resolved against the caller,
exactly as it was when every run used the caller's agent. Nothing about the data
plane changes here.

**Lazy.** The agent is created when an author first asks to personalize the
definition — never when the definition is created, and never by a run. A
definition nobody has personalized keeps running on the caller's own agent,
unchanged (`_run_agent_id`'s branch in the router).

**The row is the record.** Nothing is added to ``feature.json``: a feature has its
own agent exactly when an app-owned (``user_id IS NULL``) agent with the derived id
exists. The definition file therefore cannot disagree with reality, a definition
PUT cannot drop the association by not sending a key back, and deleting the agent
through the ordinary agent endpoints is the documented way back to the
unpersonalized state. The shared-agent shape is an existing one, not a new one:
``AgentManager`` already warns/downgrades for ``user_id IS NULL`` rows
(``_connector_uid_for``, ``_build_harness_config``) and resolves connectors per
caller through the turn-scoped tool registry.

**The id is ``feat-<feature_id>``** — prefixed so it cannot collide with a user's
own agents, app-owned so it belongs to the feature rather than to whoever
configured it. The two id rules involved are *not* the same rule, and the
difference is reported rather than papered over: the definition id rule
(``store._ID_RE``) allows 64 characters ending in ``-`` or ``_``, while the agent
id rule (:func:`~octop.infra.agents.manager.validate_custom_agent_id`) requires
3-64 characters that *start and end* with a letter or digit. A feature id longer
than 59 characters, or one ending in a separator, cannot name an agent at all:
:func:`feature_agent_id` answers ``None``, a run keeps the caller's agent (there is
nothing else to use — an agent for such an id could never have been created), and
:func:`ensure_feature_agent` refuses with the rule's own words.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from octop.infra.agents.manager import AgentCreateSpec, validate_custom_agent_id
from octop.infra.errors import ErrorCode, OctopError

if TYPE_CHECKING:
    from octop.infra.db.repos.agents import AgentRow
    from octop.infra.features.catalog import Feature

AGENT_ID_PREFIX = "feat-"
"""Prefix of a feature's own agent id — never a user's, whose ids are minted ULIDs."""


def feature_agent_id(feature_id: str) -> str | None:
    """The agent id personalizing *feature_id* uses, or ``None`` when it has none.

    ``None`` is a fact about the *name*, not about the feature: this definition's id
    cannot be carried into an agent id (see the module docstring), so it can never
    be materialized and every run of it stays on the caller's agent.
    """
    try:
        return _checked_agent_id(feature_id)
    except OctopError:
        return None


def _checked_agent_id(feature_id: str) -> str:
    """``feat-<feature_id>``, or the refusal naming the agent rule it breaks.

    The rule is the manager's own (:func:`validate_custom_agent_id`) rather than a
    copy of it, so the naming and the creation can never disagree about what a
    legal agent id is — the refusal carries that rule's wording. The reason rides in
    ``details`` as well as the message: a client response is localized by error
    *code* (``FEATURE_INVALID`` interpolates ``details["errors"]``), so the specifics
    would otherwise be replaced by the generic sentence.
    """
    candidate = f"{AGENT_ID_PREFIX}{feature_id}"
    try:
        return validate_custom_agent_id(candidate)
    except OctopError as exc:
        reason = (
            f"feature id {feature_id!r} cannot name an agent: {exc} "
            f"(its agent id would be {candidate!r})"
        )
        raise OctopError(ErrorCode.FEATURE_INVALID, reason, details={"errors": reason}) from exc


def materialized_feature_agent_id(server: Any, feature: Feature) -> str | None:
    """The feature's own agent id once it has one, else ``None``.

    The one predicate the run path and the personalization entry point share, so a
    run and the editor can never disagree about whether a definition is
    personalized. An agent of that exact id owned by a *user* is not this feature's
    agent — it is somebody's agent that happens to carry the name — and is never
    handed to a run.
    """
    row = _feature_agent_row(server, feature)
    return None if row is None else row.agent_id


def _feature_agent_row(server: Any, feature: Feature) -> AgentRow | None:
    """The app-owned agent row of this feature, or ``None`` when there is none."""
    agent_id = feature_agent_id(feature.id)
    if agent_id is None:
        return None
    row: AgentRow | None = server.app_runtime.agent_registry.get_row(agent_id)
    if row is None or row.user_id is not None:
        return None
    return row


async def ensure_feature_agent(server: Any, feature: Feature) -> tuple[AgentRow, bool]:
    """Give *feature* an agent of its own, creating it on the first call.

    Returns ``(row, created)``: ``created`` is ``False`` when the definition already
    had one, which is what makes this safe to call from the editor every time the
    personalization surface opens. Idempotence is by the row lookup, not by a stored
    flag — see the module docstring.

    The agent goes through ``AgentManager.create`` with an :class:`AgentCreateSpec`,
    the same path expert instantiation uses: there is no second creation path, so
    workspace seeding, harness start, ACL defaults and audit all behave exactly as
    they do for any other agent.
    """
    agent_id = _checked_agent_id(feature.id)
    row = _feature_agent_row(server, feature)
    created = row is None
    if row is None:
        # An id already taken by somebody else's agent is refused by ``create``
        # itself (``AGENT_ID_TAKEN``) — never adopted, never overwritten.
        row = await server.app_runtime.agent_registry.create(_create_spec(feature, agent_id))
    await _require_running(server, row.agent_id)
    return row, created


def _create_spec(feature: Feature, agent_id: str) -> AgentCreateSpec:
    """The spec the first personalization of *feature* materializes.

    Only what the definition already states is carried over, and nothing is
    invented: the label and description name the agent the way every other
    feature-facing name is resolved (``zh`` first, then ``en``, then the id — the
    chain ``features.rules`` heads an extraction prompt with), the icon and colour
    are the catalog card's, and a declared ``agent.model`` becomes the agent's
    default model so the personalization panels open on the model the feature
    already runs with.

    ``user_id`` is ``None`` on purpose: the agent belongs to the definition, not to
    the administrator who happened to configure it, so a later owner change (or
    departure) cannot take the feature's personalization with it — and no non-admin
    can reach it through the agent endpoints (:func:`assert_agent_owner` refuses a
    NULL owner for everyone but an admin).
    """
    return AgentCreateSpec(
        agent_id=agent_id,
        name=feature.label.get("zh") or feature.label.get("en") or feature.id,
        user_id=None,
        description=feature.description.get("zh") or feature.description.get("en") or None,
        icon_name=feature.icon_name or None,
        color=feature.color,
        default_model=feature.agent.model if feature.agent is not None else None,
    )


async def _require_running(server: Any, agent_id: str) -> None:
    """Leave the harness holding a live handle for *agent_id*, or say why not.

    The personalization panels read skills, subagents and workspace files off a live
    handle (``list_skill_summaries`` starts with ``get_agent``), so "the row exists"
    is not the answer an editor can use. A start that leaves no handle is reported
    here, in the row's own terms (``AGENT_FAILED``/``AGENT_NOT_RUNNING``), instead of
    a success the next call would fail on — never silently.
    """
    registry = server.app_runtime.agent_registry
    try:
        registry.get_agent(agent_id)
        return
    except OctopError:
        # Not loaded yet (a fresh row, or one stopped since): boot it the way the
        # agents router does, then read the handle again.
        await registry.start(agent_id)
    # This second read is the report, not a leftover: a start that did not take
    # raises here with the failure the row recorded.
    registry.get_agent(agent_id)


__all__ = [
    "AGENT_ID_PREFIX",
    "ensure_feature_agent",
    "feature_agent_id",
    "materialized_feature_agent_id",
]
