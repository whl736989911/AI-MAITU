"""Resolving one feature's capability layer against the caller who runs it.

Design 5.2 splits a feature's world in two: configuration (model, tools, skills,
subagents) is authored once and identical for everyone, while data (knowledge
bases, connector credentials) follows the caller. The declared scope is therefore
never applied verbatim — it is intersected with what the caller can reach, and
every entry that could not be used is reported rather than dropped in silence.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from octop.infra.features.capability import (
    CapabilityUnavailable,
    ResolvedCapability,
    resolve_capability,
    stamp_capability,
)
from octop.infra.features.catalog import FeatureAgent


class _Providers:
    def __init__(self, usable: set[str]) -> None:
        self._usable = usable
        self.asked: list[str] = []

    def is_model_ref_usable(self, ref: str) -> bool:
        self.asked.append(ref)
        return ref in self._usable


class _Registry:
    """The caller's agent, reduced to what capability resolution asks of it."""

    def __init__(
        self,
        *,
        skills: list[dict[str, Any]] = (),
        subagents: list[dict[str, Any]] = (),
        usable_models: set[str] | None = None,
    ) -> None:
        self.providers = _Providers(usable_models or set())
        self._skills = list(skills)
        self._subagents = list(subagents)

    async def list_skill_summaries(self, agent_id: str) -> list[dict[str, Any]]:
        return self._skills

    async def list_subagent_summaries(self, agent_id: str) -> list[dict[str, Any]]:
        return self._subagents


class _KnowledgeRepo:
    def __init__(self, visible: list[str]) -> None:
        self._visible = list(visible)

    def list_visible(self, user_id: int) -> list[Any]:
        return [SimpleNamespace(id=kb_id, name=kb_id) for kb_id in self._visible]


def _server(
    *,
    skills: list[dict[str, Any]] = (),
    subagents: list[dict[str, Any]] = (),
    usable_models: set[str] | None = None,
    visible_kbs: list[str] = (),
) -> Any:
    """A server stand-in: only the lookups capability resolution performs.

    The connector repositories are placeholders — the tests stub
    ``ConnectorService`` itself, which is what consumes them.
    """
    return SimpleNamespace(
        app_runtime=SimpleNamespace(
            agent_registry=_Registry(
                skills=skills, subagents=subagents, usable_models=usable_models
            )
        ),
        services=SimpleNamespace(
            repos=SimpleNamespace(
                knowledge_repo=_KnowledgeRepo(list(visible_kbs)),
                connector_repo=None,
            ),
            secret_repo=None,
            settings_repo=None,
            config=None,
        ),
    )


@pytest.fixture
def connectors(monkeypatch: pytest.MonkeyPatch) -> set[str]:
    """Connector names the caller has instances of, stubbed at the service.

    Returns the live set, so a test can change what the caller owns.
    """
    names: set[str] = set()

    class _StubConnectorService:
        def __init__(self, **_kwargs: Any) -> None: ...

        def list_active_mcp_server_names(self, user_id: int) -> list[str]:
            return sorted(names)

    monkeypatch.setattr("octop.infra.connectors.service.ConnectorService", _StubConnectorService)
    return names


async def _resolve(server: Any, layer: Any, *, user_id: int = 7) -> ResolvedCapability | None:
    return await resolve_capability(
        server,
        layer,
        agent_id="agent-1",
        user=SimpleNamespace(id=user_id, is_admin=False),
    )


async def test_a_feature_that_declares_nothing_resolves_to_none() -> None:
    """``None`` is what keeps an untouched feature running exactly as it did."""
    server = _server()

    assert await _resolve(server, None) is None


async def test_stamping_nothing_leaves_the_request_untouched() -> None:
    request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}

    stamp_capability(request, ResolvedCapability())

    assert request == {"configurable": {"session_key": "dashboard:ag:1"}}


async def test_a_declared_model_is_checked_never_substituted() -> None:
    """A feature runs on its own model or not at all — no silent fallback."""
    server = _server(usable_models={"openai/gpt-4o"})

    with pytest.raises(CapabilityUnavailable) as refusal:
        await _resolve(server, FeatureAgent(model="groq/llama-3.1-70b"))

    assert "groq/llama-3.1-70b" in str(refusal.value)
    assert server.app_runtime.agent_registry.providers.asked == ["groq/llama-3.1-70b"]

    resolved = await _resolve(server, FeatureAgent(model="openai/gpt-4o"))
    assert resolved is not None
    assert resolved.model == "openai/gpt-4o"


async def test_scope_is_intersected_with_what_the_caller_has(connectors: set[str]) -> None:
    """Declared-but-unreachable entries are withheld and named, never silent."""
    connectors.add("github")
    server = _server(
        skills=[{"name": "meeting-notes", "enabled": True}, {"name": "off", "enabled": False}],
        subagents=[{"name": "researcher"}],
        usable_models={"openai/gpt-4o"},
        visible_kbs=["kb-mine"],
    )

    resolved = await _resolve(
        server,
        FeatureAgent(
            model="openai/gpt-4o",
            skills=("meeting-notes", "off", "ghost"),
            subagents=("researcher", "writer"),
            mcp_servers=("github", "slack"),
            knowledge_base_ids=("kb-mine", "kb-theirs"),
        ),
    )

    assert resolved is not None
    assert resolved.skills == ("meeting-notes",)
    assert resolved.subagents == ("researcher",)
    assert resolved.mcp_servers == ("github",)
    assert resolved.knowledge_base_ids == ("kb-mine",)
    assert resolved.withheld == (
        "skill 'off' is not available to the caller",
        "skill 'ghost' is not available to the caller",
        "subagent 'writer' is not available to the caller",
        "connector 'slack' is not available to the caller",
        "knowledge base 'kb-theirs' is not available to the caller",
    )


async def test_an_undeclared_dimension_stays_none_but_a_declared_empty_one_is_a_scope(
    connectors: set[str],
) -> None:
    """The editor has to tell "inherit" from "explicitly none"."""
    server = _server(usable_models={"openai/gpt-4o"})

    inherited = await _resolve(server, FeatureAgent(model="openai/gpt-4o"))
    none_at_all = await _resolve(
        server, FeatureAgent(skills=(), subagents=(), mcp_servers=(), knowledge_base_ids=())
    )

    assert inherited is not None
    assert (inherited.skills, inherited.subagents, inherited.mcp_servers) == (None, None, None)
    assert inherited.knowledge_base_ids == ()
    assert none_at_all is not None
    assert (none_at_all.skills, none_at_all.subagents, none_at_all.mcp_servers) == ((), (), ())
    assert none_at_all.knowledge_base_ids == ()


async def test_stamping_writes_the_model_the_knobs_the_skills_and_the_scope() -> None:
    """Each channel the run needs, on the request that carries it."""
    capability = ResolvedCapability(
        model="openai/gpt-4o",
        runtime_overrides={"temperature": 0.3, "max_tokens": 1024},
        tools_disabled=("browser_use",),
        skills=("meeting-notes",),
        subagents=(),
    )
    request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}

    stamp_capability(request, capability)

    assert request["model"] == "openai/gpt-4o"
    assert request["configurable"]["skills"] == ["meeting-notes"]
    assert request["configurable"]["octop_agent_runtime_overrides"] == {
        "temperature": 0.3,
        "max_tokens": 1024,
    }
    assert request["configurable"]["octop_feature_scope"] == {
        "tools_disabled": ["browser_use"],
        "subagents": [],
    }
    # The rest of the session's own configurable survives the stamp.
    assert request["configurable"]["session_key"] == "dashboard:ag:1"


async def test_audit_reports_the_effective_scope() -> None:
    capability = ResolvedCapability(
        model="openai/gpt-4o",
        runtime_overrides={"temperature": 0.3},
        skills=(),
        withheld=("knowledge base 'kb-theirs' is not available to the caller",),
    )

    assert capability.audit() == {
        "model": "openai/gpt-4o",
        "runtime": {"temperature": 0.3},
        "tools_disabled": [],
        "skills": [],
        "subagents": None,
        "mcp_servers": None,
        "knowledge_base_ids": None,
        "withheld": ["knowledge base 'kb-theirs' is not available to the caller"],
    }
