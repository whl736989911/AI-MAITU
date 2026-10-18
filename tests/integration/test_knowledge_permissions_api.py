"""Knowledge permission metadata is filtered and ACL changes use native sharing rules."""

from __future__ import annotations

from typing import Any

from octop.infra.knowledge.service import KnowledgeService
from octop.infra.sharing import AclEntry
from octop.infra.sharing.service import SharingService
from tests.support.auth import create_user, resolve_user_id


async def test_permission_listing_filters_denied_files_and_content_writer_cannot_manage_acl(
    env: Any,
) -> None:
    client, server, admin_auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin_auth, "admin")
    viewer_auth = await create_user(client, admin_auth, username="knowledge-acl-viewer")
    viewer_id = await resolve_user_id(client, admin_auth, "knowledge-acl-viewer")

    repo = services.knowledge_repo
    kb = repo.create_base(owner_user_id=admin_id, name="Permission metadata")
    knowledge = KnowledgeService(services)
    folder = repo.ensure_folder(kb.id, "restricted")
    visible = knowledge.upload_document(
        kb.id,
        actor_user_id=admin_id,
        filename="visible.md",
        path="restricted/visible.md",
        content_type="text/markdown",
        content=b"visible source",
    )
    hidden = knowledge.upload_document(
        kb.id,
        actor_user_id=admin_id,
        filename="hidden.md",
        path="restricted/hidden.md",
        content_type="text/markdown",
        content=b"hidden source",
    )
    sharing = SharingService(services.db)
    sharing.apply_change(
        admin_id,
        "knowledge_base",
        kb.id,
        AclEntry(
            resource_type="knowledge_base",
            resource_id=kb.id,
            owner_user_id=admin_id,
            visibility="private",
            unit_key=None,
            version=1,
            permission="write",
            grants=(("user", str(viewer_id)),),
        ),
    )
    # The file grant bypasses this folder ACL for one file only.
    sharing.apply_change(
        admin_id,
        "knowledge_document",
        folder.id,
        AclEntry(
            resource_type="knowledge_document",
            resource_id=folder.id,
            owner_user_id=admin_id,
            visibility="private",
            unit_key=None,
            version=1,
            permission="read",
        ),
    )
    sharing.apply_change(
        admin_id,
        "knowledge_document",
        visible.id,
        AclEntry(
            resource_type="knowledge_document",
            resource_id=visible.id,
            owner_user_id=admin_id,
            visibility="private",
            unit_key=None,
            version=1,
            permission="write",
            grants=(("user", str(viewer_id)),),
        ),
    )

    root = f"/api/knowledge-bases/{kb.id}"
    result = await client.get(f"{root}/permissions", headers=viewer_auth)
    assert result.status_code == 200, result.text
    payload = result.json()
    assert [item["resource_id"] for item in payload["documents"]] == [visible.id]
    assert hidden.filename not in str(payload)
    assert payload["documents"][0]["effective"] == {"read": True, "write": True}
    assert payload["documents"][0]["can_manage"] is False
    assert payload["documents"][0]["entry"] is None
    assert payload["documents"][0]["inherited_from"]["resource_id"] == kb.id
    principals_denied = await client.get(f"{root}/permission-principals", headers=viewer_auth)
    assert principals_denied.status_code == 403, principals_denied.text
    detail = await client.get(f"{root}/documents/{visible.id}/permissions", headers=viewer_auth)
    assert detail.status_code == 200, detail.text
    assert detail.json() == payload["documents"][0]
    hidden_detail = await client.get(
        f"{root}/documents/{hidden.id}/permissions", headers=viewer_auth
    )
    assert hidden_detail.status_code == 404, hidden_detail.text

    denied = await client.put(
        f"{root}/documents/{visible.id}/permissions",
        headers=viewer_auth,
        json={
            "visibility": "public",
            "permission": "write",
            "expected_version": 1,
        },
    )
    assert denied.status_code == 403, denied.text


async def test_permission_put_rejects_stale_version_and_reports_pending_approval(env: Any) -> None:
    client, server, admin_auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin_auth, "admin")
    kb = services.knowledge_repo.create_base(owner_user_id=admin_id, name="ACL version")
    root = f"/api/knowledge-bases/{kb.id}/permissions"

    before = await client.get(root, headers=admin_auth)
    assert before.status_code == 200, before.text
    initial_version = before.json()["knowledge_base"]["version"]
    requested = await client.put(
        root,
        headers=admin_auth,
        json={"visibility": "public", "permission": "read", "expected_version": initial_version},
    )
    assert requested.status_code == 200, requested.text
    assert requested.json()["change"]["status"] == "pending_approval"
    assert requested.json()["permission"]["pending"]["status"] == "pending_approval"

    stale = await client.put(
        root,
        headers=admin_auth,
        json={"visibility": "private", "permission": "read", "expected_version": initial_version},
    )
    assert stale.status_code == 409, stale.text


async def test_resource_owner_can_manage_acl_without_users_module(env: Any) -> None:
    client, server, admin_auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    owner_auth = await create_user(
        client,
        admin_auth,
        username="knowledge-owner-no-users-module",
        permissions=["knowledge_bases"],
    )
    owner_id = await resolve_user_id(client, admin_auth, "knowledge-owner-no-users-module")
    await create_user(client, admin_auth, username="knowledge-owner-grantee")
    viewer_id = await resolve_user_id(client, admin_auth, "knowledge-owner-grantee")
    kb = services.knowledge_repo.create_base(owner_user_id=owner_id, name="Owner-managed ACL")
    root = f"/api/knowledge-bases/{kb.id}/permissions"
    directory = await client.get(
        f"/api/knowledge-bases/{kb.id}/permission-principals", headers=owner_auth
    )
    assert directory.status_code == 200, directory.text
    directory_payload = directory.json()
    recipient = next(item for item in directory_payload["users"] if item["id"] == viewer_id)
    assert recipient["label"] and recipient["username"]
    assert set(recipient) == {"id", "username", "label"}
    assert {item["id"] for item in directory_payload["roles"]} == {
        "admin",
        "enterprise_admin",
        "unit_admin",
        "user",
    }

    current = await client.get(root, headers=owner_auth)
    assert current.status_code == 200, current.text
    changed = await client.put(
        root,
        headers=owner_auth,
        json={
            "visibility": "private",
            "permission": "read",
            "grants": [{"grantee_type": "user", "grantee_id": str(viewer_id)}],
            "expected_version": current.json()["knowledge_base"]["version"],
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["change"]["status"] == "applied"


async def test_permission_labels_follow_effective_restrictions_below_company_base(env: Any) -> None:
    client, server, admin_auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    owner_id = await resolve_user_id(client, admin_auth, "admin")
    services.org_unit_repo.create(
        key="knowledge-label-team", label_zh="资料部门", label_en="Knowledge team"
    )
    repo = services.knowledge_repo
    kb = repo.create_base(owner_user_id=owner_id, name="Effective permission labels")
    folder = repo.ensure_folder(kb.id, "department")
    knowledge = KnowledgeService(services)
    files = {
        path: knowledge.upload_document(
            kb.id,
            actor_user_id=owner_id,
            filename=path.rsplit("/", 1)[-1],
            path=path,
            content_type="text/markdown",
            content=b"source",
        )
        for path in ("company.md", "department/inherited.md", "department/personal.md")
    }
    for resource_type, resource_id, visibility, unit_key in (
        ("knowledge_base", kb.id, "public", None),
        ("knowledge_document", folder.id, "unit", "knowledge-label-team"),
        ("knowledge_document", files["department/inherited.md"].id, "public", None),
        ("knowledge_document", files["department/personal.md"].id, "private", None),
    ):
        services.repos.resource_acl_repo.upsert(
            AclEntry(
                resource_type=resource_type,
                resource_id=resource_id,
                owner_user_id=owner_id,
                visibility=visibility,
                unit_key=unit_key,
                version=1,
                permission="read",
            )
        )
    response = await client.get(
        f"/api/knowledge-bases/{kb.id}/permissions",
        headers={**admin_auth, "Accept-Language": "zh"},
    )
    assert response.status_code == 200, response.text
    documents = {item["path"]: item for item in response.json()["documents"]}
    assert documents["company.md"]["access_scope"] == "company"
    assert documents["department"]["access_scope"] == "department"
    assert documents["department"]["department_name"] == "资料部门"
    assert documents["department/inherited.md"]["access_scope"] == "department"
    assert documents["department/personal.md"]["access_scope"] == "personal"
