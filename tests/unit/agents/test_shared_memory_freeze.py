"""The freeze that keeps a feature agent's ``MEMORY.md`` from being written by a run.

A feature's own agent (``kind = 'feature'``) serves every caller of that feature and
has no person whose memory its workspace MEMORY.md could be: whatever one caller's
run stores is read — and written over — by the next. The freeze is mounted on such an
agent only, so an expert's own memory keeps updating exactly as before — the two
directions are both asserted below.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.middleware.shared_memory_freeze import (
    SharedMemoryFreezeMiddleware,
    frozen_memory_refusal,
    memory_is_shared,
    shared_memory_freeze_chain,
)

SHARED_AGENT = "feat-quote-draft"
"""A feature's own agent, by the id rule a feature's agent is created with."""

FEATURE_KIND = KIND_FEATURE
"""What such a row's ``kind`` says — the one thing the freeze reads."""

EXPERT_KIND = KIND_AGENT
"""And what every other agent's says: the user's own expert, feature or not."""


def _tool(name: str) -> Any:
    def _run() -> str:
        return name

    return StructuredTool.from_function(func=_run, name=name, description=f"{name} tool")


def _request(name: str, args: dict[str, Any]) -> ToolCallRequest:
    return ToolCallRequest(
        tool_call={"name": name, "args": args, "id": "call-1"},
        tool=_tool(name),
        state=None,
        runtime=None,
    )


def _call(
    middleware: SharedMemoryFreezeMiddleware, tool_name: str, args: dict[str, Any]
) -> tuple[Any, int]:
    """Run one tool call through *middleware*, counting what reached the tool itself."""
    reached = 0

    def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal reached
        reached += 1
        return ToolMessage(content="written", tool_call_id="call-1")

    return middleware.wrap_tool_call(_request(tool_name, args), handler), reached


def _shared_middleware() -> SharedMemoryFreezeMiddleware:
    chain = shared_memory_freeze_chain(agent_id=SHARED_AGENT, kind=FEATURE_KIND)
    assert len(chain) == 1
    return chain[0]


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("write_file", {"file_path": "MEMORY.md", "content": "x"}),
        ("edit_file", {"file_path": "MEMORY.md", "old_string": "a", "new_string": "b"}),
        ("append_file", {"path": "/MEMORY.md", "content": "x"}),
        ("delete", {"file_path": "./MEMORY.md"}),
        # The path spellings a model actually emits: root-relative either way, the
        # backslash form, and the case-insensitive filesystem spelling of the same file.
        ("write_file", {"file_path": "/MEMORY.md", "content": "x"}),
        ("write_file", {"file_path": "memory.md", "content": "x"}),
        ("write_file", {"file_path": ".\\MEMORY.md", "content": "x"}),
        ("filesystem/write_file", {"file_path": "MEMORY.md", "content": "x"}),
    ],
)
def test_a_shared_agents_write_to_its_memory_is_refused(
    tool_name: str, args: dict[str, Any]
) -> None:
    """The write never reaches the tool, and the model is told why."""
    result, reached = _call(_shared_middleware(), tool_name, args)

    assert reached == 0, "the frozen memory file must not be written"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    assert "MEMORY.md" in str(result.content)
    assert "frozen" in str(result.content)


def test_the_refusal_says_the_memory_is_shared_and_not_to_work_around_it() -> None:
    """A refusal the model can act on: why, and what to do instead."""
    result, _ = _call(_shared_middleware(), "edit_file", {"file_path": "MEMORY.md"})

    assert isinstance(result, ToolMessage)
    assert "every one of its callers" in str(result.content)
    assert "do not work" in str(result.content)


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("read_file", {"file_path": "MEMORY.md"}),
        ("grep", {"pattern": "x", "path": "MEMORY.md"}),
        ("write_file", {"file_path": "notes/todo.md", "content": "x"}),
        # A file of the agent's own that happens to carry the name: not the
        # workspace-root memory file, so not the shared one.
        ("write_file", {"file_path": "reports/MEMORY.md", "content": "x"}),
        ("write_file", {"file_path": "MEMORY.md.bak", "content": "x"}),
        ("execute", {"command": "echo x >> MEMORY.md"}),
    ],
)
def test_everything_but_a_write_to_the_shared_file_passes_through(
    tool_name: str, args: dict[str, Any]
) -> None:
    result, reached = _call(_shared_middleware(), tool_name, args)

    assert reached == 1, "this call is not the frozen write"
    assert isinstance(result, ToolMessage) and result.content == "written"


def test_reading_the_memory_file_is_untouched() -> None:
    """Memory stays *readable*: the harness still injects the file into every prompt."""
    assert frozen_memory_refusal("read_file", {"file_path": "MEMORY.md"}) is None


@pytest.mark.parametrize(
    ("kind", "shared"),
    [
        # A feature's agent, whichever author owns it: the row's kind is the answer.
        (FEATURE_KIND, True),
        # The user's own agents — an expert, an app-owned row of any other shape.
        (EXPERT_KIND, False),
    ],
)
def test_only_a_features_agent_is_frozen(kind: str, shared: bool) -> None:
    assert memory_is_shared(kind=kind) is shared
    assert bool(shared_memory_freeze_chain(agent_id=SHARED_AGENT, kind=kind)) is shared


def test_an_owned_agent_that_is_not_a_feature_keeps_writing_its_own_memory() -> None:
    """The expert side is untouched: nothing is mounted, so the write goes through."""
    assert shared_memory_freeze_chain(agent_id="expert-1", kind=EXPERT_KIND) == []


def test_the_shipped_chain_refuses_the_write_it_declares() -> None:
    """The chain the manager mounts is the one that refuses: decision and effect agree."""
    (middleware,) = shared_memory_freeze_chain(agent_id=SHARED_AGENT, kind=FEATURE_KIND)

    result, reached = _call(middleware, "edit_file", {"file_path": "MEMORY.md"})

    assert reached == 0
    assert isinstance(result, ToolMessage) and result.status == "error"
