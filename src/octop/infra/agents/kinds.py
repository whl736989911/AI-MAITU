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


def is_feature_agent(kind: str) -> bool:
    """Whether a row of this *kind* is a feature's own agent.

    The one predicate that asks the question. Everything that must treat a
    feature's agent differently — the capability matrix, the memory freeze — reads
    the row's ``kind`` and asks here, so "what is this row" is answered in one
    place rather than re-derived at each call site.
    """
    return kind == KIND_FEATURE


__all__ = ["KIND_AGENT", "KIND_FEATURE", "KINDS", "is_feature_agent"]
