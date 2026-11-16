"""Tests for exported published-expert snapshots."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.agents.builtin_skills import OCTOP_BUILTIN_SKILLS_ROOT
from octop.infra.agents.experts import published_creation
from octop.infra.agents.experts.catalog import (
    MANIFEST_FILENAME,
    WORKSPACE_MANIFEST_PATH,
    seed_expert_directory,
)
from octop.infra.agents.experts.publish import (
    PublishedExpertSnapshotMeta,
    assert_can_mutate_published,
    export_agent_workspace_to_dir,
    resolve_published_expert_slug,
)
from octop.infra.agents.experts.published_creation import refresh_published_expert
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.published_experts import PublishedExpertRepo, PublishedExpertRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.users.identity import Role, User


def _workspace(root: Path) -> BackendWorkspace:
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


def test_only_creator_or_admin_can_mutate_published_expert() -> None:
    row = PublishedExpertRow(
        id="01",
        pk=1,
        slug="researcher",
        name="Researcher",
        description="",
        created_by="7",
        source_agent_id=None,
        icon_name="",
        color="",
        created_at=0,
        updated_at=0,
    )
    owner = User(id=7, username="owner", role=Role.USER, display_name=None)
    admin = User(id=8, username="admin", role=Role.ADMIN, display_name=None)
    other = User(id=9, username="other", role=Role.USER, display_name=None)

    assert_can_mutate_published(row, owner)
    assert_can_mutate_published(row, admin)
    with pytest.raises(OctopError) as exc:
        assert_can_mutate_published(row, other)
    assert exc.value.code is ErrorCode.FORBIDDEN


def test_resolve_published_expert_slug_retries_derived_name_and_rejects_taken_explicit_slug() -> (
    None
):
    class Repo:
        def __init__(self) -> None:
            self.slugs = {"source-expert", "chosen-slug"}

        def get_by_slug(self, slug: str) -> object | None:
            return object() if slug in self.slugs else None

    repo = Repo()

    assert resolve_published_expert_slug(repo=repo, name="Source Expert") == "source-expert-2"
    with pytest.raises(OctopError) as exc:
        resolve_published_expert_slug(repo=repo, name="Other", slug="chosen-slug")
    assert exc.value.code is ErrorCode.PUBLISHED_EXPERT_SLUG_TAKEN


@pytest.mark.asyncio
async def test_export_snapshot_writes_manifest_and_seed_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    manifest = {
        "id": "source-agent",
        "label": {"zh": "来源专家", "en": "Source expert"},
        "description": {"zh": "说明", "en": "Description"},
        "quick_prompts": [{"title": {"zh": "开始", "en": "Start"}}],
    }
    await source.aupload_many(
        [
            (WORKSPACE_MANIFEST_PATH, json.dumps(manifest, ensure_ascii=False).encode()),
            ("SOUL.md", b"# Source soul"),
            ("MEMORY.md", b"# Shared memory"),
            ("skills/research/SKILL.md", b"# Research"),
            (".env", b"API_KEY=secret"),
            ("inbound/note.txt", b"user upload"),
            ("daily/2026-01-01.md", b"journal"),
            ("uploads/tmp.bin", b"binary"),
            (".maitu/avatar.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
        ]
    )

    destination = tmp_path / "published"
    exported = await export_agent_workspace_to_dir(workspace=source, dest=destination)

    assert set(exported) == {
        MANIFEST_FILENAME,
        "SOUL.md",
        "skills/research/SKILL.md",
        ".maitu/avatar.png",
    }
    assert json.loads((destination / MANIFEST_FILENAME).read_text(encoding="utf-8")) == manifest
    assert (destination / "SOUL.md").read_text(encoding="utf-8") == "# Source soul"
    assert not (destination / "MEMORY.md").exists()
    assert (destination / "skills" / "research" / "SKILL.md").read_text(
        encoding="utf-8"
    ) == "# Research"
    assert not (destination / ".env").exists()
    assert not (destination / "inbound").exists()
    assert not (destination / "daily").exists()
    assert not (destination / "uploads").exists()

    installed_dir = tmp_path / "installed"
    installed_dir.mkdir()
    copied = await seed_expert_directory(
        expert_dir=destination,
        workspace=_workspace(installed_dir),
    )

    assert copied == 4
    assert (installed_dir / ".maitu" / "manifest.json").read_text(encoding="utf-8") == json.dumps(
        manifest,
        ensure_ascii=False,
    )
    assert (installed_dir / "skills" / "research" / "SKILL.md").read_bytes() == b"# Research"
    assert (installed_dir / ".maitu" / "avatar.png").read_bytes().startswith(b"\x89PNG")
    assert not (installed_dir / "MEMORY.md").exists()


@pytest.mark.asyncio
async def test_export_snapshot_rejects_invalid_manifest(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many([(WORKSPACE_MANIFEST_PATH, b"not json")])

    with pytest.raises(ValueError, match="valid JSON object"):
        await export_agent_workspace_to_dir(workspace=source, dest=tmp_path / "published")


@pytest.mark.asyncio
async def test_export_snapshot_keeps_existing_directory_when_export_fails(
    tmp_path: Path,
) -> None:
    class FailingWorkspace:
        async def adownload_bytes(self, path: str) -> bytes:
            if path in {MANIFEST_FILENAME, WORKSPACE_MANIFEST_PATH}:
                return b"{}"
            raise RuntimeError("workspace download failed")

        async def aglob(self, _pattern: str) -> SimpleNamespace:
            return SimpleNamespace(matches=[{"path": "SOUL.md", "is_dir": False}])

    destination = tmp_path / "published"
    destination.mkdir()
    (destination / "existing.txt").write_text("keep me", encoding="utf-8")

    with pytest.raises(RuntimeError, match="workspace download failed"):
        await export_agent_workspace_to_dir(
            workspace=FailingWorkspace(),  # type: ignore[arg-type]
            dest=destination,
        )

    assert [path.name for path in destination.iterdir()] == ["existing.txt"]
    assert (destination / "existing.txt").read_text(encoding="utf-8") == "keep me"


@pytest.mark.asyncio
async def test_export_snapshot_excludes_runtime_workspace_dirs(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    manifest = {
        "id": "source-agent",
        "label": {"zh": "来源专家", "en": "Source expert"},
        "description": {"zh": "说明", "en": "Description"},
    }
    await source.aupload_many(
        [
            (MANIFEST_FILENAME, json.dumps(manifest, ensure_ascii=False).encode()),
            ("SOUL.md", b"# Source soul"),
            (f"{OCTOP_BUILTIN_SKILLS_ROOT}/skill-manager/SKILL.md", b"# Builtin"),
            ("logs/agent.log", b"runtime log line"),
        ]
    )

    destination = tmp_path / "published"
    exported = await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=PublishedExpertSnapshotMeta(
            name="Published Expert",
            description="Published description",
            icon_name=None,
            color=None,
            label_zh="发布专家",
            label_en="Published Expert",
            welcome_message_zh="",
            welcome_message_en="",
        ),
        manifest_id="published-expert",
    )

    assert set(exported) == {MANIFEST_FILENAME, "SOUL.md"}
    assert not (destination / OCTOP_BUILTIN_SKILLS_ROOT).exists()
    assert not (destination / "logs").exists()
    merged = json.loads((destination / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert merged["id"] == "published-expert"
    assert merged["label"] == {"zh": "发布专家", "en": "Published Expert"}
    assert merged["description"] == {
        "zh": "Published description",
        "en": "Published description",
    }


@pytest.mark.asyncio
async def test_export_snapshot_excludes_memory_sqlite_files(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many(
        [
            ("SOUL.md", b"# Source soul"),
            ("memory.sqlite", b"sqlite"),
            ("memory.sqlite-wal", b"wal"),
            ("memory.sqlite-shm", b"shm"),
        ]
    )

    destination = tmp_path / "published"
    exported = await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=PublishedExpertSnapshotMeta(
            name="Expert",
            description="desc",
            icon_name=None,
            color=None,
            label_zh="专家",
            label_en="Expert",
            welcome_message_zh="欢迎",
            welcome_message_en="Welcome",
        ),
        manifest_id="expert",
    )

    assert set(exported) == {MANIFEST_FILENAME, "SOUL.md"}
    assert not (destination / "memory.sqlite").exists()


@pytest.mark.asyncio
async def test_export_snapshot_builds_manifest_from_publish_metadata_only(
    tmp_path: Path,
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    manifest = {
        "id": "general-assistant",
        "label": {"zh": "旧名称", "en": "Old name"},
        "description": {"zh": "旧说明", "en": "Old description"},
        "welcome_message": {"zh": "欢迎回来", "en": "Welcome back"},
        "quick_prompts": [{"title": {"zh": "开始", "en": "Start"}}],
        "task_examples": {"zh": ["工作区示例"], "en": ["Workspace example"]},
        "icon_name": "zap",
        "color": "#111111",
        "skillhub": {"manifest_generated": {"by": "test"}},
    }
    await source.aupload_many(
        [
            (MANIFEST_FILENAME, json.dumps(manifest, ensure_ascii=False).encode()),
            ("SOUL.md", b"# Source soul"),
        ]
    )

    destination = tmp_path / "published-expert"
    await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=PublishedExpertSnapshotMeta(
            name="Research Expert",
            description="Helps with research",
            icon_name="search",
            color="#123456",
            label_zh="研究专家",
            label_en="Research Expert",
            welcome_message_zh="开始研究吧",
            welcome_message_en="Start researching",
        ),
        manifest_id="research-expert",
    )

    merged = json.loads((destination / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert merged["id"] == "research-expert"
    assert merged["label"] == {"zh": "研究专家", "en": "Research Expert"}
    assert merged["description"] == {
        "zh": "Helps with research",
        "en": "Helps with research",
    }
    assert merged["welcome_message"] == {
        "zh": "开始研究吧",
        "en": "Start researching",
    }
    assert "quick_prompts" not in merged
    assert "task_examples" not in merged
    assert "skillhub" not in merged
    assert merged["icon_name"] == "search"
    assert merged["color"] == "#123456"
    assert merged["prompt_files"] == ["SOUL.md"]


@pytest.mark.asyncio
async def test_export_snapshot_writes_task_examples_from_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many(
        [
            (MANIFEST_FILENAME, json.dumps({"id": "source"}).encode()),
            ("SOUL.md", b"# Source soul"),
        ]
    )

    destination = tmp_path / "published-expert"
    await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=PublishedExpertSnapshotMeta(
            name="Research Expert",
            description="Helps with research",
            icon_name=None,
            color=None,
            label_zh="研究专家",
            label_en="Research Expert",
            welcome_message_zh="",
            welcome_message_en="",
            task_examples={"zh": ["每天巡检"], "en": ["Daily patrol"]},
        ),
        manifest_id="research-expert",
    )

    merged = json.loads((destination / MANIFEST_FILENAME).read_text(encoding="utf-8"))
    assert merged["task_examples"] == {"zh": ["每天巡检"], "en": ["Daily patrol"]}


@pytest.mark.asyncio
async def test_export_snapshot_includes_subagent_definitions(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many(
        [
            ("SOUL.md", b"# Soul"),
            ("references/extra.json", b"{}"),
            ("agents/reviewer.md", b"# Reviewer"),
            ("skills/demo/SKILL.md", b"# Skill"),
        ]
    )

    destination = tmp_path / "published"
    exported = await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=PublishedExpertSnapshotMeta(
            name="Expert",
            description="desc",
            icon_name=None,
            color=None,
            label_zh="专家",
            label_en="Expert",
            welcome_message_zh="",
            welcome_message_en="",
        ),
        manifest_id="expert",
    )

    assert set(exported) == {
        MANIFEST_FILENAME,
        "SOUL.md",
        "agents/reviewer.md",
        "skills/demo/SKILL.md",
    }
    assert not (destination / "references").exists()


@pytest.mark.asyncio
async def test_export_snapshot_builds_manifest_from_publish_metadata(tmp_path: Path) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many([("SOUL.md", b"# Source soul")])
    metadata = PublishedExpertSnapshotMeta(
        name="Research Expert",
        description="Helps with research",
        icon_name="search",
        color="#123456",
        label_zh="研究专家",
        label_en="Research Expert",
        welcome_message_zh="开始研究",
        welcome_message_en="Start researching",
    )

    destination = tmp_path / "research-expert"
    exported = await export_agent_workspace_to_dir(
        workspace=source,
        dest=destination,
        metadata=metadata,
        manifest_id="research-expert",
    )

    assert set(exported) == {MANIFEST_FILENAME, "SOUL.md"}
    assert json.loads((destination / MANIFEST_FILENAME).read_text(encoding="utf-8")) == {
        "id": "research-expert",
        "label": {"zh": "研究专家", "en": "Research Expert"},
        "description": {"zh": "Helps with research", "en": "Helps with research"},
        "welcome_message": {"zh": "开始研究", "en": "Start researching"},
        "icon_name": "search",
        "color": "#123456",
        "prompt_files": ["SOUL.md"],
    }


@pytest.mark.asyncio
async def test_export_from_legacy_layout_reads_old_system_dir(tmp_path: Path) -> None:
    """Publishing from an unmigrated workspace still carries skills and the avatar."""
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_many(
        [
            (WORKSPACE_MANIFEST_PATH, json.dumps({"id": "legacy"}).encode()),
            (".octop/skills/old/SKILL.md", b"# Old skill"),
            (".octop/agents/reviewer.md", b"# Reviewer"),
            (".octop/avatar.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 8),
        ]
    )

    destination = tmp_path / "published"
    exported = await export_agent_workspace_to_dir(workspace=source, dest=destination)

    # The old system prefix is stripped: a snapshot stays layout-neutral.
    assert set(exported) == {
        MANIFEST_FILENAME,
        "skills/old/SKILL.md",
        "agents/reviewer.md",
        ".maitu/avatar.png",
    }
    assert (destination / "skills" / "old" / "SKILL.md").read_bytes() == b"# Old skill"
    assert (destination / ".maitu" / "avatar.png").is_file()


@pytest.mark.asyncio
async def test_install_legacy_snapshot_writes_current_system_layout(tmp_path: Path) -> None:
    """A snapshot authored by an older build installs under the current directory names."""
    snapshot_dir = tmp_path / "snapshot"
    (snapshot_dir / ".octop" / "skills" / "old").mkdir(parents=True)
    (snapshot_dir / ".octop" / "skills" / "old" / "SKILL.md").write_bytes(b"# Old skill")
    (snapshot_dir / MANIFEST_FILENAME).write_text(json.dumps({"id": "legacy"}), encoding="utf-8")

    installed_dir = tmp_path / "installed"
    installed_dir.mkdir()
    await seed_expert_directory(
        expert_dir=snapshot_dir,
        workspace=_workspace(installed_dir),
    )

    assert (installed_dir / ".maitu" / "skills" / "old" / "SKILL.md").read_bytes() == b"# Old skill"
    assert not (installed_dir / ".octop").exists()
    assert (installed_dir / ".maitu" / MANIFEST_FILENAME).is_file()


@pytest.mark.asyncio
@pytest.mark.parametrize("unsafe_path", ["skills/Z:/payload.md", "skills/guide.md:payload"])
async def test_export_snapshot_rejects_windows_drive_and_stream_components(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, unsafe_path: str
) -> None:
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_bytes(MANIFEST_FILENAME, b"{}")
    original_glob = BackendWorkspace.aglob
    original_download = BackendWorkspace.adownload_bytes

    async def backend_glob(workspace, pattern):
        if workspace is source and pattern == "skills/**/*":
            return SimpleNamespace(matches=[{"path": unsafe_path, "is_dir": False}])
        return await original_glob(workspace, pattern)

    async def backend_download(workspace, path):
        if workspace is source and path == unsafe_path:
            return b"untrusted backend content"
        return await original_download(workspace, path)

    monkeypatch.setattr(BackendWorkspace, "aglob", backend_glob)
    monkeypatch.setattr(BackendWorkspace, "adownload_bytes", backend_download)
    destination = tmp_path / "published"
    destination.mkdir()
    existing = destination / "existing.md"
    existing.write_bytes(b"previous snapshot")

    with pytest.raises(ValueError):
        await export_agent_workspace_to_dir(workspace=source, dest=destination)
    assert existing.read_bytes() == b"previous snapshot"
    assert sorted(path.name for path in destination.iterdir()) == ["existing.md"]


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_metadata_update", [False, True])
async def test_refresh_keeps_snapshot_and_repo_metadata_consistent_on_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    fail_metadata_update: bool,
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = PublishedExpertRepo(pool)
    row = repo.create(
        id="01TESTEXPERT000000000010",
        slug="refresh-target",
        name="Original name",
        description="Original description",
        created_by="7",
        source_agent_id="source-agent",
        icon_name="original",
        color="#111111",
    )
    snapshot_dir = tmp_path / "published" / row.id
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / MANIFEST_FILENAME).write_text('{"id":"refresh-target"}', encoding="utf-8")
    (snapshot_dir / "SOUL.md").write_text("# Original", encoding="utf-8")

    source_dir = tmp_path / "workspace"
    source_dir.mkdir()
    workspace = _workspace(source_dir)
    await workspace.aupload_bytes("SOUL.md", b"# Refreshed")
    registry = SimpleNamespace(
        get_row=lambda _agent_id: None,
        get_config=lambda _agent_id: {},
    )
    services = SimpleNamespace(
        published_expert_repo=repo,
        paths=SimpleNamespace(published_experts_dir=snapshot_dir.parent),
    )
    user = User(id=7, username="owner", role=Role.USER, display_name=None)

    if fail_metadata_update:

        def fail_update(*_args: Any, **_kwargs: Any) -> None:
            raise RuntimeError("metadata update failed")

        monkeypatch.setattr(repo, "update_snapshot_meta", fail_update)
        with pytest.raises(RuntimeError, match="metadata update failed"):
            await refresh_published_expert(
                services=services,
                registry=registry,
                user=user,
                expert_id=row.id,
                source=SimpleNamespace(
                    agent_id="source-agent",
                    description="Refreshed description",
                    icon="sparkles",
                ),
                workspace=workspace,
                name="Refreshed name",
                description="Refreshed description",
            )
        current = repo.get(row.id)
        assert current is not None and current.name == "Original name"
        assert (snapshot_dir / "SOUL.md").read_bytes() == b"# Original"
    else:
        updated = await refresh_published_expert(
            services=services,
            registry=registry,
            user=user,
            expert_id=row.id,
            source=SimpleNamespace(
                agent_id="source-agent",
                description="Refreshed description",
                icon="sparkles",
            ),
            workspace=workspace,
            name="Refreshed name",
            description="Refreshed description",
        )
        current = repo.get(row.id)
        assert current is not None and current.name == updated.name == "Refreshed name"
        assert current.description == "Refreshed description"
        assert (snapshot_dir / "SOUL.md").read_bytes() == b"# Refreshed"
    pool.close()


@pytest.mark.asyncio
async def test_concurrent_refresh_failure_preserves_latest_committed_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = PublishedExpertRepo(pool)
    row = repo.create(
        id="01TESTEXPERT000000000011",
        slug="concurrent-refresh",
        name="Original",
        description="Original description",
        created_by="7",
        source_agent_id="source-agent",
    )
    snapshot_dir = tmp_path / "published" / row.id
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / MANIFEST_FILENAME).write_text('{"id":"concurrent-refresh"}', encoding="utf-8")
    (snapshot_dir / "SOUL.md").write_bytes(b"# Original")

    workspace_a_dir = tmp_path / "workspace-a"
    workspace_b_dir = tmp_path / "workspace-b"
    workspace_a_dir.mkdir()
    workspace_b_dir.mkdir()
    workspace_a = _workspace(workspace_a_dir)
    workspace_b = _workspace(workspace_b_dir)
    await workspace_a.aupload_bytes("SOUL.md", b"# Refresh A")
    await workspace_b.aupload_bytes("SOUL.md", b"# Refresh B")
    registry = SimpleNamespace(get_row=lambda _agent_id: None, get_config=lambda _agent_id: {})
    services = SimpleNamespace(
        published_expert_repo=repo,
        paths=SimpleNamespace(published_experts_dir=snapshot_dir.parent),
    )
    user = User(id=7, username="owner", role=Role.USER, display_name=None)

    original_export = published_creation.export_agent_workspace_to_dir
    refresh_a_waiting = asyncio.Event()
    release_refresh_a = asyncio.Event()

    async def delay_refresh_a(**kwargs: Any) -> list[str]:
        if kwargs["workspace"] is workspace_a:
            refresh_a_waiting.set()
            await release_refresh_a.wait()
        return await original_export(**kwargs)

    original_update = repo.update_snapshot_meta

    def fail_refresh_a(expert_id: str, **kwargs: Any) -> PublishedExpertRow:
        if kwargs["name"] == "Refresh A":
            raise RuntimeError("refresh A metadata failed")
        return original_update(expert_id, **kwargs)

    monkeypatch.setattr(published_creation, "export_agent_workspace_to_dir", delay_refresh_a)
    monkeypatch.setattr(repo, "update_snapshot_meta", fail_refresh_a)

    refresh_a = asyncio.create_task(
        refresh_published_expert(
            services=services,
            registry=registry,
            user=user,
            expert_id=row.id,
            source=SimpleNamespace(agent_id="source-agent", description="A", icon=""),
            workspace=workspace_a,
            name="Refresh A",
            description="A",
        )
    )
    await refresh_a_waiting.wait()
    await refresh_published_expert(
        services=services,
        registry=registry,
        user=user,
        expert_id=row.id,
        source=SimpleNamespace(agent_id="source-agent", description="B", icon=""),
        workspace=workspace_b,
        name="Refresh B",
        description="B",
    )
    release_refresh_a.set()
    with pytest.raises(RuntimeError, match="refresh A metadata failed"):
        await refresh_a

    current = repo.get(row.id)
    assert current is not None and current.name == "Refresh B"
    assert current.description == "B"
    assert (snapshot_dir / "SOUL.md").read_bytes() == b"# Refresh B"
    pool.close()


@pytest.mark.asyncio
async def test_unpublish_during_refresh_does_not_recreate_the_snapshot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = PublishedExpertRepo(pool)
    row = repo.create(
        id="01TESTEXPERT000000000012",
        slug="deleted-during-refresh",
        name="Original",
        description="Original description",
        created_by="7",
        source_agent_id="source-agent",
    )
    snapshot_dir = tmp_path / "published" / row.id
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "SOUL.md").write_bytes(b"# Original")
    source_dir = tmp_path / "workspace"
    source_dir.mkdir()
    workspace = _workspace(source_dir)
    await workspace.aupload_bytes("SOUL.md", b"# Refreshed")
    services = SimpleNamespace(
        published_expert_repo=repo,
        paths=SimpleNamespace(published_experts_dir=snapshot_dir.parent),
    )
    registry = SimpleNamespace(get_row=lambda _agent_id: None, get_config=lambda _agent_id: {})
    user = User(id=7, username="owner", role=Role.USER, display_name=None)
    original_export = published_creation.export_agent_workspace_to_dir
    started = asyncio.Event()
    release = asyncio.Event()

    async def delayed_export(**kwargs: Any) -> list[str]:
        started.set()
        await release.wait()
        return await original_export(**kwargs)

    monkeypatch.setattr(published_creation, "export_agent_workspace_to_dir", delayed_export)
    refresh = asyncio.create_task(
        refresh_published_expert(
            services=services,
            registry=registry,
            user=user,
            expert_id=row.id,
            source=SimpleNamespace(agent_id="source-agent", description="new", icon=""),
            workspace=workspace,
        )
    )
    await started.wait()
    try:
        await published_creation.unpublish_expert(services=services, user=user, expert_id=row.id)
    finally:
        release.set()
    with pytest.raises(OctopError) as raised:
        await refresh
    assert raised.value.code == ErrorCode.NOT_FOUND
    assert repo.get(row.id) is None
    assert not snapshot_dir.exists()
    pool.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["refresh", "unpublish"])
async def test_install_pins_template_files_and_metadata_before_agent_creation(
    tmp_path: Path, change: str
) -> None:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    repo = PublishedExpertRepo(pool)
    original = repo.create(
        id="01TESTEXPERT000000000013",
        slug="install-version",
        name="Original template",
        description="Original description",
        created_by="7",
        source_agent_id="source-agent",
    )
    snapshot = tmp_path / "published" / original.id
    (snapshot / "skills" / "reference").mkdir(parents=True)
    (snapshot / "SOUL.md").write_bytes(b"# Original persona")
    (snapshot / "skills" / "reference" / "SKILL.md").write_bytes(b"# Original skill")
    (snapshot / MANIFEST_FILENAME).write_text(
        json.dumps({"welcome_message": {"en": "Original welcome"}}), encoding="utf-8"
    )
    services = SimpleNamespace(
        published_expert_repo=repo,
        paths=SimpleNamespace(published_experts_dir=snapshot.parent),
    )
    publisher = User(id=7, username="publisher", role=Role.USER, display_name=None)
    installer = User(id=9, username="installer", role=Role.USER, display_name=None)
    source_dir = tmp_path / "source"
    source_dir.mkdir()
    source = _workspace(source_dir)
    await source.aupload_bytes("SOUL.md", b"# New persona")
    source_registry = SimpleNamespace(get_row=lambda _id: None, get_config=lambda _id: {})
    installed_dir = tmp_path / "installed"
    installed_dir.mkdir()
    installed_workspace = _workspace(installed_dir)

    class Registry:
        async def create(self, spec, *, defer_bootstrap, workspace_initializer):
            if change == "refresh":
                await refresh_published_expert(
                    services=services,
                    registry=source_registry,
                    user=publisher,
                    expert_id=original.id,
                    source=SimpleNamespace(agent_id="source-agent", description="new", icon=""),
                    workspace=source,
                    name="New template",
                    description="New description",
                    welcome_message_en="New welcome",
                )
            else:
                await published_creation.unpublish_expert(
                    services=services, user=publisher, expert_id=original.id
                )
            row = SimpleNamespace(
                id=42,
                agent_id="installed-agent",
                user_id=installer.id,
                name=spec.name,
                description=spec.description,
                default_model=None,
                last_state="stopped",
            )
            await workspace_initializer(row, installed_workspace)
            return row

        def is_bootstrapped(self, _agent_id):
            return False

    result = await published_creation.install_published_expert(
        services=services,
        registry=Registry(),
        user=installer,
        expert_id=original.id,
        options=published_creation.PublishedExpertInstallOptions(name="Installed agent"),
    )

    assert result["description"] == original.description
    assert (installed_dir / "SOUL.md").read_bytes() == b"# Original persona"
    assert await installed_workspace.adownload_bytes("skills/reference/SKILL.md") == (
        b"# Original skill"
    )
    installed_manifest = json.loads(
        await installed_workspace.adownload_bytes(WORKSPACE_MANIFEST_PATH)
    )
    assert installed_manifest["welcome_message"]["en"] == "Original welcome"
    current = repo.get(original.id)
    if change == "refresh":
        assert current is not None and current.description == "New description"
        assert (snapshot / "SOUL.md").read_bytes() == b"# New persona"
    else:
        assert current is None and not snapshot.exists()
    pool.close()
