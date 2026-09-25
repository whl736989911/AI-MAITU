"""Produced files are recorded only for their owning workspace and thread."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from langchain_core.messages import ToolMessage

from octop.infra.agents.middleware.thread_artifacts import (
    ThreadArtifactsMiddleware,
    artifacts_for_response,
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
