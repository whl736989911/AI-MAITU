"""What an ``agents`` row *is*: the ``kind`` column's vocabulary.

Every agent a person owns is an ordinary agent (:data:`KIND_AGENT`) — the experts
the product has always had. :data:`KIND_FEATURE` marks an agent that belongs to a
**feature**: it is created together with the feature, its ``user_id`` is that
feature's author, and it is the conversation entry point every caller of the
feature talks to.

**Why the marker is a column and not "who owns it".** An app-owned row
(``user_id IS NULL``) used to be the only way to say "this agent is not somebody's
personal one", so the reachable predicates were derived from ``user_id``. That
does not survive features having an author: the author *is* an owner, and the
difference between "this is my expert" and "this is a feature I defined" is not
visible in ownership any more. So it is stated, once, here — and everything that
must treat a feature's agent differently reads this column, never an id prefix
and never a guess about a row.

The set is closed: a row is created with exactly one of :data:`KINDS`, and the
kind never changes afterwards (there is no update path for it).
"""

from __future__ import annotations

KIND_AGENT = "agent"
"""An ordinary agent — the user's own expert, or a copy instantiated from one."""

KIND_FEATURE = "feature"
"""A feature's own agent: created with the feature, owned by its author."""

KINDS = (KIND_AGENT, KIND_FEATURE)

FEATURE_AGENT_ID_PREFIX = "feat-"
"""Prefix of a feature's own agent id — never a user's, whose ids are minted ULIDs.

The derivation lives here, next to the kind it belongs to, because two layers
need it in opposite directions: ``feature_agent`` names an agent after its
feature, and the access rule reads a feature's ACL entry (``resource_type =
'feature'``, keyed by the *feature* id) for the agent that carries it. This
module imports nothing, so both can use it without an import cycle between the
repos and the agent manager.
"""


def feature_agent_id_for(feature_id: str) -> str:
    """The agent id a feature's id names: ``feat-<feature_id>``.

    The naming rule only. Whether that id can carry the feature's name — and
    whether an agent already holds it — is
    :func:`~octop.infra.agents.feature_agent.feature_agent_id`'s question, which
    asks the agent id rule; this one is for callers holding an id they already
    know came from a feature's ACL entry.
    """
    return f"{FEATURE_AGENT_ID_PREFIX}{feature_id}"


def feature_id_of_agent(agent_id: str) -> str | None:
    """The feature id a feature's agent id carries, or ``None`` when it carries none.

    The inverse of :func:`feature_agent_id_for`, for a row that is already known
    to be a feature's (``is_feature_agent(row.kind)``): the two are one lookup
    apart, and this is that lookup.
    """
    if not agent_id.startswith(FEATURE_AGENT_ID_PREFIX):
        return None
    return agent_id[len(FEATURE_AGENT_ID_PREFIX) :] or None


def is_feature_agent(kind: str) -> bool:
    """Whether a row of this *kind* is a feature's own agent.

    The one predicate that asks the question. Everything that must treat a
    feature's agent differently — the capability matrix, the memory freeze — reads
    the row's ``kind`` and asks here, so "what is this row" is answered in one
    place rather than re-derived at each call site.
    """
    return kind == KIND_FEATURE


__all__ = [
    "FEATURE_AGENT_ID_PREFIX",
    "KIND_AGENT",
    "KIND_FEATURE",
    "KINDS",
    "feature_agent_id_for",
    "feature_id_of_agent",
    "is_feature_agent",
]
