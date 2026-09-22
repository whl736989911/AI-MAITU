"""The freeze that keeps a shared agent's ``MEMORY.md`` from being written by a run.

An app-owned agent (``user_id IS NULL``) serves every caller and has no owner whose
memory its workspace MEMORY.md could be: whatever one caller's run stores is read —
and written over — by the next. The freeze is mounted on such an agent only, so an
expert's own memory keeps updating exactly as before.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.middleware.shared_memory_freeze import (
    SharedMemoryFreezeMiddleware,
    frozen_memory_refusal,
    memory_is_shared,
    shared_memory_freeze_chain,
)

SHARED_AGENT = "feat-quote-draft"
"""An app-owned agent — the shape a feature's own agent has (``user_id IS NULL``)."""

OWNER_ID = 1
"""The owner every agent a *person* owns carries."""


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
    chain = shared_memory_freeze_chain(agent_id=SHARED_AGENT, user_id=None)
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
    ("user_id", "shared"),
    [
        # App-owned: the agent row says nobody owns it.
        (None, True),
        # An expert, and every other agent a person owns.
        (OWNER_ID, False),
        (2, False),
    ],
)
def test_only_an_app_owned_agent_is_frozen(user_id: int | None, shared: bool) -> None:
    assert memory_is_shared(user_id=user_id) is shared
    assert bool(shared_memory_freeze_chain(agent_id=SHARED_AGENT, user_id=user_id)) is shared


def test_the_shipped_chain_refuses_the_write_it_declares() -> None:
    """The chain the manager mounts is the one that refuses: decision and effect agree."""
    (middleware,) = shared_memory_freeze_chain(agent_id=SHARED_AGENT, user_id=None)

    result, reached = _call(middleware, "edit_file", {"file_path": "MEMORY.md"})

    assert reached == 0
    assert isinstance(result, ToolMessage) and result.status == "error"
