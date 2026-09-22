"""The features collection — the one call that is not already an agent's.

A feature *is* an agent (:mod:`octop.infra.agents.kinds`): creating one creates the
agent that carries it, owned by whoever defined it, and everything the product
already does to an agent applies to it unchanged. So this router holds exactly what
has no agent endpoint of its own — bringing a feature and its agent into existence —
and nothing else. Its capabilities, its workspace, its runtime state, its
``PATCH`` and its ``DELETE`` stay on ``/api/agents/{id}/...``, which is where the
dashboard's panels and the experts' own surfaces already speak.

**Why creation is not ``POST /api/agents``.** There the id is the server's to mint,
and the row is an ordinary agent. A feature's agent is named after its feature
(``feat-<feature_id>``, see :func:`~octop.infra.agents.feature_agent.feature_agent_id`)
so that the two are one lookup apart, and it is marked
:data:`~octop.infra.agents.kinds.KIND_FEATURE` so that every surface that must treat
it differently can ask. Both are decisions of the feature's, not of a generic agent
create, and both are made in one place —
:func:`~octop.infra.agents.feature_agent.create_feature_agent` — which this endpoint
calls rather than re-implementing.

**Reading features is ``GET /api/agents``.** A feature is an agent row like any
other, so the list the dashboard already fetches carries it, ``kind`` and all; a
second listing endpoint would be a second answer to "which agents are there" and
would have to be kept in step with the first. The dashboard filters by
``kind == "feature"``.

Refusals are the manager's own: an id another agent already holds
(``AGENT_ID_TAKEN``) is never adopted, a name the author already used is never
silently renamed (``AGENT_NAME_TAKEN``), and a feature id that cannot be carried
into an agent id is refused with the agent id rule's own words.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import current_user, get_server
from octop.infra.agents.feature_agent import create_feature_agent

router = APIRouter()


class FeatureCreateBody(BaseModel):
    """What a feature declares about itself when it is created.

    The feature's id is the caller's to choose and is what its agent is named after;
    everything else is optional and is carried onto the agent as it stands. Nothing
    is inferred here — fields the definition does not state keep the agent's own
    defaults.
    """

    feature_id: str = Field(
        ...,
        min_length=1,
        description="The feature's id; its agent is created as ``feat-<feature_id>``.",
    )
    name: str = Field(..., min_length=1, description="What the feature is called, as shown.")
    description: str | None = Field(default=None, description="What the feature is for.")
    icon_name: str | None = Field(default=None, description="Icon name from the icon set.")
    color: str | None = Field(default=None, description="Accent colour of the feature.")
    default_model: str | None = Field(
        default=None,
        description="Model the feature's agent runs with, as ``provider:model``.",
    )


@router.post("", status_code=201, summary="Create feature")
async def create_feature(
    body: FeatureCreateBody,
    user: Any = Depends(current_user),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Create a feature: its agent, owned by the caller, marked as a feature's.

    The answer is the creation's receipt, not a second copy of the agent payload:
    the feature is now an agent row, and every read of it — the card, the panels,
    the state — comes from ``GET /api/agents`` (``kind`` included) like any other.
    """
    row = await create_feature_agent(
        server,
        feature_id=body.feature_id,
        author_user_id=user.id,
        name=body.name,
        description=body.description,
        icon_name=body.icon_name,
        color=body.color,
        default_model=body.default_model,
    )
    return {
        "agent_id": row.agent_id,
        "kind": row.kind,
        "name": row.name,
        "state": row.last_state or "unknown",
    }


__all__ = ["FeatureCreateBody", "create_feature", "router"]
