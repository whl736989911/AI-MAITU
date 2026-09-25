"""A feature turn's memory routing: capture is private-only, recall never crosses users.

The turn path stamps a ``FeatureMemoryTurnContext`` on the run's configurable;
these tests drive the middleware, tools and freeze exactly as a stamped (or
deliberately unstamped) turn would see them.
"""

from __future__ import annotations

import time
from collections.abc import Iterator
from contextlib import contextmanager
from types import SimpleNamespace
from typing import Any

import pytest
from harness_agent.middleware.memory_recall import RECALL_SNAPSHOT_KEY, has_recall_snapshot
from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.config import var_child_runnable_config
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.memory_backend import memory_namespace
from octop.infra.agents.middleware import feature_memory as fm
from octop.infra.agents.middleware.feature_memory import (
    CONFIGURABLE_FEATURE_MEMORY_KEY,
    FeatureMemoryMiddleware,
    FeatureMemoryRuntime,
    FeatureMemoryTurnContext,
    build_feature_memory_tools,
    turn_feature_memory_context,
)
from octop.infra.agents.middleware.shared_workspace_freeze import (
    SharedWorkspaceFreezeMiddleware,
    frozen_shared_file_refusal,
)

AGENT = "feat-mem"
SHARED_NS = memory_namespace(AGENT)
USER_42_NS = memory_namespace(AGENT, 42)
USER_7_NS = memory_namespace(AGENT, 7)


# ----------------------------------------------------------------------
# Helpers
# ----------------------------------------------------------------------


@contextmanager
def stamped(context: FeatureMemoryTurnContext | None, **configurable: Any) -> Iterator[None]:
    """Pretend the turn runs under a gateway-stamped configurable."""
    config: dict[str, Any] = {"configurable": dict(configurable)}
    if context is not None:
        config["configurable"][CONFIGURABLE_FEATURE_MEMORY_KEY] = context
    token = var_child_runnable_config.set(config)
    try:
        yield
    finally:
        var_child_runnable_config.reset(token)


def _ctx(
    stage: str | None,
    *,
    shared_writable: bool = False,
    private: str | None = None,
) -> FeatureMemoryTurnContext:
    return FeatureMemoryTurnContext(
        stage=stage,
        shared_writable=shared_writable,
        shared_namespace=SHARED_NS,
        private_namespace=private,
    )


DRAFT_AUTHOR = _ctx("draft", shared_writable=True)
ACTIVE_42 = _ctx("active", private=USER_42_NS)
IM_TURN = _ctx("active")


class FakeService:
    """Records every memory touch so tests can assert where writes landed."""

    def __init__(self, name: str) -> None:
        self.name = name
        self.captured: list[dict[str, Any]] = []
        self.extracted: list[str] = []
        self.searched: list[str] = []
        self.reads: list[str] = []

    def recall(self, query: str, **_kwargs: Any) -> Any:
        return SimpleNamespace(rendered=f"recall::{self.name}" if self.name != "empty" else "")

    def search(self, query: str, *, max_results: int = 5, **_kwargs: Any) -> dict[str, Any]:
        self.searched.append(query)
        if self.name == "empty":
            return {"hits": [], "total": 0, "empty_reason": "empty"}
        return {
            "hits": [{"path": "atom/a1.md", "layer": "atom", "snippet": f"{self.name} hit"}],
            "total": 1,
            "empty_reason": None,
        }

    def get(
        self, path: str, *, start: int | None = None, lines: int | None = None
    ) -> dict[str, Any]:
        self.reads.append(path)
        if path == "missing.md":
            return {"error": "not found"}
        return {"content": f"content::{self.name}::{path}"}

    def capture_turn(self, **kwargs: Any) -> dict[str, Any]:
        self.captured.append(kwargs)
        return {}

    def extract(self, session_id: str, **_kwargs: Any) -> dict[str, Any]:
        self.extracted.append(session_id)
        return {"candidates": 0, "promoted": 0}


class FakeRuntime:
    """Namespace-keyed fake of ``FeatureMemoryRuntime``."""

    def __init__(self, *namespaces: str) -> None:
        self.services = {ns: FakeService(ns) for ns in namespaces}
        self.closed = False

    def service(self, namespace: str) -> FakeService | None:
        return self.services.get(namespace)

    def close(self) -> None:
        self.closed = True


def _middleware(
    runtime: FakeRuntime, *, idle_extract_seconds: float = 0.0
) -> FeatureMemoryMiddleware:
    return FeatureMemoryMiddleware(
        agent_id=AGENT, runtime=runtime, idle_extract_seconds=idle_extract_seconds
    )


def _sync_daemon(monkeypatch: pytest.MonkeyPatch) -> None:
    """Run memory writes inline so tests can assert them deterministically."""
    monkeypatch.setattr(fm, "_submit_daemon", lambda fn, **_k: fn())


def _prompt_state() -> dict[str, Any]:
    """State at before_model time: only the user's prompt is in the thread."""
    return {"messages": [HumanMessage("remember metric units")]}


def _turn_state() -> dict[str, Any]:
    """State at after_model time: the assistant's reply has joined the thread."""
    return {
        "messages": [HumanMessage("remember metric units"), AIMessage("noted")],
    }


# ----------------------------------------------------------------------
# Namespace rule
# ----------------------------------------------------------------------


def test_memory_namespace_shapes() -> None:
    assert memory_namespace("feat-1") == "agent_feat-1"
    assert memory_namespace("feat-1", 42) == "agent_feat-1_user_42"


@pytest.mark.parametrize("bad", [0, -3, "42", 4.2, True])
def test_memory_namespace_refuses_non_user_ids(bad: Any) -> None:
    """A bad id must raise, never silently redirect into the shared namespace."""
    with pytest.raises(ValueError):
        memory_namespace("feat-1", bad)


# ----------------------------------------------------------------------
# Turn context routing matrix
# ----------------------------------------------------------------------


def test_draft_author_turn_captures_nowhere() -> None:
    """Manual training only: even the draft author's conversation never writes shared."""
    assert DRAFT_AUTHOR.capture_namespace() is None


def test_active_verified_turn_captures_own_private_only() -> None:
    assert ACTIVE_42.capture_namespace() == USER_42_NS


def test_im_turn_captures_nowhere() -> None:
    assert IM_TURN.capture_namespace() is None


def test_recall_reads_shared_plus_own_private_never_another_user() -> None:
    assert DRAFT_AUTHOR.recall_namespaces() == (SHARED_NS,)
    assert ACTIVE_42.recall_namespaces() == (SHARED_NS, USER_42_NS)
    other = _ctx("active", private=USER_7_NS)
    assert other.recall_namespaces() == (SHARED_NS, USER_7_NS)
    assert USER_42_NS not in other.recall_namespaces()


def test_stamped_context_round_trips_through_config() -> None:
    with stamped(ACTIVE_42, thread_id="t1"):
        assert turn_feature_memory_context() is ACTIVE_42


# ----------------------------------------------------------------------
# Middleware: recall
# ----------------------------------------------------------------------


def test_recall_merges_shared_and_own_private_for_active_caller() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS, USER_7_NS)
    middleware = _middleware(runtime)
    with stamped(ACTIVE_42, thread_id="t1", session_id="s1"):
        update = middleware.before_model(_prompt_state(), None)
    assert update is not None
    message = update["messages"][0]
    assert has_recall_snapshot(message)
    suffix = message.additional_kwargs[RECALL_SNAPSHOT_KEY]["suffix"]
    assert f"recall::{SHARED_NS}" in suffix
    assert f"recall::{USER_42_NS}" in suffix
    assert f"recall::{USER_7_NS}" not in suffix


def test_draft_author_recall_reads_shared_only() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime)
    with stamped(DRAFT_AUTHOR, thread_id="t1"):
        update = middleware.before_model(_prompt_state(), None)
    assert update is not None
    suffix = update["messages"][0].additional_kwargs[RECALL_SNAPSHOT_KEY]["suffix"]
    assert f"recall::{SHARED_NS}" in suffix
    assert f"recall::{USER_42_NS}" not in suffix


def test_unstamped_turn_fails_closed_to_shared_reads() -> None:
    """HITL resume / cron carry no stamp: shared recall only, nothing writable."""
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime)
    update = middleware.before_model(_prompt_state(), None)
    assert update is not None
    suffix = update["messages"][0].additional_kwargs[RECALL_SNAPSHOT_KEY]["suffix"]
    assert f"recall::{SHARED_NS}" in suffix
    assert f"recall::{USER_42_NS}" not in suffix


# ----------------------------------------------------------------------
# Middleware: capture and extraction scope
# ----------------------------------------------------------------------


def test_active_turn_captures_and_extracts_private_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _sync_daemon(monkeypatch)
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime)
    with stamped(ACTIVE_42, thread_id="t1", session_id="s1", user="42"):
        middleware.before_model(_prompt_state(), None)
        middleware.after_model(_turn_state(), None)
    assert runtime.services[USER_42_NS].captured, "capture must land in the private ns"
    assert runtime.services[SHARED_NS].captured == []
    captured = runtime.services[USER_42_NS].captured[0]
    assert captured["user"] == "remember metric units"
    assert captured["assistant"] == "noted"
    assert captured["session_id"] == "s1"

    # Idle extraction distills the namespace the turns were captured in.
    middleware.end_session("s1", background=False)
    assert runtime.services[USER_42_NS].extracted == ["s1"]
    assert runtime.services[SHARED_NS].extracted == []


@pytest.mark.parametrize(
    "context",
    [
        DRAFT_AUTHOR,
        IM_TURN,
        None,
    ],
    ids=["draft-author", "im", "unstamped"],
)
def test_no_other_turn_ever_captures(
    context: FeatureMemoryTurnContext | None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Draft author, IM and unstamped turns write no memory at all."""
    _sync_daemon(monkeypatch)
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime)
    configurable = {"thread_id": "t1", "session_id": "s1"}
    with stamped(context, **configurable):
        middleware.before_model(_prompt_state(), None)
        middleware.after_model(_turn_state(), None)
    for service in runtime.services.values():
        assert service.captured == []
    middleware.end_session("s1", background=False)
    for service in runtime.services.values():
        assert service.extracted == []


def test_session_scope_survives_a_stage_flip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Extraction follows where the events live, not the feature's current stage."""
    _sync_daemon(monkeypatch)
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime)
    with stamped(ACTIVE_42, thread_id="t1", session_id="s1"):
        middleware.before_model(_prompt_state(), None)
        middleware.after_model(_turn_state(), None)
    # The feature flips to draft mid-session: later turns capture nothing.
    with stamped(DRAFT_AUTHOR, thread_id="t1", session_id="s1"):
        middleware.before_model(_prompt_state(), None)
        middleware.after_model(_turn_state(), None)
    middleware.end_session("s1", background=False)
    assert runtime.services[USER_42_NS].extracted == ["s1"]
    assert runtime.services[SHARED_NS].extracted == []


def test_shutdown_cancels_pending_extractions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Timers die with the middleware; the session's raw events stay extractable."""
    _sync_daemon(monkeypatch)
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    middleware = _middleware(runtime, idle_extract_seconds=0.15)
    with stamped(ACTIVE_42, thread_id="t1", session_id="s1", user="42"):
        middleware.before_model(_prompt_state(), None)
        middleware.after_model(_turn_state(), None)
    middleware.shutdown()
    time.sleep(0.4)
    assert runtime.services[USER_42_NS].extracted == []
    middleware.end_session("s1", background=False)
    assert runtime.services[USER_42_NS].extracted == ["s1"]


def test_close_releases_the_runtime_idempotently() -> None:
    runtime = FakeRuntime(SHARED_NS)
    middleware = _middleware(runtime)
    middleware.close()
    middleware.close()
    assert runtime.closed


def test_closed_runtime_hands_out_no_services() -> None:
    runtime = FeatureMemoryRuntime(
        agent_id=AGENT, backend_type="unknown-backend", backend_config=None
    )
    assert runtime.service(SHARED_NS) is None
    runtime.close()


# ----------------------------------------------------------------------
# Tools: search / get stay inside the turn's namespaces
# ----------------------------------------------------------------------


def test_search_covers_shared_and_own_private_with_prefixes() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS, USER_7_NS)
    memory_search, _memory_get = build_feature_memory_tools(runtime, agent_id=AGENT)
    with stamped(ACTIVE_42):
        out = memory_search.invoke({"query": "units"})
    assert "shared:atom/a1.md" in out
    assert "private:atom/a1.md" in out
    assert runtime.services[SHARED_NS].searched == ["units"]
    assert runtime.services[USER_42_NS].searched == ["units"]
    # The model has no way to reach another user's partition.
    assert USER_7_NS not in runtime.services or runtime.services[USER_7_NS].searched == []


def test_search_on_draft_author_covers_shared_only() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    memory_search, _ = build_feature_memory_tools(runtime, agent_id=AGENT)
    with stamped(DRAFT_AUTHOR):
        out = memory_search.invoke({"query": "units"})
    assert "shared:atom/a1.md" in out
    assert "[private]" not in out
    assert runtime.services[USER_42_NS].searched == []


def test_get_routes_prefixes_and_bare_paths() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS, USER_7_NS)
    _search, memory_get = build_feature_memory_tools(runtime, agent_id=AGENT)
    with stamped(ACTIVE_42):
        pinned_private = memory_get.invoke({"path": "private:atom/a1.md"})
        pinned_shared = memory_get.invoke({"path": "shared:atom/a1.md"})
        bare = memory_get.invoke({"path": "atom/a1.md"})
    assert pinned_private == f"content::{USER_42_NS}::atom/a1.md"
    assert pinned_shared == f"content::{SHARED_NS}::atom/a1.md"
    # A bare path prefers the caller's own private memory.
    assert bare == f"content::{USER_42_NS}::atom/a1.md"
    assert runtime.services[USER_42_NS].reads == ["atom/a1.md", "atom/a1.md"]
    assert runtime.services[SHARED_NS].reads == ["atom/a1.md"]
    assert runtime.services[USER_7_NS].reads == []


def test_get_refuses_private_prefix_without_a_private_view() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    _search, memory_get = build_feature_memory_tools(runtime, agent_id=AGENT)
    with stamped(DRAFT_AUTHOR):
        out = memory_get.invoke({"path": "private:atom/a1.md"})
    assert "no private memory" in out
    assert runtime.services[USER_42_NS].reads == []


def test_get_reports_misses_instead_of_raising() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    _search, memory_get = build_feature_memory_tools(runtime, agent_id=AGENT)
    with stamped(ACTIVE_42):
        miss = memory_get.invoke({"path": "shared:missing.md"})
    assert "not found" in miss


def test_unstamped_tool_turn_reads_shared_only() -> None:
    runtime = FakeRuntime(SHARED_NS, USER_42_NS)
    memory_search, memory_get = build_feature_memory_tools(runtime, agent_id=AGENT)
    out = memory_search.invoke({"query": "units"})
    assert "[shared]" in out and "[private]" not in out
    got = memory_get.invoke({"path": "atom/a1.md"})
    assert got == f"content::{SHARED_NS}::atom/a1.md"


# ----------------------------------------------------------------------
# Freeze: manual training only, verified identity only
# ----------------------------------------------------------------------


def _freeze_middleware() -> SharedWorkspaceFreezeMiddleware:
    return SharedWorkspaceFreezeMiddleware(agent_id=AGENT)


def _run_write(tool_name: str, args: dict[str, Any]) -> tuple[Any, int]:
    reached = 0

    def handler(_request: ToolCallRequest) -> ToolMessage:
        nonlocal reached
        reached += 1
        return ToolMessage(content="written", tool_call_id="call-1")

    request = ToolCallRequest(
        tool_call={"name": tool_name, "args": args, "id": "call-1"},
        tool=StructuredTool.from_function(func=lambda: "x", name=tool_name, description="t"),
        state=None,
        runtime=None,
    )
    middleware = _freeze_middleware()
    result = middleware.wrap_tool_call(request, handler)
    return result, reached


def test_verified_draft_author_may_edit_shared_files_manually() -> None:
    with stamped(DRAFT_AUTHOR):
        assert frozen_shared_file_refusal("write_file", {"file_path": "MEMORY.md"}) is None
        result, reached = _run_write("write_file", {"file_path": "MEMORY.md"})
    assert reached == 1
    assert str(getattr(result, "content", "")) == "written"


@pytest.mark.parametrize(
    "context",
    [
        _ctx("draft", shared_writable=False),  # draft, but not the author
        IM_TURN,  # active: frozen for everyone
        None,  # unstamped: fail closed
    ],
    ids=["draft-non-author", "active", "unstamped"],
)
def test_freeze_refuses_shared_file_writes_without_training_stamp(
    context: FeatureMemoryTurnContext | None,
) -> None:
    with stamped(context):
        refusal = frozen_shared_file_refusal("edit_file", {"file_path": "memory.md"})
        result, reached = _run_write("edit_file", {"file_path": "USER.md"})
    assert refusal is not None
    assert reached == 0
    assert "frozen" in str(getattr(result, "content", ""))


def test_freeze_leaves_other_files_and_reads_alone() -> None:
    with stamped(IM_TURN):
        assert frozen_shared_file_refusal("write_file", {"file_path": "notes.txt"}) is None
        assert frozen_shared_file_refusal("read_file", {"file_path": "MEMORY.md"}) is None


def test_freeze_chain_never_mounts_for_experts() -> None:
    from octop.infra.agents.middleware.shared_workspace_freeze import shared_workspace_freeze_chain

    assert shared_workspace_freeze_chain(agent_id=AGENT, kind=KIND_FEATURE)
    assert shared_workspace_freeze_chain(agent_id="expert-1", kind=KIND_AGENT) == []
