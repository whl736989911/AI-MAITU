"""The middleware that caps and records one step's subagent dispatches.

The ceiling and the record are two different promises about the same turn (7.7,
7.8), and both are about what really happened: the tests here measure the
concurrency the subagents actually got rather than trusting the numbers the ledger
reports about itself, and they check the record against the calls the model made.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import ToolMessage
from langgraph.types import Command

from octop.infra.agents.middleware import feature_dispatch
from octop.infra.agents.middleware.feature_scope import TASK_TOOL_NAME
from octop.infra.features.dispatch import MAX_DISPATCH_RESULT_CHARS

_OPENED: list[str] = []


def _tool_request(name: str, args: dict[str, Any] | None = None) -> ToolCallRequest:
    """A tool call as ``ToolNode`` hands it to an interceptor.

    ``tool`` stays ``None``: this boundary reads the call the model made, never the
    tool object it resolved to.
    """
    return ToolCallRequest(
        tool_call={"name": name, "args": args or {}, "id": "call-1"},
        tool=None,
        state=None,
        runtime=None,
    )


def _task_request(role: str, task: str) -> ToolCallRequest:
    """A ``task`` call the way the model makes it: one subagent, one task text."""
    return _tool_request(TASK_TOOL_NAME, {"subagent_type": role, "description": task})


def _mint(*, ceiling: int) -> tuple[str, feature_dispatch.DispatchLedger]:
    token, ledger = feature_dispatch.open_ledger(ceiling=ceiling)
    _OPENED.append(token)
    return token, ledger


def _configure(monkeypatch: Any, token: str | None) -> None:
    """Stamp one ledger token onto every run, the way the router's stamp would."""
    configurable: dict[str, Any] = (
        {} if token is None else {feature_dispatch.CONFIG_KEY: {"token": token}}
    )
    monkeypatch.setattr(feature_dispatch, "get_config", lambda: {"configurable": configurable})


def _open(monkeypatch: Any, *, ceiling: int) -> tuple[str, feature_dispatch.DispatchLedger]:
    token, ledger = _mint(ceiling=ceiling)
    _configure(monkeypatch, token)
    return token, ledger


@pytest.fixture(autouse=True)
def _close_ledgers() -> Iterator[None]:
    """Leave the process-wide token registry as empty as each test found it."""
    yield
    while _OPENED:
        feature_dispatch.close_ledger(_OPENED.pop())


def test_stamp_round_trips_through_the_run_config() -> None:
    """The token reaches the harness as JSON and names the ledger it belongs to."""
    request: dict[str, Any] = {"configurable": {"session_key": "dashboard:ag:1"}}
    token, ledger = _mint(ceiling=3)

    feature_dispatch.stamp_dispatch(request, token)

    assert request["configurable"][feature_dispatch.CONFIG_KEY] == {"token": token}
    assert request["configurable"]["session_key"] == "dashboard:ag:1"
    assert feature_dispatch.dispatch_token(request["configurable"]) == token
    assert feature_dispatch.ledger_for(token) is ledger
    assert feature_dispatch.close_ledger(token) is ledger
    assert feature_dispatch.close_ledger(token) is None
    assert feature_dispatch.ledger_for(token) is None
    assert feature_dispatch.dispatch_token({}) is None
    assert feature_dispatch.dispatch_token({feature_dispatch.CONFIG_KEY: "not-a-mapping"}) is None


def test_a_ceiling_below_one_is_refused() -> None:
    """A zero-slot ledger is not a tight ceiling; it waits for a slot that cannot exist."""
    with pytest.raises(ValueError, match="at least 1"):
        feature_dispatch.DispatchLedger(ceiling=0)


@pytest.mark.parametrize(("ceiling", "waited"), [(2, 3), (1, 4)])
async def test_the_ceiling_bounds_how_many_dispatches_run_at_once(
    monkeypatch: Any, ceiling: int, waited: int
) -> None:
    """7.7: the model may dispatch five at once; the platform runs *ceiling* of them."""
    _, ledger = _open(monkeypatch, ceiling=ceiling)
    running = 0
    overlap: list[int] = []

    async def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal running
        running += 1
        overlap.append(running)
        await asyncio.sleep(0.01)
        running -= 1
        return ToolMessage(
            content=str(request.tool_call["args"]["subagent_type"]), tool_call_id="call-1"
        )

    middleware = feature_dispatch.FeatureDispatchMiddleware()
    results = await asyncio.gather(
        *(
            middleware.awrap_tool_call(_task_request(f"worker-{index}", f"piece {index}"), handler)
            for index in range(5)
        )
    )

    assert [result.content for result in results] == [f"worker-{index}" for index in range(5)]
    assert max(overlap) == ceiling  # what the subagents really got
    assert ledger.peak == ceiling
    assert ledger.waited == waited
    entries = ledger.entries()
    assert all(entry.slots <= ceiling for entry in entries)
    assert sorted(entry.role for entry in entries) == [f"worker-{index}" for index in range(5)]
    assert sorted(entry.task for entry in entries) == [f"piece {index}" for index in range(5)]


async def test_a_failed_subagent_is_recorded_as_failed(monkeypatch: Any) -> None:
    """The call's own outcome is what the record keeps, its error text included."""
    _, ledger = _open(monkeypatch, ceiling=2)
    failed = ToolMessage(
        content="subagent 'writer' failed: no model configured",
        tool_call_id="call-1",
        status="error",
    )

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return failed

    result = await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _task_request("writer", "draft the section"), handler
    )

    assert result is failed
    entry = ledger.entries()[0]
    assert (entry.role, entry.task) == ("writer", "draft the section")
    assert entry.status == "failed"
    assert entry.error == "subagent 'writer' failed: no model configured"
    assert entry.result is None
    assert entry.truncated is False
    assert entry.waited is False and entry.waited_ms == 0
    assert entry.slots == 1
    assert entry.ended_at is not None


async def test_an_exception_from_a_dispatch_is_recorded_and_re_raised(monkeypatch: Any) -> None:
    _, ledger = _open(monkeypatch, ceiling=1)
    middleware = feature_dispatch.FeatureDispatchMiddleware()

    async def exploding(request: ToolCallRequest) -> ToolMessage:
        raise RuntimeError("subagent blew up")

    with pytest.raises(RuntimeError, match="subagent blew up"):
        await middleware.awrap_tool_call(_task_request("writer", "draft"), exploding)

    entry = ledger.entries()[0]
    assert entry.status == "failed"
    assert entry.error == "RuntimeError: subagent blew up"
    assert entry.result is None

    async def answered(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content="fine", tool_call_id="call-1")

    await middleware.awrap_tool_call(_task_request("writer", "retry"), answered)

    # The failed dispatch gave its slot back: the next one starts, it does not queue.
    assert ledger.waited == 0
    assert [entry.status for entry in ledger.entries()] == ["failed", "succeeded"]


async def test_a_call_without_a_stamped_ledger_is_untouched(monkeypatch: Any) -> None:
    """Every turn that is not a feature step keeps the behaviour it had before."""
    _configure(monkeypatch, None)
    seen: list[ToolCallRequest] = []

    async def handler(request: ToolCallRequest) -> str:
        seen.append(request)
        return "executed"

    request = _task_request("writer", "draft")
    result = await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(request, handler)

    assert result == "executed"
    assert seen == [request]


async def test_a_ledger_that_was_already_closed_gates_nothing(monkeypatch: Any) -> None:
    """A turn that ended must not be gated by the ledger it left behind."""
    token, ledger = _mint(ceiling=1)
    assert feature_dispatch.close_ledger(token) is ledger
    _configure(monkeypatch, token)

    async def handler(request: ToolCallRequest) -> str:
        return "executed"

    result = await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _task_request("writer", "draft"), handler
    )

    assert result == "executed"
    assert ledger.entries() == ()


async def test_a_tool_that_is_not_a_dispatch_is_untouched(monkeypatch: Any) -> None:
    _, ledger = _open(monkeypatch, ceiling=1)

    async def handler(request: ToolCallRequest) -> str:
        return "executed"

    result = await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _tool_request("read_file", {"path": "notes.md"}), handler
    )

    assert result == "executed"
    assert ledger.entries() == ()


async def test_a_long_answer_is_recorded_truncated(monkeypatch: Any) -> None:
    """A record that cut an answer has to say so — the length is the evidence."""
    _, ledger = _open(monkeypatch, ceiling=1)
    answer = "x" * (MAX_DISPATCH_RESULT_CHARS + 7)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(content=answer, tool_call_id="call-1")

    await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _task_request("writer", "draft"), handler
    )

    entry = ledger.entries()[0]
    assert entry.truncated is True
    assert entry.result == "x" * MAX_DISPATCH_RESULT_CHARS
    assert len(entry.result) == MAX_DISPATCH_RESULT_CHARS


async def test_an_answer_in_blocks_is_recorded_as_its_text(monkeypatch: Any) -> None:
    """A subagent's own tools can answer in blocks; the record keeps what they said."""
    _, ledger = _open(monkeypatch, ceiling=1)

    async def handler(request: ToolCallRequest) -> ToolMessage:
        return ToolMessage(
            content=[{"type": "text", "text": "draft: "}, {"type": "text", "text": "part one"}],
            tool_call_id="call-1",
        )

    await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _task_request("writer", "draft"), handler
    )

    entry = ledger.entries()[0]
    assert entry.result == "draft: part one"
    assert entry.truncated is False


async def test_a_command_carried_answer_is_recorded(monkeypatch: Any) -> None:
    """A rerouted turn returns a ``Command``; its message is the dispatch's outcome."""
    _, ledger = _open(monkeypatch, ceiling=1)

    async def handler(request: ToolCallRequest) -> Command[Any]:
        return Command(
            update={
                "messages": [
                    ToolMessage(content="bridge refused", tool_call_id="call-1", status="error")
                ]
            }
        )

    result = await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(
        _task_request("writer", "draft"), handler
    )

    assert isinstance(result, Command)
    entry = ledger.entries()[0]
    assert entry.status == "failed"
    assert entry.error == "bridge refused"


def test_the_sync_path_refuses_a_dispatch_instead_of_running_it_uncapped(
    monkeypatch: Any,
) -> None:
    """A dispatch this path cannot await must not run unmeasured — and must say why."""
    _, ledger = _open(monkeypatch, ceiling=1)
    called: list[ToolCallRequest] = []

    def handler(request: ToolCallRequest) -> str:
        called.append(request)
        return "executed"

    middleware = feature_dispatch.FeatureDispatchMiddleware()
    refused = middleware.wrap_tool_call(_task_request("writer", "draft"), handler)

    assert isinstance(refused, ToolMessage)
    assert refused.status == "error"
    assert "async" in str(refused.content)
    assert called == []
    assert ledger.entries() == ()

    # A turn carrying no token is not a feature step: this path leaves it alone.
    _configure(monkeypatch, None)
    assert middleware.wrap_tool_call(_task_request("writer", "draft"), handler) == "executed"


async def test_a_nested_dispatch_of_a_subagent_runs_through(monkeypatch: Any) -> None:
    """A subagent's own helper must not queue behind the parent that holds its slot."""
    _, ledger = _open(monkeypatch, ceiling=1)
    middleware = feature_dispatch.FeatureDispatchMiddleware()
    order: list[str] = []

    async def helper(request: ToolCallRequest) -> ToolMessage:
        order.append("helper")
        return ToolMessage(content="inner", tool_call_id="call-2")

    async def subagent(request: ToolCallRequest) -> ToolMessage:
        nested = await middleware.awrap_tool_call(_task_request("helper", "look this up"), helper)
        order.append("subagent")
        return ToolMessage(content=f"outer+{nested.content}", tool_call_id="call-1")

    result = await asyncio.wait_for(
        middleware.awrap_tool_call(_task_request("writer", "draft"), subagent), timeout=5
    )

    assert result.content == "outer+inner"
    assert order == ["helper", "subagent"]
    # The record stays the step's own dispatch list.
    assert [entry.role for entry in ledger.entries()] == ["writer"]
