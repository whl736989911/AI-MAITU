"""Feature workspace profile and memory stay shared but not writable in a turn.

Both root files reach later callers of a feature. The guard blocks conversational
file-tool writes to either and leaves an expert's own files untouched.
"""

from __future__ import annotations

from typing import Any

import pytest
from langchain_core.messages import ToolMessage
from langchain_core.tools import StructuredTool
from langgraph.prebuilt.tool_node import ToolCallRequest

from octop.infra.agents.kinds import KIND_AGENT, KIND_FEATURE
from octop.infra.agents.middleware.shared_workspace_freeze import (
    SharedWorkspaceFreezeMiddleware,
    shared_workspace_freeze_chain,
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
    middleware: SharedWorkspaceFreezeMiddleware, tool_name: str, args: dict[str, Any]
) -> tuple[Any, int]:
    """Run one tool call through *middleware*, counting what reached the tool itself."""
    reached = 0

    def handler(request: ToolCallRequest) -> ToolMessage:
        nonlocal reached
        reached += 1
        return ToolMessage(content="written", tool_call_id="call-1")

    return middleware.wrap_tool_call(_request(tool_name, args), handler), reached


def _shared_middleware() -> SharedWorkspaceFreezeMiddleware:
    chain = shared_workspace_freeze_chain(agent_id=SHARED_AGENT, kind=FEATURE_KIND)
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
        ("write_file", {"file_path": "USER.md", "content": "private"}),
        ("edit_file", {"file_path": "./USER.md", "old_string": "a", "new_string": "b"}),
        ("append_file", {"path": "/user.md", "content": "private"}),
        ("write_file", {"file_path": "notes/../USER.md", "content": "private"}),
    ],
)
def test_a_shared_agents_write_to_its_profile_or_memory_is_refused(
    tool_name: str, args: dict[str, Any]
) -> None:
    """The write never reaches the tool, and the model is told why."""
    result, reached = _call(_shared_middleware(), tool_name, args)

    assert reached == 0, "shared root files must not be written"
    assert isinstance(result, ToolMessage)
    assert result.status == "error"
    expected = "USER.md" if "user.md" in str(args).casefold() else "MEMORY.md"
    assert expected in str(result.content)


@pytest.mark.parametrize(
    ("tool_name", "args"),
    [
        ("read_file", {"file_path": "MEMORY.md"}),
        ("read_file", {"file_path": "USER.md"}),
        ("grep", {"pattern": "x", "path": "MEMORY.md"}),
        ("write_file", {"file_path": "notes/todo.md", "content": "x"}),
        # A file under a subdirectory is not the shared workspace-root file.
        ("write_file", {"file_path": "reports/MEMORY.md", "content": "x"}),
        ("write_file", {"file_path": "reports/USER.md", "content": "x"}),
        ("write_file", {"file_path": "USER.md.bak", "content": "x"}),
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


def test_an_owned_agent_that_is_not_a_feature_keeps_writing_its_own_memory() -> None:
    """The expert side is untouched: nothing is mounted, so the write goes through."""
    assert shared_workspace_freeze_chain(agent_id="expert-1", kind=EXPERT_KIND) == []
