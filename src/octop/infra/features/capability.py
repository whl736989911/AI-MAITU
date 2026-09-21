"""Resolve a feature's declared capability layer against the caller who runs it.

Design 5.2 splits one feature's world in two:

* **Configuration** — the model, the tools, the skills, the subagents, the
  persona - is authored once for everyone and must be identical for every caller.
* **Data** — knowledge bases, connector credentials, whatever the caller's own
  policy allows — follows the caller, never the feature's author.

So the declared scope is not applied verbatim: it is *intersected* with what the
caller may reach (``knowledge_base_ids`` against
``knowledge_repo.list_visible(caller)``, ``mcp_servers`` against the caller's own
connectors, skills and subagents against the caller's agent), and every entry the
caller cannot use is logged rather than dropped in silence.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from octop.infra.agents.runtime_limits import CONFIGURABLE_AGENT_RUNTIME_OVERRIDES

if TYPE_CHECKING:
    from octop.infra.agents.middleware.feature_scope import FeatureRunScope

logger = logging.getLogger(__name__)


class CapabilityUnavailable(RuntimeError):
    """A declared capability the platform cannot honour at all (never a fallback)."""


@dataclass(frozen=True)
class ResolvedCapability:
    """One feature's capability layer as it applies to one caller.

    ``None`` keeps meaning "the feature declares nothing here", which for skills
    and subagents is "the caller's agent decides" and for knowledge bases and
    connectors is "this run mounts none" — the behaviour a feature had before the
    capability layer existed, so an untouched feature runs exactly as it did.
    """

    model: str | None = None
    runtime_overrides: Mapping[str, Any] = field(default_factory=dict)
    tools_disabled: tuple[str, ...] = ()
    skills: tuple[str, ...] | None = None
    subagents: tuple[str, ...] | None = None
    mcp_servers: tuple[str, ...] | None = None
    knowledge_base_ids: tuple[str, ...] | None = None
    withheld: tuple[str, ...] = ()

    def run_scope(self) -> FeatureRunScope:
        """The part of the layer that rides ``configurable`` (tools, subagents)."""
        from octop.infra.agents.middleware.feature_scope import (  # noqa: PLC0415
            FeatureRunScope,
        )

        return FeatureRunScope(
            tools_disabled=self.tools_disabled,
            subagents=self.subagents,
        )

    def audit(self) -> dict[str, Any]:
        """The run's effective scope, as logged and reported for one run."""
        return {
            "model": self.model,
            "runtime": dict(self.runtime_overrides),
            "tools_disabled": list(self.tools_disabled),
            "skills": None if self.skills is None else list(self.skills),
            "subagents": None if self.subagents is None else list(self.subagents),
            "mcp_servers": None if self.mcp_servers is None else list(self.mcp_servers),
            "knowledge_base_ids": (
                None if self.knowledge_base_ids is None else list(self.knowledge_base_ids)
            ),
            "withheld": list(self.withheld),
        }


def _intersect(
    declared: tuple[str, ...],
    available: set[str],
    *,
    what: str,
    withheld: list[str],
) -> tuple[str, ...]:
    """Declared entries the caller may actually use, in declaration order."""
    kept: list[str] = []
    for name in declared:
        if name in available:
            kept.append(name)
        else:
            withheld.append(f"{what} {name!r} is not available to the caller")
    return tuple(kept)


async def _available_skills(server: Any, agent_id: str) -> set[str]:
    """Skill names and slugs the caller's agent has, enabled ones only."""
    names: set[str] = set()
    for summary in await server.app_runtime.agent_registry.list_skill_summaries(agent_id):
        if not summary.get("enabled"):
            continue
        names.add(str(summary.get("name") or ""))
        slug = summary.get("slug")
        if slug:
            names.add(str(slug))
    names.discard("")
    return names


async def _available_subagents(server: Any, agent_id: str) -> set[str]:
    """Subagent types the caller's agent can dispatch."""
    summaries = await server.app_runtime.agent_registry.list_subagent_summaries(agent_id)
    return {str(row.get("name") or "") for row in summaries} - {""}


def _usable_model(server: Any, declared: str) -> str:
    """The declared model, or a loud failure — a feature never runs on another one."""
    if not server.app_runtime.agent_registry.providers.is_model_ref_usable(declared):
        raise CapabilityUnavailable(
            f"feature declares model {declared!r}, which is not an enabled model on this "
            "instance; enable it or point the feature at a model that is"
        )
    return declared


async def resolve_capability(
    server: Any,
    declared: Any,
    *,
    agent_id: str,
    user: Any,
) -> ResolvedCapability | None:
    """Apply one feature's declared capability layer to one caller.

    *declared* is the definition's :class:`~octop.infra.features.catalog.FeatureAgent`.
    ``None`` — a feature that predates this layer — resolves to ``None``, and the
    run stamps nothing at all: no key is added to its request, so it stays
    byte-identical to the run it had before the capability layer existed.
    """
    if declared is None:
        return None

    withheld: list[str] = []
    model = _usable_model(server, declared.model) if declared.model else None

    skills: tuple[str, ...] | None = None
    if declared.skills is not None:
        skills = _intersect(
            declared.skills,
            await _available_skills(server, agent_id),
            what="skill",
            withheld=withheld,
        )

    subagents: tuple[str, ...] | None = None
    if declared.subagents is not None:
        subagents = _intersect(
            declared.subagents,
            await _available_subagents(server, agent_id),
            what="subagent",
            withheld=withheld,
        )

    mcp_servers: tuple[str, ...] | None = None
    if declared.mcp_servers is not None:
        from octop.infra.connectors.service import ConnectorService  # noqa: PLC0415

        svc = ConnectorService(
            repo=server.services.repos.connector_repo,
            secret_repo=server.services.secret_repo,
            settings_repo=server.services.settings_repo,
            config=server.services.config,
        )
        mcp_servers = _intersect(
            declared.mcp_servers,
            set(svc.list_active_mcp_server_names(int(user.id))),
            what="connector",
            withheld=withheld,
        )

    knowledge_base_ids: tuple[str, ...] = ()
    if declared.knowledge_base_ids is not None:
        visible = {
            str(base.id)
            for base in server.services.repos.knowledge_repo.list_visible(int(user.id))
        }
        knowledge_base_ids = _intersect(
            declared.knowledge_base_ids,
            visible,
            what="knowledge base",
            withheld=withheld,
        )

    if withheld:
        logger.warning(
            "feature capability: user %s cannot use %d declared entr%s (%s)",
            user.id,
            len(withheld),
            "y" if len(withheld) == 1 else "ies",
            "; ".join(withheld),
        )

    return ResolvedCapability(
        model=model,
        runtime_overrides=declared.runtime_values(),
        tools_disabled=declared.tools_disabled or (),
        skills=skills,
        subagents=subagents,
        mcp_servers=mcp_servers,
        knowledge_base_ids=knowledge_base_ids,
        withheld=tuple(withheld),
    )


def stamp_capability(request: dict[str, Any], capability: ResolvedCapability) -> None:
    """Put the resolved capability onto one harness request, in place.

    Only the channels that need a request key are set here: the model
    (``request["model"]``), the runtime knobs (``configurable``), the skills
    allow-list (``configurable["skills"]``, harness ``SkillFilterMiddleware``) and
    the tool / subagent scope (``FeatureScopeMiddleware``). MCP connectors and
    knowledge bases need lookups of their own — the router resolves those, because
    it is also what reports them in the run log.
    """
    from octop.infra.agents.middleware.feature_scope import stamp_feature_scope  # noqa: PLC0415

    stamp_feature_scope(request, capability.run_scope())
    configurable = dict(request.get("configurable") or {})
    if capability.runtime_overrides:
        configurable[CONFIGURABLE_AGENT_RUNTIME_OVERRIDES] = dict(capability.runtime_overrides)
    if capability.model:
        request["model"] = capability.model
    if capability.skills is not None:
        configurable["skills"] = list(capability.skills)
    if configurable:
        request["configurable"] = configurable


__all__ = [
    "CapabilityUnavailable",
    "ResolvedCapability",
    "resolve_capability",
    "stamp_capability",
]
