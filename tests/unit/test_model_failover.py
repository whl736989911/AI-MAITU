"""Request-level model routing behavior without provider credentials or network calls."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any
from unittest.mock import patch

from langchain.agents.middleware import ModelResponse, ModelRetryMiddleware
from langchain_core.exceptions import ModelError
from langchain_core.messages import AIMessage

from octop.infra.agents.middleware.model_failover import (
    AutoModelFailoverMiddleware,
    current_failover_model_ref,
)
from octop.infra.agents.providers.health import ModelHealthRegistry
from octop.infra.errors import ErrorCode, OctopError


@dataclass
class _Request:
    model: Any = "original"

    def override(self, **values: Any) -> _Request:
        return _Request(model=values.get("model", self.model))


@dataclass
class _Model:
    ref: str
    callbacks: list[Any] = field(default_factory=list)

    def model_copy(self, *, update: dict[str, Any]) -> _Model:
        return _Model(**{**self.__dict__, **update})


class _Factory:
    def get_chat_model(self, ref: str) -> _Model:
        return _Model(ref)


class _ServiceError(Exception):
    status_code = 503


def _middleware(health: ModelHealthRegistry | None = None) -> AutoModelFailoverMiddleware:
    return AutoModelFailoverMiddleware(
        factory=_Factory(),
        health=health or ModelHealthRegistry(cooldown_seconds=30),
        candidate_refs=lambda vision: ["primary/chat", "backup/chat"],
        primary_ref=lambda request: "primary/chat",
    )


def _automatic_config() -> dict[str, Any]:
    return {"configurable": {"octop_model_failover_auto": True}}


def test_auto_route_fails_over_before_any_token_and_stamps_selected_model() -> None:
    health = ModelHealthRegistry(cooldown_seconds=30)
    middleware = _middleware(health)
    calls: list[str] = []

    def handler(request: _Request) -> ModelResponse[Any]:
        model: _Model = request.model
        calls.append(model.ref)
        assert current_failover_model_ref() == model.ref
        if model.ref == "primary/chat":
            raise _ServiceError()
        return ModelResponse(result=[AIMessage(content="ok")])

    with patch(
        "octop.infra.agents.middleware.model_failover.get_config",
        side_effect=_automatic_config,
    ):
        response = middleware.wrap_model_call(_Request(), handler)

    assert current_failover_model_ref() is None
    assert calls == ["primary/chat", "backup/chat"]
    assert response.result[0].response_metadata["octop_model_ref"] == "backup/chat"
    assert not health.is_available("primary/chat")


def test_partial_stream_stops_native_retry_and_model_failover() -> None:
    middleware = _middleware()
    native_retry = ModelRetryMiddleware(
        max_retries=2,
        initial_delay=0,
        max_delay=0,
        jitter=False,
        on_failure="error",
    )
    calls: list[str] = []

    def handler(request: _Request) -> ModelResponse[Any]:
        model: _Model = request.model
        calls.append(model.ref)
        model.callbacks[0].on_llm_new_token("partial output")
        raise _ServiceError()

    def failover_handler(request: _Request) -> ModelResponse[Any]:
        return middleware.wrap_model_call(request, handler)

    with patch(
        "octop.infra.agents.middleware.model_failover.get_config",
        side_effect=_automatic_config,
    ):
        try:
            native_retry.wrap_model_call(_Request(), failover_handler)
        except OctopError as exc:
            assert exc.code == ErrorCode.MODEL_UNAVAILABLE
            assert exc.details["reason"] == "model_failed_after_stream"
            assert isinstance(exc, ModelError)
            assert exc.is_retryable is False
        else:
            raise AssertionError("post-token model failure must propagate")

    assert calls == ["primary/chat"]


def test_explicit_route_keeps_native_model_retry_behavior() -> None:
    middleware = _middleware()
    native_retry = ModelRetryMiddleware(
        max_retries=1,
        initial_delay=0,
        max_delay=0,
        jitter=False,
        on_failure="error",
    )
    calls: list[str] = []

    def handler(request: _Request) -> ModelResponse[Any]:
        calls.append(request.model)
        raise _ServiceError()

    def failover_handler(request: _Request) -> ModelResponse[Any]:
        return middleware.wrap_model_call(request, handler)

    with patch(
        "octop.infra.agents.middleware.model_failover.get_config",
        return_value={"configurable": {"octop_model_failover_auto": False}},
    ):
        try:
            native_retry.wrap_model_call(_Request(model="user/selected"), failover_handler)
        except _ServiceError:
            pass
        else:
            raise AssertionError("explicit model failures retain native retry result")


def test_graph_interrupt_is_not_wrapped_or_retried() -> None:
    from langgraph.errors import GraphInterrupt

    health = ModelHealthRegistry()
    middleware = _middleware(health)
    native_retry = ModelRetryMiddleware(
        max_retries=2,
        initial_delay=0,
        max_delay=0,
        jitter=False,
        on_failure="error",
    )
    interrupt = GraphInterrupt()
    calls: list[str] = []

    def handler(request: _Request) -> ModelResponse[Any]:
        model: _Model = request.model
        calls.append(model.ref)
        raise interrupt

    with patch(
        "octop.infra.agents.middleware.model_failover.get_config",
        side_effect=_automatic_config,
    ):
        try:
            native_retry.wrap_model_call(
                _Request(),
                lambda request: middleware.wrap_model_call(request, handler),
            )
        except GraphInterrupt as caught:
            assert caught is interrupt
        else:
            raise AssertionError("graph interrupts must retain their control flow")

    assert calls == ["primary/chat"]
    assert health.is_available("primary/chat")


def test_explicit_route_is_not_replaced() -> None:
    middleware = _middleware()
    called: list[str] = []

    def handler(request: _Request) -> ModelResponse[Any]:
        called.append(request.model)
        return ModelResponse(result=[AIMessage(content="explicit")])

    with patch(
        "octop.infra.agents.middleware.model_failover.get_config",
        return_value={"configurable": {"octop_model_failover_auto": False}},
    ):
        middleware.wrap_model_call(_Request(model="user/selected"), handler)

    assert called == ["user/selected"]
