"""Moving a document or folder inside a knowledge base."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.config import OctopConfig
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.services import RepoBundle, SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.sharing.service import SharingService
from octop.infra.utils.paths import PathLayout


@pytest.fixture
def env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("OCTOP_HOME", str(tmp_path / "home"))
    pool = SqlitePool(tmp_path / "move.db")
    run_migrations(pool)
    services = SharedServices(PathLayout.from_env(), OctopConfig(), RepoBundle.from_pool(pool))
    services.settings_repo.set("knowledge_bases_enabled", "true")
    owner = services.user_repo.create(username="owner", password_hash="h", role="user")
    base = services.knowledge_repo.create_base(owner_user_id=owner, name="Move")
    knowledge = KnowledgeService(services)
    try:
        yield {"services": services, "base": base, "owner": owner, "knowledge": knowledge}
    finally:
        pool.close()


def _upload(env: dict, filename: str, *, folder: str = "") -> str:
    document = env["knowledge"].upload_document(
        env["base"].id,
        actor_user_id=env["owner"],
        filename=filename,
        content_type="text/plain",
        content=b"hello",
        path=f"{folder}/{filename}" if folder else None,
    )
    return document.id


@pytest.fixture
def tree(env: dict) -> dict:
    knowledge = env["knowledge"]
    base_id = env["base"].id
    owner = env["owner"]
    knowledge.create_folder(base_id, actor_user_id=owner, path="图纸")
    knowledge.create_folder(base_id, actor_user_id=owner, path="台账")
    file_id = _upload(env, "a.txt", folder="图纸")
    return {**env, "file_id": file_id}


def test_move_file_between_folders(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]

    moved = knowledge.move_document(
        base_id, tree["file_id"], target_folder="台账", actor_user_id=owner
    )

    assert moved.path == "台账/a.txt"
    assert moved.filename == "a.txt"


def test_move_file_to_root(tree: dict) -> None:
    moved = tree["knowledge"].move_document(
        tree["base"].id, tree["file_id"], target_folder="", actor_user_id=tree["owner"]
    )

    assert moved.path == "a.txt"


def test_folder_move_carries_descendants(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    folder = knowledge._repo.get_document_by_path(base_id, "图纸")

    moved = knowledge.move_document(base_id, folder.id, target_folder="台账", actor_user_id=owner)

    assert moved.path == "台账/图纸"
    descendants = [
        row.path
        for row in knowledge._repo.list_documents(base_id)
        if row.path.startswith("台账/图纸/")
    ]
    assert descendants == ["台账/图纸/a.txt"]


@pytest.mark.parametrize("target_folder", ["图纸", "图纸/子目录"])
def test_folder_cannot_move_into_itself_or_descendant(tree: dict, target_folder: str) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    knowledge.create_folder(base_id, actor_user_id=owner, path="图纸/子目录")
    folder = knowledge._repo.get_document_by_path(base_id, "图纸")

    with pytest.raises(OctopError) as raised:
        knowledge.move_document(
            base_id, folder.id, target_folder=target_folder, actor_user_id=owner
        )
    assert raised.value.code == ErrorCode.KNOWLEDGE_MOVE_INVALID


def test_move_refuses_name_clash(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    _upload(tree, "a.txt", folder="台账")

    with pytest.raises(OctopError) as raised:
        knowledge.move_document(base_id, tree["file_id"], target_folder="台账", actor_user_id=owner)
    assert raised.value.code == ErrorCode.KNOWLEDGE_MOVE_NAME_TAKEN


@pytest.mark.parametrize("target_folder", ["不存在", "../escape"])
def test_move_refuses_missing_or_invalid_folder(tree: dict, target_folder: str) -> None:
    with pytest.raises(LookupError, match="knowledge folder not found"):
        tree["knowledge"].move_document(
            tree["base"].id,
            tree["file_id"],
            target_folder=target_folder,
            actor_user_id=tree["owner"],
        )


def test_move_refuses_file_as_destination(tree: dict) -> None:
    file_id = tree["file_id"]
    with pytest.raises(LookupError, match="knowledge folder not found"):
        tree["knowledge"].move_document(
            tree["base"].id,
            file_id,
            target_folder="图纸/a.txt",
            actor_user_id=tree["owner"],
        )


def test_move_refuses_document_from_another_base(tree: dict) -> None:
    other = tree["services"].knowledge_repo.create_base(owner_user_id=tree["owner"], name="Other")
    with pytest.raises(LookupError):
        tree["knowledge"].move_document(
            other.id,
            tree["file_id"],
            target_folder="",
            actor_user_id=tree["owner"],
        )


def test_move_folder_rewrites_only_its_exact_path_subtree(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    knowledge.create_folder(base_id, actor_user_id=owner, path="图纸-old")
    folder = knowledge._repo.get_document_by_path(base_id, "图纸")
    moved = knowledge.move_document(base_id, folder.id, target_folder="台账", actor_user_id=owner)

    paths = {row.path for row in knowledge._repo.list_documents(base_id)}
    assert moved.path == "台账/图纸"
    assert "台账/图纸/a.txt" in paths
    assert "图纸-old" in paths


def test_move_to_current_parent_is_noop(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    original = knowledge._repo.get_document(tree["file_id"])

    moved = knowledge.move_document(
        base_id, tree["file_id"], target_folder="图纸", actor_user_id=owner
    )

    assert moved == original


def test_move_folder_to_root_carries_descendants(tree: dict) -> None:
    knowledge, base_id, owner = tree["knowledge"], tree["base"].id, tree["owner"]
    folder = knowledge.create_folder(base_id, actor_user_id=owner, path="台账/归档")
    document = _upload(tree, "record.txt", folder="台账/归档")

    moved = knowledge.move_document(base_id, folder.id, target_folder="", actor_user_id=owner)

    assert moved.path == "归档"
    assert knowledge._repo.get_document(document).path == "归档/record.txt"


def test_move_requires_write_access_to_target_folder(tree: dict) -> None:
    knowledge, services = tree["knowledge"], tree["services"]
    base_id, owner = tree["base"].id, tree["owner"]
    contributor = services.user_repo.create(username="contributor", password_hash="h", role="user")
    SharingService(services.db).apply_change(
        owner,
        "knowledge_base",
        base_id,
        AclEntry(
            "knowledge_base",
            base_id,
            owner,
            "private",
            None,
            0,
            grants=(("user", str(contributor)),),
            permission="write",
        ),
    )
    target = knowledge._repo.get_document_by_path(base_id, "台账")
    services.repos.resource_acl_repo.upsert(
        AclEntry(
            "knowledge_document",
            target.id,
            owner,
            "private",
            None,
            1,
            grants=(("user", str(contributor)),),
            permission="read",
        )
    )

    with pytest.raises(PermissionError, match="document write"):
        knowledge.move_document(
            base_id,
            tree["file_id"],
            target_folder="台账",
            actor_user_id=contributor,
        )
    assert knowledge._repo.get_document_by_path(base_id, "图纸/a.txt") is not None


def test_folder_move_requires_write_access_to_every_descendant(tree: dict) -> None:
    knowledge, services = tree["knowledge"], tree["services"]
    base_id, owner = tree["base"].id, tree["owner"]
    writer = services.user_repo.create(username="writer", password_hash="h", role="user")
    SharingService(services.db).apply_change(
        owner,
        "knowledge_base",
        base_id,
        AclEntry(
            "knowledge_base",
            base_id,
            owner,
            "private",
            None,
            0,
            grants=(("user", str(writer)),),
            permission="write",
        ),
    )
    file_id = tree["file_id"]
    services.repos.resource_acl_repo.upsert(
        AclEntry(
            "knowledge_document",
            file_id,
            owner,
            "private",
            None,
            1,
            grants=(("user", str(writer)),),
            permission="read",
        )
    )
    folder = knowledge._repo.get_document_by_path(base_id, "图纸")

    with pytest.raises(PermissionError, match="document write"):
        knowledge.move_document(base_id, folder.id, target_folder="台账", actor_user_id=writer)

    assert knowledge._repo.get_document_by_path(base_id, "图纸/a.txt") is not None
    assert knowledge._repo.get_document_by_path(base_id, "台账/图纸") is None
