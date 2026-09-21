"""Tests for the feature-run system prompt middleware."""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest
from langchain_core.messages import SystemMessage
from langchain_openai import ChatOpenAI

from octop.infra.agents.middleware import feature_prompt

BASE_PROMPT = "You are the user's own agent."


def _request(system_message: SystemMessage | None = None) -> ModelRequest[Any]:
    return ModelRequest(
        model=ChatOpenAI(model="gpt-5.4", api_key="test"),
        messages=[],
        system_message=system_message,
    )


def test_stamp_ignores_a_missing_or_blank_prompt() -> None:
    """A feature without a system file must not change the request at all."""
    for prompt in (None, "", "   \n"):
        request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}
        feature_prompt.stamp_feature_system_prompt(request, prompt)
        assert request == {"configurable": {"session_key": "dashboard:ag:1"}}


def test_stamp_adds_the_prompt_to_the_request_configurable() -> None:
    request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}

    feature_prompt.stamp_feature_system_prompt(request, "  Write meeting notes.  ")

    assert request["configurable"] == {
        "session_key": "dashboard:ag:1",
        feature_prompt.CONFIG_KEY: "Write meeting notes.",
    }


def test_middleware_appends_the_run_prompt_to_the_agents_own(monkeypatch) -> None:
    """The run's text lands after the agent's prompt, for this model call only."""
    monkeypatch.setattr(
        feature_prompt,
        "get_config",
        lambda: {"configurable": {feature_prompt.CONFIG_KEY: "Write meeting notes."}},
    )
    request = _request(SystemMessage(content=BASE_PROMPT))

    configured = feature_prompt._with_feature_prompt(request)

    assert configured.system_message is not None
    assert configured.system_message.text == f"{BASE_PROMPT}\n\nWrite meeting notes."
    # The original request is untouched: nothing is written into ``messages``,
    # which is what the checkpointer persists.
    assert request.system_message is not None
    assert request.system_message.text == BASE_PROMPT
    assert request.messages == []


def test_middleware_is_a_no_op_without_a_stamped_prompt(monkeypatch) -> None:
    monkeypatch.setattr(feature_prompt, "get_config", lambda: {"configurable": {}})
    request = _request(SystemMessage(content=BASE_PROMPT))

    assert feature_prompt._with_feature_prompt(request) is request


def test_wrap_model_call_hands_the_overridden_request_to_the_model(monkeypatch) -> None:
    monkeypatch.setattr(
        feature_prompt,
        "get_config",
        lambda: {"configurable": {feature_prompt.CONFIG_KEY: "Write meeting notes."}},
    )
    seen: list[ModelRequest[Any]] = []

    def handler(request: ModelRequest[Any]) -> str:
        seen.append(request)
        return "response"

    result = feature_prompt.FeatureSystemPromptMiddleware().wrap_model_call(
        _request(SystemMessage(content=BASE_PROMPT)),
        handler,
    )

    assert result == "response"
    assert seen[0].system_message is not None
    assert "Write meeting notes." in seen[0].system_message.text
