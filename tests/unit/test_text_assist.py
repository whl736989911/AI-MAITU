"""Regression tests for shared form/chat text assistance model resolution."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from langchain_core.messages import AIMessage

from octop.infra import text_assist
from octop.infra.errors import OctopError


class _FakeLLM:
    def __init__(self) -> None:
        self.messages = []

    async def ainvoke(self, messages: list[object]) -> AIMessage:
        self.messages = messages
        return AIMessage(content="<think>hidden</think>Edited text")


class _FakeFactory:
    def __init__(self, llm: _FakeLLM) -> None:
        self.llm = llm
        self.refs: list[str] = []

    def get_chat_model(self, model_ref: str) -> _FakeLLM:
        self.refs.append(model_ref)
        return self.llm


def _server(*, usable: set[str], fallback: str, agent_model: str = ""):
    llm = _FakeLLM()
    factory = _FakeFactory(llm)
    registry = SimpleNamespace(
        providers=SimpleNamespace(is_model_ref_usable=lambda ref: ref in usable),
        harness_manager=SimpleNamespace(shared_factory=factory),
        resolve_fallback_model_ref=lambda: fallback,
        get_agent=lambda _agent_id: SimpleNamespace(
            config=SimpleNamespace(pick_default_model_ref=lambda: agent_model)
        ),
    )
    server = SimpleNamespace(
        app_runtime=SimpleNamespace(agent_registry=registry),
    )
    return server, factory, llm


@pytest.mark.asyncio
async def test_form_assist_uses_registry_fallback_and_strips_thinking() -> None:
    server, factory, llm = _server(usable={"openai/gpt"}, fallback="openai/gpt")
    result = await text_assist.run_text_assist(
        server=server,
        kind="team.description",
        text="draft",
        context={"name": "Research Team"},
        locale="en",
    )
    assert result == "Edited text"
    assert factory.refs == ["openai/gpt"]
    assert "Research Team" in str(llm.messages[1].content)


@pytest.mark.asyncio
async def test_chat_assist_keeps_agent_default_model_scope() -> None:
    server, factory, llm = _server(
        usable={"provider/agent-model", "provider/other"},
        fallback="provider/other",
        agent_model="provider/agent-model",
    )
    result = await text_assist.run_text_assist(
        server=server,
        kind="chat.prompt",
        text="draft prompt",
        agent_id="agent-a",
        locale="en",
    )
    assert result == "Edited text"
    assert factory.refs == ["provider/agent-model"]
    assert "draft prompt" in str(llm.messages[1].content)


@pytest.mark.asyncio
async def test_assist_rejects_unusable_explicit_model() -> None:
    server, factory, _llm = _server(usable={"provider/usable"}, fallback="provider/usable")
    with pytest.raises(OctopError):
        await text_assist.run_text_assist(
            server=server,
            kind="knowledge_base.description",
            text="draft",
            context={"name": "Docs"},
            default_model="provider/invalid",
        )
    assert factory.refs == []
