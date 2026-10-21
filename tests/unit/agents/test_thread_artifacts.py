"""Produced files are recorded only for their owning workspace and thread."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.messages import AIMessage, HumanMessage, ToolMessage

from octop.infra.agents.middleware.thread_artifacts import (
    ThreadArtifactsMiddleware,
    artifacts_for_response,
    encode_team_artifact_ref,
    extract_artifact_paths,
)


class _Threads:
    def __init__(self) -> None:
        self.artifacts: list[tuple[str, list[str]]] = []
        self.pending_plan: str | None = None

    def append_artifacts(self, thread_id: str, paths: list[str]) -> None:
        self.artifacts.append((thread_id, paths))

    def update_composer(self, _thread_id: str, *, pending_plan_path: str) -> None:
        self.pending_plan = pending_plan_path


def _request(path: str) -> MagicMock:
    request = MagicMock()
    request.tool_call = {"name": "write_file", "args": {"path": path}, "id": "tc1"}
    return request


def test_artifact_paths_reject_escaping_the_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    inside = (workspace / "plans" / "review.md").as_posix()
    outside = (tmp_path / "private.md").as_posix()
    assert extract_artifact_paths(
        tool_name="write_file", args={"path": "plans/review.md"}, workspace_dir=workspace
    ) == [inside]
    assert (
        extract_artifact_paths(
            tool_name="write_file", args={"path": "../private.md"}, workspace_dir=workspace
        )
        == []
    )
    assert (
        extract_artifact_paths(
            tool_name="write_file", args={"path": outside}, workspace_dir=workspace
        )
        == []
    )
    assert artifacts_for_response(["plans/review.md", inside, outside], workspace) == [inside]


def test_team_artifact_reference_keeps_producer_and_validates_its_workspace(
    tmp_path: Path,
) -> None:
    host_workspace = tmp_path / "host"
    member_a_workspace = tmp_path / "member-a"
    member_b_workspace = tmp_path / "member-b"
    member_a_path = (member_a_workspace / "out" / "same.md").as_posix()
    member_b_path = (member_b_workspace / "out" / "same.md").as_posix()
    refs = [
        encode_team_artifact_ref("member-a", member_a_path),
        encode_team_artifact_ref("member-b", member_b_path),
    ]
    assert all(refs)
    workspaces = {
        "member-a": member_a_workspace,
        "member-b": member_b_workspace,
    }
    resolved = artifacts_for_response(
        refs,
        host_workspace,
        producer_workspace=lambda producer: workspaces.get(producer),
    )
    assert resolved == [
        {"producer_agent_id": "member-a", "path": member_a_path},
        {"producer_agent_id": "member-b", "path": member_b_path},
    ]
    assert artifacts_for_response(refs, host_workspace) == []


def test_successful_plan_write_tracks_artifact_and_pending_plan(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    result = ToolMessage(content="saved", tool_call_id="tc1")
    with (
        patch(
            "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
            return_value="thread-a",
        ),
        patch(
            "octop.infra.agents.middleware.thread_artifacts.get_config",
            return_value={"configurable": {"conversation_mode": "plan"}},
        ),
    ):
        returned = middleware.wrap_tool_call(_request("plans/review.md"), lambda _req: result)
    assert returned is result
    assert threads.artifacts == [("thread-a", [(workspace / "plans/review.md").as_posix()])]
    assert threads.pending_plan == "plans/review.md"


def test_failed_write_does_not_offer_an_unwritten_plan(tmp_path: Path) -> None:
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=tmp_path)
    failed = ToolMessage(content="denied", tool_call_id="tc1", status="error")
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.wrap_tool_call(_request("plans/review.md"), lambda _req: failed)
    assert threads.artifacts == []
    assert threads.pending_plan is None


def test_agent_snapshot_records_only_declared_script_output(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    unchanged = workspace / "notes.md"
    unchanged.write_text("before", encoding="utf-8")
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.before_agent(None, None)
        unchanged.write_text("after modified", encoding="utf-8")
        generated = workspace / "申报书.docx"
        generated.write_text("binary-ish", encoding="utf-8")
        os.utime(generated, None)
        state = {"messages": [AIMessage(content=f"已生成 {generated.name}")]}
        middleware.after_agent(state, None)

    assert len(threads.artifacts) == 1
    thread_id, paths = threads.artifacts[0]
    assert thread_id == "thread-a"
    assert generated.as_posix() in paths
    assert unchanged.as_posix() not in paths


def test_explicit_send_request_finds_previous_workspace_file(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = workspace / "report.docx"
    report.write_text("already generated", encoding="utf-8")
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.before_agent(None, None)
        state = {"messages": [HumanMessage(content="把 report.docx 发给我")]}
        middleware.after_agent(state, None)

    assert threads.artifacts == [("thread-a", [report.as_posix()])]


def test_helper_script_is_not_auto_delivered(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    script = workspace / "gen_docx.py"
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.before_agent(None, None)
        script.write_text("print('generate')", encoding="utf-8")
        state = {"messages": [AIMessage(content="已生成 gen_docx.py")]}
        middleware.after_agent(state, None)

    assert threads.artifacts == []


def test_explicit_send_request_matches_requested_file_type(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    report = workspace / "report.docx"
    report.write_text("already generated", encoding="utf-8")
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.before_agent(None, None)
        state = {"messages": [HumanMessage(content="把刚才生成的 Word 文件发给我")]}
        middleware.after_agent(state, None)

    assert threads.artifacts == [("thread-a", [report.as_posix()])]
