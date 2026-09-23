"""Publishing means runnable as declared — the service's reference rule.

The check is deliberately *only* on publish: a draft may name a skill its author is
about to install (that is what training is), while an active definition that names one
nobody has is a step that silently does less than it says. These tests pin which side
of that line each write path lands on.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_workflow as wf
from octop.infra.agents import feature_workflow_service as service
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle
from octop.infra.errors import ErrorCode, OctopError

AUTHOR_ID = 7
FEATURE_ID = "quote-helper"


def _definition(*, status: str, skills: list[str] | None = None) -> dict[str, Any]:
    step: dict[str, Any] = {"id": "extract", "name": "提取", "prompt": "读附件"}
    if skills is not None:
        step["skills"] = skills
    return {"version": 1, "status": status, "steps": [step], "rules": []}


def _workspace(root: Path) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


@pytest.fixture
def repos(tmp_path: Path) -> RepoBundle:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (?, 'author', 'x', 'user', 0)",
            (AUTHOR_ID,),
        )
    return RepoBundle.from_pool(pool)


@pytest.mark.asyncio
async def test_a_draft_may_name_a_skill_that_is_not_installed_yet(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "ws")

    stored = await service.save_definition(workspace, _definition(status="draft", skills=["excel"]))

    assert stored["steps"][0]["skills"] == ["excel"]


@pytest.mark.asyncio
async def test_publishing_with_an_unknown_skill_is_refused_with_what_exists(
    tmp_path: Path,
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await workspace.aupload_bytes("skills/xlsx/SKILL.md", b"---\nname: xlsx\n---\n")

    with pytest.raises(OctopError) as caught:
        await service.save_definition(workspace, _definition(status="active", skills=["excel"]))

    assert caught.value.code is ErrorCode.WORKFLOW_INVALID
    reason = caught.value.details["reason"]
    assert "steps[0].skills names unknown skill(s): excel" in reason
    assert "available: xlsx" in reason
    # Nothing was written by the refusal.
    assert (await wf.load_workflow(workspace)).definition is None


@pytest.mark.asyncio
async def test_publishing_with_an_installed_skill_is_accepted(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "ws")
    await workspace.aupload_bytes("skills/xlsx/SKILL.md", b"---\nname: xlsx\n---\n")

    stored = await service.save_definition(workspace, _definition(status="active", skills=["xlsx"]))

    assert stored["status"] == "active"
    assert (await wf.load_workflow(workspace)).definition == stored


@pytest.mark.asyncio
async def test_an_unknown_subagent_is_refused_too(tmp_path: Path) -> None:
    workspace = _workspace(tmp_path / "ws")
    definition = _definition(status="active")
    definition["steps"][0]["subagents"] = ["writer"]

    with pytest.raises(OctopError) as caught:
        await service.save_definition(workspace, definition)

    assert "subagents names unknown subagent(s): writer" in caught.value.details["reason"]
    assert "has none installed" in caught.value.details["reason"]


@pytest.mark.asyncio
async def test_a_change_that_would_publish_a_dangling_reference_is_refused(
    tmp_path: Path, repos: RepoBundle
) -> None:
    """The rule holds for whichever write path moved the document to active."""
    workspace = _workspace(tmp_path / "ws")
    await service.save_definition(workspace, _definition(status="draft"))

    with pytest.raises(OctopError) as caught:
        await service.apply_change(
            workspace=workspace,
            repos=repos,
            feature_id=FEATURE_ID,
            user_id=AUTHOR_ID,
            target="definition",
            summary="发布并引用一个不存在的技能",
            items=[
                {"path": "/status", "before": "draft", "after": "active"},
                {"path": "/steps/0/skills", "before": None, "after": ["excel"]},
            ],
        )

    assert caught.value.code is ErrorCode.WORKFLOW_INVALID
    # Nothing was written and nothing was recorded: the document is the draft it was.
    stored = (await wf.load_workflow(workspace)).definition
    assert stored is not None and stored["status"] == "draft"
    assert "skills" not in stored["steps"][0]
    assert (
        repos.feature_change_repo.list_for_feature(feature_id=FEATURE_ID, user_id=AUTHOR_ID) == []
    )
