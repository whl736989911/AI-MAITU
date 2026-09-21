"""The middleware that applies one feature run's declared capability scope.

A feature declares what its own run may use (design 5.1/5.2). The scope rides
``configurable``, so it is read per run: chat turns, cron delivery and IM
messages never carry it and must pass through untouched.
"""

from __future__ import annotations

from typing import Any

from langchain.agents.middleware import ModelRequest, ToolCallRequest
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langchain_openai import ChatOpenAI

from octop.infra.agents.middleware import feature_scope

TASK_DESCRIPTION = """Delegate a subtask to a subagent.

Available agent types and the tools they have access to:
- researcher: searches the web
- writer: writes the draft

Pick the one that fits.
"""


def _tool(name: str) -> Any:
    def _run() -> str:
        return name

    return StructuredTool.from_function(func=_run, name=name, description=f"{name} tool")


def _model_request(tools: list[Any]) -> ModelRequest[Any]:
    return ModelRequest(
        model=ChatOpenAI(model="gpt-5.4", api_key="test"),
        messages=[AIMessage(content="hi")],
        tools=tools,
    )


def _tool_request(name: str, args: dict[str, Any] | None = None) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": "call-1"},
        tool=_tool(name),
        state=None,
        runtime=None,
    )


def _configure(monkeypatch: Any, scope: dict[str, Any] | None) -> None:
    """Stamp one scope onto every run, the way the router's stamp would."""
    configurable = {} if scope is None else {feature_scope.CONFIG_KEY: scope}
    monkeypatch.setattr(feature_scope, "get_config", lambda: {"configurable": configurable})


def test_stamp_is_a_no_op_for_a_feature_that_declares_nothing() -> None:
    """Such a run's request must stay byte-identical to a pre-layer one."""
    request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}

    feature_scope.stamp_feature_scope(request, feature_scope.FeatureRunScope())

    assert request == {"configurable": {"session_key": "dashboard:ag:1"}}


def test_stamp_round_trips_through_the_run_config() -> None:
    """A declared empty scope survives: it is "none", not "nothing declared"."""
    request: dict[str, Any] = {}

    feature_scope.stamp_feature_scope(
        request,
        feature_scope.FeatureRunScope(tools_disabled=("browser_use",), subagents=()),
    )

    assert request == {
        "configurable": {
            feature_scope.CONFIG_KEY: {"tools_disabled": ["browser_use"], "subagents": []}
        }
    }
    read_back = feature_scope.feature_scope_from_config(request["configurable"])
    assert read_back is not None
    assert read_back.tools_disabled == ("browser_use",)
    assert read_back.subagents == ()


def test_a_run_without_a_scope_reads_back_as_none(monkeypatch) -> None:
    _configure(monkeypatch, None)

    assert feature_scope.feature_scope_from_config({}) is None
    assert feature_scope.feature_scope_from_config({"session_key": "x"}) is None
    assert (
        feature_scope.FeatureScopeMiddleware()._apply(
            request := _model_request([_tool("read_file")])
        )
        is request
    )


def test_disabled_tools_are_dropped_from_the_models_tool_list(monkeypatch) -> None:
    _configure(monkeypatch, {"tools_disabled": ["browser_use"], "subagents": None})

    request = feature_scope.FeatureScopeMiddleware()._apply(
        _model_request([_tool("read_file"), _tool("browser_use")])
    )

    assert [feature_scope.tool_name(tool) for tool in request.tools] == ["read_file"]


def test_an_empty_subagent_scope_hides_the_task_tool(monkeypatch) -> None:
    """A tool the run may never use would be a standing invitation to call it."""
    _configure(monkeypatch, {"tools_disabled": [], "subagents": []})

    request = feature_scope.FeatureScopeMiddleware()._apply(
        _model_request([_tool("read_file"), _tool(feature_scope.TASK_TOOL_NAME)])
    )

    assert [feature_scope.tool_name(tool) for tool in request.tools] == ["read_file"]


def test_the_task_tool_only_offers_the_allowed_subagents(monkeypatch) -> None:
    _configure(monkeypatch, {"tools_disabled": [], "subagents": ["writer"]})
    task = StructuredTool.from_function(
        func=lambda subagent_type: subagent_type,
        name=feature_scope.TASK_TOOL_NAME,
        description=TASK_DESCRIPTION,
    )

    request = feature_scope.FeatureScopeMiddleware()._apply(
        _model_request([_tool("read_file"), task])
    )

    description = str(request.tools[1].description)
    assert "- writer: writes the draft" in description
    assert "researcher" not in description
    assert "Only these subagent types may be used in this run: writer" in description


def test_an_unrecognised_task_description_is_kept_and_constrained(monkeypatch) -> None:
    """A layout this build does not know must not be edited by guesswork."""
    _configure(monkeypatch, {"tools_disabled": [], "subagents": ["writer"]})
    original = "Dispatch a subagent. (layout changed upstream)"
    task = StructuredTool.from_function(
        func=lambda subagent_type: subagent_type,
        name=feature_scope.TASK_TOOL_NAME,
        description=original,
    )

    request = feature_scope.FeatureScopeMiddleware()._apply(_model_request([task]))

    description = str(request.tools[0].description)
    assert description.startswith(original)
    assert "Only these subagent types may be used in this run: writer" in description


def test_a_disabled_tool_is_refused_at_call_time(monkeypatch) -> None:
    """``ToolNode`` defers validation, so hiding a name is not enough."""
    _configure(monkeypatch, {"tools_disabled": ["browser_use"], "subagents": None})
    called: list[Any] = []

    def handler(request: ToolCallRequest) -> str:
        called.append(request)
        return "executed"

    result = feature_scope.FeatureScopeMiddleware().wrap_tool_call(
        _tool_request("browser_use"), handler
    )

    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "browser_use" in str(result.content)
    assert called == []


def test_a_subagent_outside_the_scope_is_refused(monkeypatch) -> None:
    _configure(monkeypatch, {"tools_disabled": [], "subagents": ["writer"]})
    called: list[Any] = []

    def handler(request: ToolCallRequest) -> str:
        called.append(request)
        return "executed"

    middleware = feature_scope.FeatureScopeMiddleware()
    refused = middleware.wrap_tool_call(
        _tool_request(feature_scope.TASK_TOOL_NAME, {"subagent_type": "researcher"}), handler
    )
    allowed = middleware.wrap_tool_call(
        _tool_request(feature_scope.TASK_TOOL_NAME, {"subagent_type": "writer"}), handler
    )

    assert isinstance(refused, ToolMessage)
    assert refused.status == "error"
    assert "researcher" in str(refused.content)
    assert allowed == "executed"
    assert len(called) == 1


def test_a_run_without_a_scope_calls_tools_untouched(monkeypatch) -> None:
    _configure(monkeypatch, None)

    result = feature_scope.FeatureScopeMiddleware().wrap_tool_call(
        _tool_request("browser_use"), lambda request: "executed"
    )

    assert result == "executed"
