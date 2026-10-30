"""A protocol retry must never replay output already exposed to the caller."""

from __future__ import annotations

from collections.abc import AsyncIterator
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from harness_agent.config import ModelConfig
from harness_agent.llm.factory import _inject_model_token_limits
from harness_agent.middleware.model_settings import apply_configurable_model_settings
from langchain.agents.middleware import ModelRequest
from langchain_core.callbacks import CallbackManager
from langchain_core.messages import AIMessage, AIMessageChunk
from langchain_core.runnables import RunnableLambda
from langchain_core.runnables.config import RunnableConfig
from langchain_openai import ChatOpenAI
from openai import BadRequestError

from octop.infra.agents.providers.harness_factory import (
    _ProtocolFallbackRunnable,
    _ResponsesFallbackChatModel,
)


def test_fallback_wrapper_preserves_consumer_visible_token_budgets() -> None:
    primary = ChatOpenAI(model="test-model", api_key="test-key")
    fallback = ChatOpenAI(model="test-model", api_key="test-key")
    _inject_model_token_limits(
        primary,
        ModelConfig(
            id="test-model",
            max_input_tokens=6000,
            context_window=8000,
            max_output_tokens=2000,
        ),
    )
    model = _ResponsesFallbackChatModel(primary, fallback)

    first_request = ModelRequest(
        model=model,
        messages=[],
        model_settings={"max_tokens": 2500},
        runtime=SimpleNamespace(configurable={"max_input_tokens": 0}),
    )
    apply_configurable_model_settings(first_request)
    assert model.profile["max_input_tokens"] == 5500

    second_request = ModelRequest(
        model=model,
        messages=[],
        model_settings={"max_tokens": 1000},
        runtime=SimpleNamespace(configurable={"max_input_tokens": 0}),
    )
    apply_configurable_model_settings(second_request)
    assert model.profile["max_input_tokens"] == 6000
    assert primary.profile["max_input_tokens"] == 6000


def _protocol_error() -> BadRequestError:
    response = httpx.Response(400, request=httpx.Request("POST", "https://example.test/responses"))
    return BadRequestError("Responses API is unsupported", response=response, body=None)


@pytest.mark.asyncio
async def test_partial_stream_is_not_replayed_on_late_protocol_error() -> None:
    fallback_calls: list[str] = []

    async def broken(_input: Any) -> AsyncIterator[AIMessageChunk]:
        yield AIMessageChunk(content="Already visible")
        raise _protocol_error()

    def fallback(_input: Any) -> AIMessage:
        fallback_calls.append("called")
        return AIMessage(content="Replayed answer")

    model = _ProtocolFallbackRunnable(RunnableLambda(broken), RunnableLambda(fallback))
    stream = model.astream("question")
    assert (await anext(stream)).text == "Already visible"
    with pytest.raises(BadRequestError):
        await anext(stream)
    assert fallback_calls == []


def test_callback_output_also_prevents_invoke_replay() -> None:
    fallback_calls: list[str] = []

    def broken(_input: Any, config: RunnableConfig) -> AIMessage:
        callbacks = CallbackManager.configure(config.get("callbacks"))
        run = callbacks.on_llm_start({"name": "primary"}, ["question"])[0]
        run.on_llm_new_token("Already visible")
        raise _protocol_error()

    def fallback(_input: Any) -> AIMessage:
        fallback_calls.append("called")
        return AIMessage(content="Replayed answer")

    model = _ProtocolFallbackRunnable(RunnableLambda(broken), RunnableLambda(fallback))
    with pytest.raises(BadRequestError):
        model.invoke("question")
    assert fallback_calls == []
