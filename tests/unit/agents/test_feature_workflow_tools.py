"""Tests for the workflow tools a feature's own agent is given.

What these pin is authorization as much as behaviour: the tools resolve the feature
from the turn's own agent, so "who is asking" is the whole of their access control —
and a tool that edited a feature somebody else owns would be a way to change a
feature's runs without ever passing the API's own checks.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_workflow as wf
from octop.infra.agents import feature_workflow_tools as tools_module
from octop.infra.agents.feature_workflow_tools import build_feature_workflow_tools
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos._base import now_ts
from octop.infra.db.services import RepoBundle

AUTHOR_ID = 7
FEATURE_AGENT = "feat-quote-helper"
FEATURE_ID = "quote-helper"


def _definition(**overrides: Any) -> dict[str, Any]:
    document: dict[str, Any] = {
        "version": 1,
        "status": "draft",
        "steps": [{"id": "draft", "name": "起草", "prompt": "写"}],
        "rules": ["金额逐行核对"],
    }
    document.update(overrides)
    return document


def _workspace(root: Path) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


def _tools(
    monkeypatch: Any,
    *,
    workspace: BackendWorkspace,
    repos: RepoBundle,
    kind: str = "feature",
    owner_id: int = AUTHOR_ID,
    user_id: int = AUTHOR_ID,
) -> dict[str, Any]:
    registry = SimpleNamespace(
        get_row=lambda agent_id: SimpleNamespace(kind=kind, user_id=owner_id),
        workspace_for_agent=lambda agent_id: workspace,
    )
    monkeypatch.setattr(
        tools_module,
        "get_config",
        lambda: {"configurable": {"agent_id": FEATURE_AGENT, "user": str(user_id)}},
    )
    return {
        tool.name: tool for tool in build_feature_workflow_tools(registry=registry, repos=repos)
    }


@pytest.fixture
def repos(tmp_path: Path) -> RepoBundle:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (?, 'author', 'x', 'user', ?)",
            (AUTHOR_ID, now_ts()),
        )
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (99, 'stranger', 'x', 'user', ?)",
            (now_ts(),),
        )
    return RepoBundle.from_pool(pool)


@pytest.mark.asyncio
async def test_get_returns_the_document_problems_and_the_authors_own_overlay(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition())
    repos.feature_overlay_repo.set(feature_id=FEATURE_ID, user_id=AUTHOR_ID, content="我们含税")
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    payload = json.loads(await tools["feature_workflow_get"].ainvoke({"feature_id": ""}))

    assert payload["feature_id"] == FEATURE_ID
    assert payload["workflow"]["steps"][0]["id"] == "draft"
    assert payload["problems"] == []
    assert payload["your_overlay"] == "我们含税"


@pytest.mark.asyncio
async def test_save_writes_a_validated_document_or_lists_every_problem(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    saved = json.loads(
        await tools["feature_workflow_save"].ainvoke({"workflow": _definition(status="active")})
    )
    assert saved["saved"] is True
    assert (await wf.load_workflow(workspace)).definition == _definition(status="active")

    refused = json.loads(
        await tools["feature_workflow_save"].ainvoke(
            {"workflow": _definition(version=2, outputs=[{"name": "x", "form": "pdf"}])}
        )
    )
    assert "version must be 1" in refused["error"]
    assert "form must be one of" in refused["error"]
    # Nothing was written by the refusal.
    assert (await wf.load_workflow(workspace)).definition == _definition(status="active")


@pytest.mark.asyncio
async def test_a_change_is_applied_and_can_be_taken_back(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition())
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    applied = json.loads(
        await tools["feature_workflow_change"].ainvoke(
            {
                "target": "definition",
                "summary": "第 1 步改成确认门",
                "items": [{"path": "/steps/0/gate", "before": None, "after": "confirm"}],
            }
        )
    )

    assert applied["status"] == "applied"
    assert (
        json.loads(await tools["feature_workflow_get"].ainvoke({"feature_id": ""}))["workflow"][
            "steps"
        ][0]["gate"]
        == "confirm"
    )

    undone = json.loads(
        await tools["feature_workflow_revert"].ainvoke({"change_id": applied["change_id"]})
    )
    assert undone["status"] == "reverted"
    stored = (await wf.load_workflow(workspace)).definition
    assert stored is not None and "gate" not in stored["steps"][0]


@pytest.mark.asyncio
async def test_save_derives_step_ids_so_nobody_has_to_invent_them(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    """A model (or a person) writing a document should not be inventing identifiers."""
    workspace = _workspace(tmp_path / "ws")
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    saved = json.loads(
        await tools["feature_workflow_save"].ainvoke(
            {
                "workflow": {
                    "version": 1,
                    "status": "active",
                    "steps": [{"name": "起草", "prompt": "写"}, {"name": "复核", "prompt": "看"}],
                }
            }
        )
    )

    assert saved["saved"] is True
    stored = (await wf.load_workflow(workspace)).definition
    assert stored is not None
    assert [step["id"] for step in stored["steps"]] == ["step_1", "step_2"]


@pytest.mark.asyncio
async def test_a_stale_change_is_refused_and_nothing_is_written(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition(rules=["金额逐行核对", "改过的那条"]))
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    refused = json.loads(
        await tools["feature_workflow_change"].ainvoke(
            {
                "target": "definition",
                "summary": "基于旧值改写",
                "items": [{"path": "/rules/1", "before": "原来的那条", "after": "新的"}],
            }
        )
    )

    assert "stale change" in refused["error"]
    assert refused["details"]["paths"] == "/rules/1"
    stored = (await wf.load_workflow(workspace)).definition
    assert stored is not None and stored["rules"] == ["金额逐行核对", "改过的那条"]
    assert (
        repos.feature_change_repo.list_for_feature(feature_id=FEATURE_ID, user_id=AUTHOR_ID) == []
    )


@pytest.mark.asyncio
async def test_an_overlay_change_edits_only_the_authors_own_text(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition())
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    applied = json.loads(
        await tools["feature_workflow_change"].ainvoke(
            {
                "target": "overlay",
                "summary": "作者自己的偏好",
                "items": [{"path": "/overlay", "before": None, "after": "我们含税"}],
            }
        )
    )

    assert applied["status"] == "applied"
    assert (
        repos.feature_overlay_repo.content(feature_id=FEATURE_ID, user_id=AUTHOR_ID) == "我们含税"
    )
    # The definition is untouched by an overlay change.
    assert (await wf.load_workflow(workspace)).definition == _definition()


@pytest.mark.asyncio
async def test_runs_list_what_the_author_submitted(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition())
    repos.feature_run_repo.record(
        feature_id=FEATURE_ID,
        agent_id=FEATURE_AGENT,
        user_id=AUTHOR_ID,
        thread_id="thr-1",
        inputs={"customer_name": "ACME"},
        definition=_definition(),
    )
    tools = _tools(monkeypatch, workspace=workspace, repos=repos)

    payload = json.loads(await tools["feature_workflow_runs"].ainvoke({"limit": 5}))

    assert len(payload["runs"]) == 1
    assert payload["runs"][0]["inputs"] == {"customer_name": "ACME"}


@pytest.mark.asyncio
async def test_somebody_else_cannot_change_the_feature(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    """The tools resolve the feature from the turn's own agent: it is their access rule."""
    workspace = _workspace(tmp_path / "ws")
    await wf.save_workflow(workspace, _definition())
    tools = _tools(monkeypatch, workspace=workspace, repos=repos, user_id=99)

    refused = json.loads(
        await tools["feature_workflow_save"].ainvoke({"workflow": _definition(status="active")})
    )

    assert "only the feature's author" in refused["error"]
    assert (await wf.load_workflow(workspace)).definition == _definition()


@pytest.mark.asyncio
async def test_an_experts_agent_has_no_workflow_to_change(
    tmp_path: Path, monkeypatch: Any, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    tools = _tools(monkeypatch, workspace=workspace, repos=repos, kind="agent")

    refused = json.loads(await tools["feature_workflow_get"].ainvoke({"feature_id": ""}))

    assert "is not a feature's own agent" in refused["error"]
