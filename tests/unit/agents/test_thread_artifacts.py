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
    (workspace / "plans").mkdir(parents=True)
    (workspace / "plans" / "review.md").write_text("body", encoding="utf-8")
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
    # A path the tool merely reported is not downloadable while nothing is on disk.
    assert (
        artifacts_for_response([(workspace / "plans" / "missing.md").as_posix()], workspace) == []
    )


def test_artifact_paths_keep_virtual_roots_and_full_nested_paths(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    expected = [
        (workspace / "outbound" / "x.docx").as_posix(),
        (workspace / "inbound" / "y.pdf").as_posix(),
        (workspace / "reports" / "outbound" / "x.docx").as_posix(),
    ]
    assert extract_artifact_paths(
        tool_name="send_file_to_user",
        args={"path": "/outbound/x.docx"},
        workspace_dir=workspace,
    ) == [expected[0]]
    assert extract_artifact_paths(
        tool_name="write_file",
        args={"path": "/inbound/y.pdf"},
        workspace_dir=workspace,
    ) == [expected[1]]
    assert extract_artifact_paths(
        tool_name="write_file",
        args={"path": "reports/outbound/x.docx"},
        workspace_dir=workspace,
    ) == [expected[2]]


def test_artifact_paths_reject_traversal_and_outside_workspace(tmp_path: Path) -> None:
    workspace = tmp_path / "workspace"
    outside = tmp_path / "foreign" / "outbound" / "x.docx"
    assert (
        extract_artifact_paths(
            tool_name="write_file",
            args={"path": "../foreign/outbound/x.docx"},
            workspace_dir=workspace,
        )
        == []
    )
    assert (
        extract_artifact_paths(
            tool_name="write_file",
            args={"path": outside.as_posix()},
            workspace_dir=workspace,
        )
        == []
    )


def test_same_named_artifacts_in_different_directories_remain_distinct(
    tmp_path: Path,
) -> None:
    workspace = tmp_path / "workspace"
    for folder in ("reports", "archive"):
        (workspace / folder / "outbound").mkdir(parents=True)
        (workspace / folder / "outbound" / "x.docx").write_text(folder, encoding="utf-8")
    paths = artifacts_for_response(
        ["reports/outbound/x.docx", "archive/outbound/x.docx"],
        workspace,
    )
    assert paths == [
        (workspace / "reports" / "outbound" / "x.docx").as_posix(),
        (workspace / "archive" / "outbound" / "x.docx").as_posix(),
    ]


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
    (workspace / "plans").mkdir(parents=True)
    (workspace / "plans" / "review.md").write_text("plan body", encoding="utf-8")
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


def test_reported_write_without_a_file_is_not_offered(tmp_path: Path) -> None:
    """A tool that reports success while leaving nothing on disk must not
    become a downloadable artifact: a 404 the user can never resolve is worse
    than no entry at all."""
    workspace = tmp_path / "workspace"
    (workspace / "output").mkdir(parents=True)
    empty = workspace / "output" / "never-written.docx"
    empty.write_bytes(b"")
    threads = _Threads()
    middleware = ThreadArtifactsMiddleware(thread_repo=threads, workspace_dir=workspace)
    result = ToolMessage(content="saved", tool_call_id="tc1")
    with patch(
        "octop.infra.agents.middleware.thread_artifacts.current_thread_id",
        return_value="thread-a",
    ):
        middleware.wrap_tool_call(_request("output/missing.docx"), lambda _req: result)
        middleware.wrap_tool_call(_request("output/never-written.docx"), lambda _req: result)

    assert threads.artifacts == []
    assert (
        artifacts_for_response(
            [empty.as_posix(), (workspace / "output" / "missing.docx").as_posix()], workspace
        )
        == []
    )


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


def test_unverifiable_remote_paths_survive_the_filter(tmp_path: Path) -> None:
    """A remote/S3 workspace has no local parent directory to stat.

    Dropping such a path because this host cannot see it would empty every
    artifact list for object-storage and container backends.
    """
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    # A remote backend keeps the workspace-relative path, but never creates the
    # directory on this host, so the parent is absent.
    remote_like = (workspace / "out" / "bid.docx").as_posix()

    assert artifacts_for_response([remote_like], workspace) == [remote_like]


def test_zero_byte_and_missing_sibling_still_dropped(tmp_path: Path) -> None:
    """The failures the filter exists for stay filtered."""
    workspace = tmp_path / "workspace"
    (workspace / "out").mkdir(parents=True)
    empty = workspace / "out" / "empty.docx"
    empty.write_bytes(b"")

    assert artifacts_for_response([empty.as_posix()], workspace) == []
    assert artifacts_for_response([(workspace / "out" / "never.docx").as_posix()], workspace) == []
