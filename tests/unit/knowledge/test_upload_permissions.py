"""Bounded upload permission defaults and explicit read-only choices."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.knowledge import KnowledgeRepo
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.db.repos.resource_acl import ResourceAclRepo
from octop.infra.db.repos.settings import SettingsRepo
from octop.infra.db.repos.users import UserRepo
from octop.infra.errors import OctopError
from octop.infra.knowledge.permissions import KnowledgePermissionService
from octop.infra.knowledge.scope import document_access_ids
from octop.infra.sharing import AclEntry


@pytest.fixture
def permission_context(tmp_path: Path) -> SimpleNamespace:
    pool = SqlitePool(tmp_path / "knowledge.db")
    run_migrations(pool)
    knowledge_repo = KnowledgeRepo(pool)
    resource_acl_repo = ResourceAclRepo(pool)
    services = SimpleNamespace(
        db=pool,
        knowledge_repo=knowledge_repo,
        resource_acl_repo=resource_acl_repo,
        org_unit_repo=OrgUnitRepo(pool),
        settings_repo=SettingsRepo(pool),
        user_repo=UserRepo(pool),
    )
    services.repos = SimpleNamespace(
        knowledge_repo=knowledge_repo,
        resource_acl_repo=resource_acl_repo,
        org_unit_repo=services.org_unit_repo,
    )
    actor = services.user_repo.create(username="owner", password_hash="unused", role="user")
    base = knowledge_repo.create_base(owner_user_id=actor, name="Private")
    try:
        yield SimpleNamespace(pool=pool, services=services, actor=actor, base=base)
    finally:
        pool.close()


def test_omitted_upload_choice_persists_maximum_private_boundary(permission_context):
    env = permission_context
    acl = KnowledgePermissionService(env.services).validate_upload_access(
        env.base.id,
        folder="",
        data_source_id=None,
        actor_user_id=env.actor,
        scope=None,
        permission=None,
        unit_key=None,
    )

    assert acl is not None
    assert acl.visibility == "private"
    assert acl.permission == "write"
    assert acl.grants == (("user", str(env.actor)),)


def test_personal_read_choice_is_offered_and_kept_read_only(permission_context):
    env = permission_context
    permissions = KnowledgePermissionService(env.services)
    response = permissions.upload_access(env.base.id, actor_user_id=env.actor)
    assert {
        option["permission"] for option in response["options"] if option["scope"] == "personal"
    } == {
        "read",
        "write",
    }

    acl = permissions.validate_upload_access(
        env.base.id,
        folder="",
        data_source_id=None,
        actor_user_id=env.actor,
        scope="personal",
        permission="read",
        unit_key=None,
    )
    assert acl is not None
    assert acl.visibility == "private"
    assert acl.permission == "read"


def _boundary(env, resource_type, resource_id, *, visibility="private", unit_key=None, grants=()):
    env.services.resource_acl_repo.upsert(
        AclEntry(
            resource_type,
            resource_id,
            env.actor,
            visibility,
            unit_key,
            1,
            permission="write",
            grants=grants,
        )
    )


def test_other_individual_recipients_do_not_make_a_personal_grant_reshareable(permission_context):
    env = permission_context
    alice = env.services.user_repo.create(username="alice", password_hash="unused", role="user")
    bob = env.services.user_repo.create(username="bob", password_hash="unused", role="user")
    _boundary(env, "knowledge_base", env.base.id, visibility="public")
    folder = env.services.knowledge_repo.ensure_folder(env.base.id, "individual")
    _boundary(
        env,
        "knowledge_document",
        folder.id,
        grants=(("user", str(alice)), ("user", str(bob))),
    )
    choices = KnowledgePermissionService(env.services).upload_access(
        env.base.id, folder="individual", actor_user_id=alice
    )
    assert {option["scope"] for option in choices["options"]} == {"personal"}
    assert choices["default"] == {
        "scope": "personal",
        "permission": "write",
        "unit_key": None,
        "department_name": None,
    }


def test_nested_departments_default_to_the_intersection_not_the_parent(permission_context):
    env = permission_context
    env.services.org_unit_repo.create(key="division", label_zh="事业部", label_en="Division")
    env.services.org_unit_repo.create(
        key="team", label_zh="小组", label_en="Team", parent_key="division"
    )
    alice = env.services.user_repo.create(
        username="alice", password_hash="unused", role="user", org_unit="team"
    )
    _boundary(env, "knowledge_base", env.base.id, visibility="public")
    for path, key in (("division", "division"), ("division/team", "team")):
        folder = env.services.knowledge_repo.ensure_folder(env.base.id, path)
        _boundary(env, "knowledge_document", folder.id, visibility="unit", unit_key=key)
    permissions = KnowledgePermissionService(env.services)
    choices = permissions.upload_access(env.base.id, folder="division/team", actor_user_id=alice)
    assert choices["default"]["scope"] == "department"
    assert choices["default"]["unit_key"] == "team"
    with pytest.raises(OctopError):
        permissions.validate_upload_access(
            env.base.id,
            folder="division/team",
            data_source_id=None,
            actor_user_id=alice,
            scope="department",
            permission="write",
            unit_key="division",
        )


def test_custom_role_default_stays_private_after_its_parent_becomes_public(permission_context):
    env = permission_context
    alice = env.services.user_repo.create(username="alice", password_hash="unused", role="user")
    outsider = env.services.user_repo.create(
        username="outsider", password_hash="unused", role="enterprise_admin"
    )
    _boundary(env, "knowledge_base", env.base.id, visibility="public")
    folder = env.services.knowledge_repo.ensure_folder(env.base.id, "role")
    _boundary(env, "knowledge_document", folder.id, grants=(("role", "user"),))
    permissions = KnowledgePermissionService(env.services)
    choices = permissions.upload_access(env.base.id, folder="role", actor_user_id=alice)
    assert choices["default"]["scope"] == "authorized"
    acl = permissions.validate_upload_access(
        env.base.id,
        folder="role",
        data_source_id=None,
        actor_user_id=alice,
        scope=None,
        permission=None,
        unit_key=None,
    )
    document = env.services.knowledge_repo.create_document(
        kb_id=env.base.id,
        filename="snapshot.txt",
        path="role/snapshot.txt",
        content_type="text/plain",
        byte_size=1,
        initial_acl=acl,
    )
    _boundary(env, "knowledge_document", folder.id, visibility="public")
    restricted, readable, _ = document_access_ids(
        env.services.knowledge_repo.list_documents(env.base.id),
        env.services.knowledge_repo.document_acl_entries(),
        user_id=outsider,
        role="enterprise_admin",
        unit_keys=(),
    )
    assert document.id in restricted and document.id not in readable
