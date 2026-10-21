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
FEATURE_AGENT = "feat-quote-helper"


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
    bundle = RepoBundle.from_pool(pool)
    bundle.agent_repo.create(
        agent_id=FEATURE_AGENT, user_id=AUTHOR_ID, name="Quote helper", kind="feature"
    )
    return bundle


@pytest.mark.asyncio
async def test_a_draft_may_name_a_skill_that_is_not_installed_yet(
    tmp_path: Path, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")

    stored = await service.save_definition(
        workspace,
        _definition(status="draft", skills=["excel"]),
        repos=repos,
        agent_id=FEATURE_AGENT,
    )

    assert stored["steps"][0]["skills"] == ["excel"]


@pytest.mark.asyncio
async def test_publishing_with_an_unknown_skill_is_refused_with_what_exists(
    tmp_path: Path, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await workspace.aupload_bytes("skills/xlsx/SKILL.md", b"---\nname: xlsx\n---\n")

    with pytest.raises(OctopError) as caught:
        await service.save_definition(
            workspace,
            _definition(status="active", skills=["excel"]),
            repos=repos,
            agent_id=FEATURE_AGENT,
        )

    assert caught.value.code is ErrorCode.WORKFLOW_INVALID
    reason = caught.value.details["reason"]
    assert "steps[0].skills names unknown skill(s): excel" in reason
    assert "available: xlsx" in reason
    # Nothing was written by the refusal.
    assert (await wf.load_workflow(workspace)).definition is None


@pytest.mark.asyncio
async def test_publishing_with_an_installed_skill_is_accepted(
    tmp_path: Path, repos: RepoBundle
) -> None:
    workspace = _workspace(tmp_path / "ws")
    await workspace.aupload_bytes("skills/xlsx/SKILL.md", b"---\nname: xlsx\n---\n")
    with repos.db.transaction() as conn:
        conn.execute(
            "INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, grantee_id) "
            "VALUES ('agent', ?, 'user', '991')",
            (FEATURE_AGENT,),
        )
        conn.execute(
            "INSERT INTO resource_acl_grants(resource_type, resource_id, grantee_type, grantee_id) "
            "VALUES ('feature', ?, 'user', '992')",
            (FEATURE_ID,),
        )

    stored = await service.save_definition(
        workspace,
        _definition(status="active", skills=["xlsx"]),
        repos=repos,
        agent_id=FEATURE_AGENT,
    )

    assert stored["status"] == "active"
    assert (await wf.load_workflow(workspace)).definition == stored
    owner = repos.agent_repo.get(FEATURE_AGENT)
    assert owner is not None and owner.user_id is None
    assert owner.enterprise_unit_key == "*"
    owners = _ownership_and_acl(repos)[1]
    assert [row[0] for row in owners] == ["agent"]
    grants = _ownership_and_acl(repos)[2]
    assert ("agent", FEATURE_AGENT, "user", "991") in grants
    assert ("feature", FEATURE_ID, "user", "992") in grants


def _ownership_and_acl(repos: RepoBundle) -> tuple[Any, Any, Any]:
    with repos.db.connect() as conn:
        agent = conn.execute(
            "SELECT user_id, enterprise_unit_key FROM agents WHERE agent_id = ?",
            (FEATURE_AGENT,),
        ).fetchone()
        owners = conn.execute(
            "SELECT resource_type, resource_id, owner_user_id FROM resource_acl "
            "WHERE resource_id IN (?, ? ) ORDER BY resource_type",
            (FEATURE_AGENT, FEATURE_ID),
        ).fetchall()
        grants = conn.execute(
            "SELECT resource_type, resource_id, grantee_type, grantee_id "
            "FROM resource_acl_grants WHERE resource_id IN (?, ?) "
            "ORDER BY resource_type, grantee_type, grantee_id",
            (FEATURE_AGENT, FEATURE_ID),
        ).fetchall()
    return tuple(
        tuple(tuple(row) for row in rows) if isinstance(rows, list) else tuple(rows)
        for rows in (agent, owners, grants)
    )


@pytest.mark.asyncio
async def test_failed_workflow_write_preserves_document_owner_and_acl(
    tmp_path: Path, repos: RepoBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "ws")
    previous = await service.save_definition(
        workspace, _definition(status="draft"), repos=repos, agent_id=FEATURE_AGENT
    )
    before_acl = _ownership_and_acl(repos)
    before_text = await workspace.aread_text(wf.WORKSPACE_WORKFLOW_PATH)
    original_write = workspace.awrite_text
    failed = False

    async def fail_first_write(path: str, content: str, **kwargs: Any) -> Any:
        nonlocal failed
        if not failed and ".pending-" in path:
            failed = True
            raise OSError("injected workspace write failure")
        return await original_write(path, content, **kwargs)

    monkeypatch.setattr(workspace, "awrite_text", fail_first_write)
    with pytest.raises(OSError, match="injected workspace write failure"):
        await service.save_definition(
            workspace, _definition(status="active"), repos=repos, agent_id=FEATURE_AGENT
        )

    assert (await wf.load_workflow(workspace)).definition == previous
    assert await workspace.aread_text(wf.WORKSPACE_WORKFLOW_PATH) == before_text
    assert _ownership_and_acl(repos) == before_acl


@pytest.mark.asyncio
async def test_failed_publish_restores_existing_workflow_owner_and_acl(
    tmp_path: Path, repos: RepoBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "ws")
    previous = await service.save_definition(
        workspace, _definition(status="draft"), repos=repos, agent_id=FEATURE_AGENT
    )
    before_acl = _ownership_and_acl(repos)

    def fail_publish(_agent_id: str) -> None:
        raise RuntimeError("injected publish failure")

    monkeypatch.setattr(repos.agent_repo, "publish_feature", fail_publish)
    with pytest.raises(RuntimeError, match="injected publish failure"):
        await service.save_definition(
            workspace, _definition(status="active"), repos=repos, agent_id=FEATURE_AGENT
        )

    assert (await wf.load_workflow(workspace)).definition == previous
    assert _ownership_and_acl(repos) == before_acl


@pytest.mark.asyncio
async def test_failed_install_after_ownership_transfer_rolls_back_database_and_file(
    tmp_path: Path, repos: RepoBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "ws")
    previous = await service.save_definition(
        workspace, _definition(status="draft"), repos=repos, agent_id=FEATURE_AGENT
    )
    before_acl = _ownership_and_acl(repos)
    original_move = workspace.amove

    async def fail_staged_install(source: str, destination: str) -> None:
        if ".pending-" in source and destination == wf.WORKSPACE_WORKFLOW_PATH:
            raise OSError("injected staged install failure")
        await original_move(source, destination)

    monkeypatch.setattr(workspace, "amove", fail_staged_install)
    with pytest.raises(OSError, match="injected staged install failure"):
        await service.save_definition(
            workspace, _definition(status="active"), repos=repos, agent_id=FEATURE_AGENT
        )

    assert (await wf.load_workflow(workspace)).definition == previous
    assert _ownership_and_acl(repos) == before_acl


@pytest.mark.asyncio
async def test_failed_first_publish_removes_staged_workflow_and_keeps_author_acl(
    tmp_path: Path, repos: RepoBundle, monkeypatch: pytest.MonkeyPatch
) -> None:
    workspace = _workspace(tmp_path / "ws")
    before_acl = _ownership_and_acl(repos)

    def fail_publish(_agent_id: str) -> None:
        raise RuntimeError("injected publish failure")

    monkeypatch.setattr(repos.agent_repo, "publish_feature", fail_publish)
    with pytest.raises(RuntimeError, match="injected publish failure"):
        await service.save_definition(
            workspace, _definition(status="active"), repos=repos, agent_id=FEATURE_AGENT
        )

    assert (await wf.load_workflow(workspace)).present is False
    assert _ownership_and_acl(repos) == before_acl


@pytest.mark.asyncio
async def test_an_unknown_subagent_is_refused_too(tmp_path: Path, repos: RepoBundle) -> None:
    workspace = _workspace(tmp_path / "ws")
    definition = _definition(status="active")
    definition["steps"][0]["subagents"] = ["writer"]

    with pytest.raises(OctopError) as caught:
        await service.save_definition(workspace, definition, repos=repos, agent_id=FEATURE_AGENT)

    assert "subagents names unknown subagent(s): writer" in caught.value.details["reason"]
    assert "has none installed" in caught.value.details["reason"]


@pytest.mark.asyncio
async def test_a_change_that_would_publish_a_dangling_reference_is_refused(
    tmp_path: Path, repos: RepoBundle
) -> None:
    """The rule holds for whichever write path moved the document to active."""
    workspace = _workspace(tmp_path / "ws")
    await service.save_definition(
        workspace, _definition(status="draft"), repos=repos, agent_id=FEATURE_AGENT
    )

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
