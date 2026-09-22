"""Keep one agent's model-visible skill catalog in step with its workspace.

deepagents scans an agent's skill sources **once per thread**: the thread's first
turn fills ``skills_metadata`` and every later turn reuses it
(``SkillsMiddleware.before_agent`` returns early while the key is present). A skill
that appears afterwards — installed from the dashboard, the ``/skills`` API, a
mounted skill package — is therefore invisible to that thread's model for the rest
of the thread: the harness's ``SkillFilterMiddleware`` calls the name unknown and
drops it, while :meth:`AgentManager.list_skill_summaries` (the live filesystem, the
list ``/features`` resolves a run's scope against) reports it as available.

This middleware closes that gap. Octop records every write to an agent's catalog as
a revision (:meth:`AgentRegistry.note_skill_catalog_changed`); a thread records the
revision its state was scanned under; when the two differ, the next turn re-runs the
harness's own scan and writes the fresh metadata into the state *before* the model
call, so the prompt section and the per-turn allow-list both see it.

Only a revision mismatch triggers the scan — the catalog is never polled. A skill
changed outside Octop (the agent editing its own ``skills/`` with its filesystem
tools) keeps the harness's once-per-session behaviour.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from typing import TYPE_CHECKING, Annotated, Any, NotRequired, Protocol, cast

from deepagents.middleware.skills import SkillsMiddleware, SkillsState
from langchain.agents.middleware import AgentMiddleware
from langchain.agents.middleware.types import PrivateStateAttr

from octop.infra.errors import OctopError

if TYPE_CHECKING:
    from harness_agent import HarnessAgent
    from langchain_core.runnables import RunnableConfig

logger = logging.getLogger(__name__)

SKILLS_METADATA_KEY = "skills_metadata"
"""Thread state field deepagents scans the skill sources into (once per thread)."""

SKILL_CATALOG_REVISION_KEY = "skill_catalog_revision"
"""Thread state field recording the revision this thread's skills were scanned under."""


class SkillCatalogRegistry(Protocol):
    """What this middleware asks of the caller's agent registry."""

    def get_agent(self, agent_id: str) -> HarnessAgent: ...

    def skill_catalog_revision(self, agent_id: str) -> int: ...


class SkillCatalogState(SkillsState):
    """deepagents' skills state plus the revision it was scanned under."""

    skill_catalog_revision: NotRequired[Annotated[int, PrivateStateAttr]]


def _stamped(update: Mapping[str, Any] | None, revision: int) -> dict[str, Any]:
    """The scan's state update, carrying the revision it was taken under."""
    return {**(update or {}), SKILL_CATALOG_REVISION_KEY: revision}


class SkillCatalogRefreshMiddleware(AgentMiddleware[Any, Any, Any]):
    """Rescan one agent's skill sources when the platform moved its catalog.

    Rides the graph alongside the harness's ``SkillsMiddleware`` and never renders
    the skills section itself (the scanner below is built with no system prompt):
    the harness's own instance keeps rendering it, from the state this middleware
    refreshes. That keeps this middleware out of the prompt's byte layout — an
    unchanged catalog leaves the request untouched — and off the harness's scan
    code, which it calls rather than copies.
    """

    state_schema = SkillCatalogState

    def __init__(self, *, registry: SkillCatalogRegistry, agent_id: str) -> None:
        self._registry = registry
        self._agent_id = agent_id

    def before_agent(
        self,
        state: Any,
        runtime: Any,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any] | None:
        revision = self._stale_revision(state)
        if revision is None:
            return None
        if SKILLS_METADATA_KEY not in state:
            # First turn on this thread: the harness's own scan fills the metadata
            # on this same turn, so this one only records the revision it came from.
            return {SKILL_CATALOG_REVISION_KEY: revision}
        scanner = self._scanner()
        if scanner is None:
            return None
        # ``config`` is the run's when langchain passes it on; the scan never reads it.
        scanned = scanner.before_agent(self._state_to_scan(state), runtime, config or {})
        return _stamped(scanned, revision)

    async def abefore_agent(
        self,
        state: Any,
        runtime: Any,
        config: RunnableConfig | None = None,
    ) -> dict[str, Any] | None:
        revision = self._stale_revision(state)
        if revision is None:
            return None
        if SKILLS_METADATA_KEY not in state:
            return {SKILL_CATALOG_REVISION_KEY: revision}
        scanner = self._scanner()
        if scanner is None:
            return None
        scanned = await scanner.abefore_agent(self._state_to_scan(state), runtime, config or {})
        return _stamped(scanned, revision)

    def _stale_revision(self, state: Mapping[str, Any]) -> int | None:
        """The revision to refresh to, or ``None`` when this thread is current."""
        revision = self._registry.skill_catalog_revision(self._agent_id)
        return None if state.get(SKILL_CATALOG_REVISION_KEY) == revision else revision

    @staticmethod
    def _state_to_scan(state: Mapping[str, Any]) -> SkillsState:
        """The thread's state without its catalog — what makes the harness rescan.

        The scan skips any state that already carries ``skills_metadata`` (that is
        the behaviour this middleware works around), so the key is dropped here and
        the rest of the state is handed over untouched.
        """
        return cast(
            "SkillsState",
            {key: value for key, value in state.items() if key != SKILLS_METADATA_KEY},
        )

    def _scanner(self) -> SkillsMiddleware | None:
        """A scanner over this agent's live skill sources, or ``None`` when unloaded.

        Mirrors how the harness builds its own middleware — the same backend and the
        same ``skill_paths`` — so the refresh sees exactly the sources the graph was
        compiled with.
        """
        try:
            agent = self._registry.get_agent(self._agent_id)
        except (KeyError, OctopError):
            logger.warning(
                "skill catalog refresh: agent %s is not loaded; keeping %s's current skills",
                self._agent_id,
                SKILLS_METADATA_KEY,
                exc_info=True,
            )
            return None
        workspace = agent.workspace
        return SkillsMiddleware(
            backend=workspace.backend,
            sources=workspace.skill_paths(extra=agent.config.skills_dir),
            system_prompt=None,
        )


__all__ = [
    "SKILL_CATALOG_REVISION_KEY",
    "SKILLS_METADATA_KEY",
    "SkillCatalogRefreshMiddleware",
    "SkillCatalogRegistry",
    "SkillCatalogState",
]
