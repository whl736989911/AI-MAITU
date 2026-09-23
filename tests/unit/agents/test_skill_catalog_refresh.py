"""The model-visible skill catalog follows the workspace, not the thread's first scan.

deepagents scans an agent's skill sources once per thread and reuses what it found
for the rest of that thread, while Octop's own listing (``/skills``, the run scope
of a feature) reads the filesystem live. A skill installed mid-thread was therefore
reported as available by the platform and as unknown by the harness: the run stamped
it into the allow-list, the harness dropped it, and the model never saw it.

:class:`SkillCatalogRefreshMiddleware` closes that gap off a revision the platform
moves whenever it writes to an agent's catalog — no per-turn disk scan.
"""

from __future__ import annotations

import itertools
import json
import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.filesystem import FilesystemBackend
from deepagents.middleware.skills import SkillsMiddleware
from harness_agent import HarnessAgentManager
from harness_agent.llm.factory import ChatModelFactory
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.messages import AIMessage

from octop.config import OctopConfig
from octop.infra.agents.manager import AgentCreateSpec, AgentManager
from octop.infra.agents.middleware.skill_catalog import (
    SKILL_CATALOG_REVISION_KEY,
    SKILLS_METADATA_KEY,
    SkillCatalogRefreshMiddleware,
)
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import build_shared_services
from octop.infra.utils.paths import PathLayout

# Memory maintenance runs on a daemon thread that races SQLite on close (Windows);
# nothing here needs memories.
_MEMORY_OFF: dict[str, Any] = {"memory": {"memory_enabled": False}}

_BUILTIN_SKILL = "---\nname: web-search\ndescription: Built in\n---\n# Web search\n"

_SIGIL_SKILL = "---\nname: s4-sigil\ndescription: Answers with the sigil line\n---\n# Sigil\n"


def _install(root: Path, slug: str, manifest: str) -> None:
    """Write one skill the way the workspace backend does: ``skills/<slug>/SKILL.md``."""
    skill_dir = root / "skills" / slug
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(manifest, encoding="utf-8")


class _Workspace:
    """The part of a harness workspace the middleware reads."""

    def __init__(self, root: Path) -> None:
        self.backend = FilesystemBackend(root_dir=root, virtual_mode=False)

    def skill_paths(self, *, extra: Any = None) -> list[str]:
        del extra
        return ["skills"]


class _Registry:
    """One agent's catalog, reduced to what the middleware asks the registry for."""

    def __init__(self, root: Path, *, revision: int = 7) -> None:
        self.revision = revision
        self.lookups = 0
        self._agent = SimpleNamespace(
            workspace=_Workspace(root),
            config=SimpleNamespace(skills_dir=None),
        )

    def get_agent(self, agent_id: str) -> Any:
        self.lookups += 1
        return self._agent

    def skill_catalog_revision(self, agent_id: str) -> int:
        return self.revision


def _scanned(names: list[str]) -> dict[str, Any]:
    """A thread state as the harness leaves it after its own scan."""
    return {
        SKILLS_METADATA_KEY: [{"name": name, "path": f"skills/{name}/SKILL.md"} for name in names],
        SKILL_CATALOG_REVISION_KEY: 7,
    }


def _names(update: dict[str, Any]) -> set[str]:
    return {str(skill["name"]) for skill in update[SKILLS_METADATA_KEY]}


def test_the_harness_keeps_the_catalog_it_scanned_first(tmp_path: Path) -> None:
    """What this middleware exists for: a thread never rescans its sources.

    If the harness ever starts rescanning on its own, this test failing is the
    signal that the workaround below can go.
    """
    _install(tmp_path, "web-search", _BUILTIN_SKILL)
    scanner = SkillsMiddleware(backend=_Workspace(tmp_path).backend, sources=["skills"])
    first = scanner.before_agent({}, None, None)
    assert first is not None
    assert _names(first) == {"web-search"}

    _install(tmp_path, "s4-sigil", _SIGIL_SKILL)

    assert scanner.before_agent(dict(first), None, None) is None


def test_a_catalog_that_did_not_move_is_never_scanned_again(tmp_path: Path) -> None:
    """A turn on an up-to-date thread must not touch the workspace at all."""
    registry = _Registry(tmp_path, revision=7)
    middleware = SkillCatalogRefreshMiddleware(registry=registry, agent_id="A1")

    assert middleware.before_agent(_scanned(["web-search"]), None) is None
    assert registry.lookups == 0


def test_a_thread_that_never_scanned_leaves_the_scan_to_the_harness(tmp_path: Path) -> None:
    """First turn: the harness's own middleware scans that same turn."""
    registry = _Registry(tmp_path, revision=7)
    middleware = SkillCatalogRefreshMiddleware(registry=registry, agent_id="A1")

    assert middleware.before_agent({}, None) == {SKILL_CATALOG_REVISION_KEY: 7}
    assert registry.lookups == 0


async def test_a_skill_installed_after_the_first_turn_reaches_the_next_turn(
    tmp_path: Path,
) -> None:
    """The refresh itself: the moved revision brings the new skill into the state."""
    _install(tmp_path, "web-search", _BUILTIN_SKILL)
    registry = _Registry(tmp_path, revision=7)
    middleware = SkillCatalogRefreshMiddleware(registry=registry, agent_id="A1")

    _install(tmp_path, "s4-sigil", _SIGIL_SKILL)
    registry.revision += 1  # what note_skill_catalog_changed does after a write

    update = await middleware.abefore_agent(_scanned(["web-search"]), None)

    assert update is not None
    assert _names(update) == {"web-search", "s4-sigil"}
    assert update[SKILL_CATALOG_REVISION_KEY] == 8


class _FakeToolModel(GenericFakeChatModel):
    """A fake model that accepts tool binding — ``create_agent`` binds tools to it."""

    def bind_tools(self, tools: Any, **kwargs: Any) -> Any:
        del tools, kwargs
        return self


class _FakeChatModelFactory(ChatModelFactory):
    """A real model factory whose models never leave the process."""

    def _build(self, provider: Any, model: Any) -> Any:
        del provider, model
        return _FakeToolModel(messages=itertools.cycle([AIMessage(content="pong")]))


def _manager_with_fake_model(tmp_path: Path) -> AgentManager:
    """A real :class:`AgentManager` and a real harness that never calls a provider."""
    paths = PathLayout(tmp_path / ".octop")
    paths.ensure_root()
    db = SqlitePool(paths.db)
    run_migrations(db)
    services = build_shared_services(db=db, paths=paths, config=OctopConfig())
    services.repos.provider_repo.create(
        name="fake",
        kind="openai",
        base_url="http://127.0.0.1:9/v1",
        api_key="not-used",
        models_json=json.dumps([{"id": "fake-model", "name": "fake", "enabled": True}]),
    )
    manager = AgentManager(repos=services.repos, paths=services.paths)
    harness = HarnessAgentManager(
        providers=manager.providers.build_harness_configs(),
        log_dir=str(manager.paths.logs_dir),
    )
    shared = harness.shared_factory
    assert shared is not None
    harness._shared_factory = _FakeChatModelFactory(shared.provider_configs())
    manager._harness_manager = harness
    return manager


async def _served_skills(agent: Any, thread_id: str) -> set[str]:
    """The catalog the harness serves *thread_id* — what the prompt section renders."""
    checkpoint = await agent.checkpointer.aget_tuple({"configurable": {"thread_id": thread_id}})
    assert checkpoint is not None
    values = dict(checkpoint.checkpoint.get("channel_values") or {})
    return {str(skill["name"]) for skill in values.get(SKILLS_METADATA_KEY) or []}


async def test_a_skill_installed_mid_thread_is_visible_to_that_threads_next_turn(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """End to end, on a real graph: install, then the same thread's next turn."""
    manager = _manager_with_fake_model(tmp_path)
    filter_log = logging.getLogger("harness_agent.middleware.skill_filter")
    try:
        row = await manager.create(
            AgentCreateSpec(
                name="skills",
                default_model="fake/fake-model",
                system_prompt="Answer in one word.",
                config=_MEMORY_OFF,
            ),
        )
        agent = manager.get_agent(row.agent_id)
        await agent.call({"messages": "ping", "thread_id": "t1"})
        assert "s4-sigil" not in await _served_skills(agent, "t1")

        await agent.workspace.aupload_many(
            [("skills/s4-sigil/SKILL.md", _SIGIL_SKILL.encode("utf-8"))],
        )
        # What POST /agents/{id}/skills does right after writing the files.
        manager.note_skill_catalog_changed(row.agent_id)

        with caplog.at_level(logging.WARNING, logger=filter_log.name):
            await agent.call({"messages": "ping", "thread_id": "t1", "skills": ["s4-sigil"]})

        assert "s4-sigil" in await _served_skills(agent, "t1")
        unknown = [record for record in caplog.records if "unknown skill" in record.getMessage()]
        assert unknown == []
    finally:
        # ``stop`` closes the agent's backend and checkpointer; closing the harness
        # manager with a live aiosqlite connection trips pytest's thread check.
        await manager.stop(row.agent_id)
        assert manager._harness_manager is not None
        manager._harness_manager.close()
