"""Permission decisions must survive real tool dispatch and nested agent graphs."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
from collections.abc import Iterator, Sequence
from pathlib import Path
from typing import Any

import pytest
from deepagents import create_deep_agent
from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, ToolMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langchain_core.tools import StructuredTool

from octop.infra.agents.middleware.capabilities import CapabilityMiddleware, guarded_subagent_specs
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.users.capabilities import UserCapabilityPolicy


class _UntrustedToolModel(BaseChatModel):
    """Deliberately request a tool even when it is absent from the advertised list."""

    requested_tool: str = "execute"

    @property
    def _llm_type(self) -> str:
        return "permission-boundary-probe"

    def bind_tools(self, tools: Sequence[Any], **kwargs: Any) -> Any:
        return self

    def get_token_ids(self, text: str) -> list[int]:
        return list(range(len(text.split())))

    def _generate(
        self,
        messages: list[BaseMessage],
        stop: list[str] | None = None,
        run_manager: Any = None,
        **kwargs: Any,
    ) -> ChatResult:
        if isinstance(messages[-1], ToolMessage):
            message = AIMessage(content="Finished")
        else:
            prompt = next(
                message.content
                for message in reversed(messages)
                if isinstance(message, HumanMessage)
            )
            if prompt in {"delegate-default", "delegate-worker"}:
                name = "general-purpose" if prompt == "delegate-default" else "worker"
                call = {
                    "name": "task",
                    "args": {"description": "execute", "subagent_type": name},
                    "id": "delegate",
                }
            else:
                call = {
                    "name": self.requested_tool,
                    "args": {"command": "probe"},
                    "id": "operation",
                }
            message = AIMessage(content="", tool_calls=[call])
        return ChatResult(generations=[ChatGeneration(message=message)])


@pytest.fixture
def execution_env(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[tuple[RepoBundle, Path, StructuredTool, CapabilityMiddleware]]:
    monkeypatch.setattr("octop.infra.users.permissions.license_managed_install", lambda: False)
    db = SqlitePool(tmp_path / "control.db")
    run_migrations(db)
    repos = RepoBundle.from_pool(db)
    marker = tmp_path / "execution.txt"

    def execute(command: str) -> str:
        """Run the bounded local execution probe."""
        environment = {
            key: os.environ[key] for key in ("SYSTEMROOT", "WINDIR") if key in os.environ
        }
        subprocess.run(
            [
                sys.executable,
                "-I",
                "-S",
                "-c",
                "from pathlib import Path; import sys; Path(sys.argv[1]).write_text('executed')",
                str(marker),
            ],
            check=True,
            timeout=10,
            cwd=tmp_path,
            env=environment,
            capture_output=True,
        )
        return "Execution completed"

    tool = StructuredTool.from_function(execute)
    middleware = CapabilityMiddleware(
        policy=UserCapabilityPolicy(repos), config_for_agent=lambda: {}
    )
    try:
        yield repos, marker, tool, middleware
    finally:
        db.close()


def test_shared_graph_checks_the_caller_not_an_authorized_previous_user(execution_env) -> None:
    repos, marker, tool, middleware = execution_env
    allowed = repos.user_repo.create(username="allowed", role="user", permissions=["terminal"])
    denied = repos.user_repo.create(username="denied", role="user")
    graph = create_agent(_UntrustedToolModel(), tools=[tool], middleware=[middleware])
    graph.invoke(
        {"messages": [HumanMessage(content="execute")]},
        config={"configurable": {"user": str(allowed)}},
    )
    assert marker.read_text() == "executed"
    marker.unlink()

    result = graph.invoke(
        {"messages": [HumanMessage(content="execute")]},
        config={"configurable": {"user": str(denied)}},
    )

    assert not marker.exists()
    assert any(
        isinstance(message, ToolMessage) and message.status == "error"
        for message in result["messages"]
    )


def test_existing_graph_observes_revocation_before_the_next_execution(execution_env) -> None:
    repos, marker, tool, middleware = execution_env
    user_id = repos.user_repo.create(username="revoked", role="user", permissions=["terminal"])
    graph = create_agent(_UntrustedToolModel(), tools=[tool], middleware=[middleware])
    config = {"configurable": {"user": str(user_id)}}
    graph.invoke({"messages": [HumanMessage(content="execute")]}, config=config)
    assert marker.read_text() == "executed"
    marker.unlink()
    repos.user_repo.set_permissions(user_id, [])

    result = graph.invoke({"messages": [HumanMessage(content="execute")]}, config=config)

    assert not marker.exists()
    assert any(
        isinstance(message, ToolMessage) and message.status == "error"
        for message in result["messages"]
    )


@pytest.mark.parametrize("prompt", ["delegate-default", "delegate-worker"])
@pytest.mark.parametrize("allowed", [False, True])
async def test_delegated_graph_uses_the_callers_permissions(
    execution_env, prompt: str, allowed: bool
) -> None:
    repos, marker, tool, middleware = execution_env
    user_id = repos.user_repo.create(
        username="delegate", role="user", permissions=["terminal"] if allowed else []
    )
    specs = guarded_subagent_specs(
        [
            {
                "name": "worker",
                "description": "Execute the requested task",
                "system_prompt": "Do the task",
                "tools": [tool],
            }
        ],
        middleware,
        parent_tools=[tool],
    )
    graph = create_deep_agent(
        _UntrustedToolModel(), tools=[tool], middleware=[middleware], subagents=specs
    )

    result = await graph.ainvoke(
        {"messages": [HumanMessage(content=prompt)]},
        config={"configurable": {"user": str(user_id)}},
    )

    task_result = next(
        message
        for message in result["messages"]
        if isinstance(message, ToolMessage) and message.name == "task"
    )
    assert task_result.status == "success"
    if allowed:
        assert marker.read_text() == "executed"
    else:
        assert not marker.exists()


@pytest.mark.parametrize("tool_name", ["send_file_to_user", "delete"])
def test_workspace_permission_covers_file_delivery_and_deletion(
    execution_env, tool_name: str
) -> None:
    repos, marker, _execution_tool, middleware = execution_env
    marker.write_text("private-test-document")
    user_id = repos.user_repo.create(username="file-user", role="user")

    def operate(command: str) -> str:
        """Deliver or delete only the isolated test document."""
        if tool_name == "delete":
            marker.unlink()
            return "Deleted"
        return marker.read_text()

    tool = StructuredTool.from_function(operate, name=tool_name)
    graph = create_agent(
        _UntrustedToolModel(requested_tool=tool_name), tools=[tool], middleware=[middleware]
    )
    config = {"configurable": {"user": str(user_id)}}

    denied = graph.invoke({"messages": [HumanMessage(content="operate")]}, config=config)

    assert marker.read_text() == "private-test-document"
    result = next(message for message in denied["messages"] if isinstance(message, ToolMessage))
    assert result.status == "error"
    assert "private-test-document" not in result.content

    repos.user_repo.set_permissions(user_id, ["workspace"])
    allowed = graph.invoke({"messages": [HumanMessage(content="operate")]}, config=config)
    result = next(message for message in allowed["messages"] if isinstance(message, ToolMessage))
    assert result.status == "success"
    if tool_name == "delete":
        assert not marker.exists()
    else:
        assert result.content == "private-test-document"


@pytest.mark.parametrize("async_call", [False, True])
async def test_disabled_account_cannot_start_a_toolless_model_request(
    execution_env, async_call: bool
) -> None:
    repos, request_log, _tool, middleware = execution_env

    class TextModel(BaseChatModel):
        @property
        def _llm_type(self) -> str:
            return "permission-text-probe"

        def get_token_ids(self, text: str) -> list[int]:
            return list(range(len(text.split())))

        def _generate(
            self,
            messages: list[BaseMessage],
            stop: list[str] | None = None,
            run_manager: Any = None,
            **kwargs: Any,
        ) -> ChatResult:
            request_log.write_text("provider invoked", encoding="utf-8")
            return ChatResult(generations=[ChatGeneration(message=AIMessage(content="Ready"))])

    user_id = repos.user_repo.create(username="plain-chat", role="user", permissions=[])
    graph = create_agent(TextModel(), middleware=[middleware])
    config = {"configurable": {"user": str(user_id)}}

    async def invoke() -> Any:
        inputs = {"messages": [HumanMessage(content="Hello")]}
        if async_call:
            return await graph.ainvoke(inputs, config=config)
        return await asyncio.to_thread(graph.invoke, inputs, config=config)

    await invoke()
    assert request_log.read_text(encoding="utf-8") == "provider invoked"
    request_log.unlink()
    repos.user_repo.set_disabled(user_id, True)

    from octop.infra.errors import ErrorCode, OctopError

    with pytest.raises(OctopError) as error:
        await invoke()

    assert error.value.code == ErrorCode.USER_DISABLED
    assert not request_log.exists()
