"""Regression tests for skill package/workspace transfers."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from octop_harness.backends.workspace import BackendWorkspace

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.skill_packages import SkillPackageRepo
from octop.infra.skills import skill_transfer
from octop.infra.skills.skill_package_store import SkillPackageStore
from octop.infra.skills.skill_transfer import (
    SkillTransferNotFound,
    copy_package_skills_to_workspace,
    read_workspace_skill_files,
)


def _workspace(root: Path) -> BackendWorkspace:
    root.mkdir(parents=True, exist_ok=True)
    return BackendWorkspace(LocalShellBackend(root_dir=str(root), virtual_mode=False), root)


@pytest.fixture
def package_store(tmp_path: Path) -> Iterator[SkillPackageStore]:
    db = SqlitePool(tmp_path / "packages.sqlite")
    run_migrations(db)
    try:
        yield SkillPackageStore(repo=SkillPackageRepo(db), root=tmp_path / "packages")
    finally:
        db.close()


@pytest.mark.asyncio
async def test_package_tombstone_is_not_copied_to_workspace(
    package_store: SkillPackageStore,
    tmp_path: Path,
) -> None:
    row = package_store.create(name="P", description="", created_by="1")
    package_store.write_skill(row.id, "gone", [("SKILL.md", b"---\nremoved: true\n---\n")])
    workspace = _workspace(tmp_path / "workspace")

    with pytest.raises(SkillTransferNotFound):
        await copy_package_skills_to_workspace(
            store=package_store,
            package_id=row.id,
            slugs=["gone"],
            workspace=workspace,
        )

    assert await workspace.aread_text("skills/gone/SKILL.md") is None


@pytest.mark.asyncio
async def test_workspace_transfer_file_limit_does_not_count_directories(
    package_store: SkillPackageStore,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace = _workspace(tmp_path / "workspace")
    await workspace.aupload_bytes("skills/demo/SKILL.md", b"# skill")
    await workspace.amkdir("skills/demo/nested/empty")
    monkeypatch.setattr(skill_transfer, "MAX_SKILL_FILES", 1)

    row = package_store.create(name="P", description="", created_by="1")
    await skill_transfer.copy_workspace_skill_to_package(
        workspace=workspace,
        store=package_store,
        package_id=row.id,
        slug="demo",
    )
    restored = package_store.package_skills_dir(row.id) / "demo"
    assert (restored / "SKILL.md").read_bytes() == b"# skill"
    assert (restored / "nested" / "empty").is_dir()


@pytest.mark.asyncio
async def test_workspace_zip_roundtrip_retains_empty_directories(
    package_store: SkillPackageStore, tmp_path: Path
) -> None:
    from octop.infra.skills.skill_packages import build_skill_zip, read_skill_zip

    source = _workspace(tmp_path / "source")
    destination = _workspace(tmp_path / "destination")
    await source.aupload_bytes("skills/demo/SKILL.md", b"# demo")
    await source.amkdir("skills/demo/references/empty")
    files = await read_workspace_skill_files(source, "demo")
    restored = read_skill_zip(build_skill_zip({"demo": files}))["demo"]
    row = package_store.create(name="P", description="", created_by="1")
    package_store.write_skill(row.id, "demo", restored)

    await copy_package_skills_to_workspace(
        store=package_store, package_id=row.id, slugs=["demo"], workspace=destination
    )

    assert await destination.adownload_bytes("skills/demo/SKILL.md") == b"# demo"
    assert await destination.aexists("skills/demo/references/empty")
