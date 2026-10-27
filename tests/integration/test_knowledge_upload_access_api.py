"""Upload ceilings and source-linked delegation are enforced at the HTTP boundary."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from octop.infra.knowledge.files import document_path
from octop.infra.knowledge.jobs import enqueue_index_document
from octop.infra.sharing import AclEntry
from tests.support.auth import auth_header, create_user, resolve_user_id


def _set_acl(
    services: Any,
    resource_type: str,
    resource_id: str,
    owner_id: int,
    *,
    visibility: str,
    unit_key: str | None = None,
    grants: tuple[tuple[str, str], ...] = (),
) -> None:
    services.repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type=resource_type,
            resource_id=resource_id,
            owner_user_id=owner_id,
            visibility=visibility,
            unit_key=unit_key,
            version=1,
            permission="write",
            grants=grants,
        )
    )


async def _private_document(
    client: Any, services: Any, admin_auth: dict[str, str], admin_id: int
) -> tuple[str, str]:
    base = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Grant boundaries")
    _set_acl(services, "knowledge_base", base.id, admin_id, visibility="public")
    response = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers=admin_auth,
        data={"access_scope": "personal", "access_permission": "write"},
        files={"upload": ("private.txt", b"private authorization evidence", "text/plain")},
    )
    assert response.status_code == 201, response.text
    await enqueue_index_document(services, base.id, response.json()["id"])
    return base.id, response.json()["id"]


async def test_conditional_original_download_rejects_stale_hash_and_serves_current_bytes(
    env: Any,
) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    base = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Versioned source")
    uploaded = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers=admin,
        files={"upload": ("version.txt", b"indexed bytes", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    document_id = uploaded.json()["id"]
    await enqueue_index_document(services, base.id, document_id)
    stored = services.knowledge_repo.get_document(document_id)
    assert stored is not None
    old_version = stored.content_hash
    file_path = document_path(base.id, document_id, stored.filename)
    file_path.write_bytes(b"changed before next scan")
    current_bytes = file_path.read_bytes()
    current_version = hashlib.sha256(current_bytes).hexdigest()

    stale = await client.get(
        f"/api/knowledge-bases/{base.id}/documents/{document_id}/file",
        params={"version": old_version},
        headers=admin,
    )
    assert stale.status_code == 409, stale.text
    assert stale.json()["error"]["code"] == "KNOWLEDGE_SOURCE_VERSION_CHANGED"

    current = await client.get(
        f"/api/knowledge-bases/{base.id}/documents/{document_id}/file",
        params={"version": current_version},
        headers=admin,
    )
    assert current.status_code == 200, current.text
    assert current.content == current_bytes


async def _replace_user_grants(
    client: Any,
    admin_auth: dict[str, str],
    kb_id: str,
    doc_id: str,
    grants: list[dict[str, Any]],
) -> None:
    url = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/permissions"
    before = await client.get(url, headers=admin_auth)
    assert before.status_code == 200, before.text
    changed = await client.put(
        url,
        headers=admin_auth,
        json={
            "expected_version": before.json()["version"],
            "visibility": "private",
            "permission": "write",
            "grants": grants,
        },
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["change"]["status"] == "applied"


async def _assert_file_access(
    client: Any, root: str, auth: dict[str, str], *, allowed: bool
) -> None:
    response = await client.get(f"{root}/file", headers=auth)
    if allowed:
        assert response.status_code == 200, response.text
        assert response.content == b"private authorization evidence"
        preview = await client.get(f"{root}/preview", headers=auth)
        assert preview.status_code == 200, preview.text
        assert b"private authorization evidence" in preview.content
    else:
        assert response.status_code in (403, 404), response.text
        preview = await client.get(f"{root}/preview", headers=auth)
        assert preview.status_code in (403, 404), preview.text
    kb_root, doc_id = root.rsplit("/documents/", 1)
    search = await client.get(
        f"{kb_root}/search", headers=auth, params={"q": "private authorization evidence"}
    )
    if allowed:
        assert search.status_code == 200, search.text
        assert any(hit["document_id"] == doc_id for hit in search.json())
    elif search.status_code == 200:
        assert all(hit["document_id"] != doc_id for hit in search.json())
    else:
        assert search.status_code == 403, search.text


@pytest.mark.parametrize("destination", ["platform", "source"])
async def test_personal_upload_ceiling_rejects_widening_and_persists_private_access(
    env: Any, tmp_path: Path, destination: str
) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    alice = await create_user(
        client, admin, username="upload-personal-alice", permissions=["knowledge_bases"]
    )
    bob = await create_user(
        client, admin, username="upload-personal-bob", permissions=["knowledge_bases"]
    )
    alice_id = await resolve_user_id(client, admin, "upload-personal-alice")
    kb = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Personal upload ceiling")
    _set_acl(services, "knowledge_base", kb.id, admin_id, visibility="public")
    base_url = f"/api/knowledge-bases/{kb.id}"
    query: dict[str, str] = {"folder": "reports"}
    upload_url = f"{base_url}/documents"
    logical_folder = "reports"
    physical_root = tmp_path / "source"
    if destination == "source":
        (physical_root / "reports").mkdir(parents=True)
        (physical_root / "reports" / "seed.txt").write_text("seed", encoding="utf-8")
        source = await client.post(
            f"{base_url}/data-sources",
            headers=admin,
            json={
                "name": "Writable source",
                "kind": "local",
                "folder": {"root_path": str(physical_root), "read_only": False},
            },
        )
        assert source.status_code == 201, source.text
        source_id = source.json()["id"]
        sync = await client.post(f"/api/data-sources/{source_id}/sync", headers=admin)
        assert sync.status_code == 200, sync.text
        query["data_source_id"] = source_id
        logical_folder = f"{source_id}/reports"
        upload_url = f"{base_url}/data-sources/{source_id}/documents"
    folder = services.knowledge_repo.ensure_folder(kb.id, logical_folder)
    _set_acl(
        services,
        "knowledge_document",
        folder.id,
        admin_id,
        visibility="private",
        grants=(("user", str(alice_id)),),
    )
    choices = await client.get(f"{base_url}/upload-permissions", headers=alice, params=query)
    assert choices.status_code == 200, choices.text
    assert choices.json()["default"]["scope"] == "personal"
    assert choices.json()["default"]["permission"] == "write"
    assert {option["scope"] for option in choices.json()["options"]} == {"personal"}
    before_count = services.knowledge_repo.count_documents(kb.id)
    denied = await client.post(
        upload_url,
        headers=alice,
        data={
            "path": "reports/widened.txt",
            "access_scope": "company",
            "access_permission": "write",
        },
        files={"upload": ("widened.txt", b"must never become public", "text/plain")},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "KNOWLEDGE_UPLOAD_PERMISSION_FORBIDDEN"
    assert services.knowledge_repo.count_documents(kb.id) == before_count
    assert not (physical_root / "reports" / "widened.txt").exists()
    uploaded = await client.post(
        upload_url,
        headers=alice,
        data={"path": "reports/personal.txt"},
        files={"upload": ("personal.txt", b"personal source evidence", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["id"]
    if destination == "source":
        assert (
            physical_root / "reports" / "personal.txt"
        ).read_bytes() == b"personal source evidence"
        assert not document_path(kb.id, doc_id, "personal.txt").exists()
    # The selected default is a file restriction, not merely today's parent ACL.
    _set_acl(services, "knowledge_document", folder.id, admin_id, visibility="public")
    own = await client.get(f"{base_url}/documents/{doc_id}/file", headers=alice)
    assert own.status_code == 200, own.text
    assert own.content == b"personal source evidence"
    hidden = await client.get(f"{base_url}/documents/{doc_id}/file", headers=bob)
    assert hidden.status_code in (403, 404), hidden.text
    wider_choices = await client.get(f"{base_url}/upload-permissions", headers=alice, params=query)
    assert wider_choices.status_code == 200, wider_choices.text
    old_default = wider_choices.json()["default"]
    assert old_default["scope"] == "company"
    _set_acl(
        services,
        "knowledge_document",
        folder.id,
        admin_id,
        visibility="private",
        grants=(("user", str(alice_id)),),
    )
    count_before_stale_choice = services.knowledge_repo.count_documents(kb.id)
    stale_choice = await client.post(
        upload_url,
        headers=alice,
        data={
            "path": "reports/stale-choice.txt",
            "access_scope": old_default["scope"],
            "access_permission": old_default["permission"],
        },
        files={"upload": ("stale-choice.txt", b"stale choice must fail", "text/plain")},
    )
    assert stale_choice.status_code == 403, stale_choice.text
    assert stale_choice.json()["error"]["code"] == "KNOWLEDGE_UPLOAD_PERMISSION_FORBIDDEN"
    assert services.knowledge_repo.count_documents(kb.id) == count_before_stale_choice
    assert not (physical_root / "reports" / "stale-choice.txt").exists()


async def test_department_upload_default_and_forged_foreign_department(env: Any) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    for key in ("upload-a", "upload-b"):
        services.org_unit_repo.create(key=key, label_zh=key, label_en=key)
    alice = await create_user(
        client,
        admin,
        username="upload-unit-alice",
        org_unit="upload-a",
        permissions=["knowledge_bases"],
    )
    same_unit = await create_user(
        client,
        admin,
        username="upload-unit-peer",
        org_unit="upload-a",
        permissions=["knowledge_bases"],
    )
    other_unit = await create_user(
        client,
        admin,
        username="upload-unit-outsider",
        org_unit="upload-b",
        permissions=["knowledge_bases"],
    )
    kb = services.knowledge_repo.create_base(
        owner_user_id=admin_id, name="Department upload ceiling"
    )
    _set_acl(services, "knowledge_base", kb.id, admin_id, visibility="public")
    folder = services.knowledge_repo.ensure_folder(kb.id, "department")
    _set_acl(
        services, "knowledge_document", folder.id, admin_id, visibility="unit", unit_key="upload-a"
    )
    root = f"/api/knowledge-bases/{kb.id}"
    choices = await client.get(
        f"{root}/upload-permissions", headers=alice, params={"folder": "department"}
    )
    assert choices.status_code == 200, choices.text
    default = choices.json()["default"]
    assert default["scope"] == "department"
    assert default["unit_key"] == "upload-a"
    assert default["permission"] == "write"
    assert all(
        option["scope"] != "company" and option["unit_key"] != "upload-b"
        for option in choices.json()["options"]
    )
    forged = await client.post(
        f"{root}/documents",
        headers=alice,
        data={
            "path": "department/foreign.txt",
            "access_scope": "department",
            "access_permission": "write",
            "access_unit_key": "upload-b",
        },
        files={"upload": ("foreign.txt", b"forbidden", "text/plain")},
    )
    assert forged.status_code == 403, forged.text
    uploaded = await client.post(
        f"{root}/documents",
        headers=alice,
        data={"path": "department/default.txt"},
        files={"upload": ("default.txt", b"department evidence", "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    doc_id = uploaded.json()["id"]
    _set_acl(services, "knowledge_document", folder.id, admin_id, visibility="public")
    peer_read = await client.get(f"{root}/documents/{doc_id}/file", headers=same_unit)
    assert peer_read.status_code == 200, peer_read.text
    assert peer_read.content == b"department evidence"
    outside_read = await client.get(f"{root}/documents/{doc_id}/file", headers=other_unit)
    assert outside_read.status_code in (403, 404), outside_read.text


async def test_personal_authorization_cannot_delegate_until_explicitly_enabled(env: Any) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    alice = await create_user(
        client, admin, username="delegate-off-alice", permissions=["knowledge_bases"]
    )
    bob = await create_user(
        client, admin, username="delegate-off-bob", permissions=["knowledge_bases"]
    )
    alice_id = await resolve_user_id(client, admin, "delegate-off-alice")
    bob_id = await resolve_user_id(client, admin, "delegate-off-bob")
    kb_id, doc_id = await _private_document(client, services, admin, admin_id)
    root = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}"
    await _replace_user_grants(
        client, admin, kb_id, doc_id, [{"grantee_type": "user", "grantee_id": str(alice_id)}]
    )
    await _assert_file_access(client, root, alice, allowed=True)
    denied = await client.post(
        f"{root}/delegations", headers=alice, json={"grantee_user_id": bob_id, "can_delegate": True}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "KNOWLEDGE_DELEGATION_FORBIDDEN"
    # Being able to edit the content does not let a recipient enable its own flag.
    before = await client.get(f"{root}/permissions", headers=alice)
    assert before.status_code == 200, before.text
    assert before.json()["can_delegate"] is False
    self_enable = await client.put(
        f"{root}/permissions",
        headers=alice,
        json={
            "expected_version": before.json()["version"],
            "visibility": "private",
            "permission": "write",
            "grants": [{"grantee_type": "user", "grantee_id": str(alice_id), "can_delegate": True}],
        },
    )
    assert self_enable.status_code == 403, self_enable.text
    await _assert_file_access(client, root, bob, allowed=False)
    await _replace_user_grants(
        client,
        admin,
        kb_id,
        doc_id,
        [{"grantee_type": "user", "grantee_id": str(alice_id), "can_delegate": True}],
    )
    enabled = await client.post(
        f"{root}/delegations",
        headers=alice,
        json={"grantee_user_id": bob_id, "can_delegate": False},
    )
    assert enabled.status_code == 201, enabled.text
    await _assert_file_access(client, root, bob, allowed=True)


async def test_multilevel_revocation_preserves_independent_access_and_notifies_every_recipient(
    env: Any,
) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    auths = {}
    ids = {}
    for name in ("chain-alice", "chain-bob", "chain-carol", "chain-outsider"):
        auths[name] = await create_user(
            client, admin, username=name, permissions=["knowledge_bases"]
        )
        ids[name] = await resolve_user_id(client, admin, name)
    kb_id, doc_id = await _private_document(client, services, admin, admin_id)
    root = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}"
    roots = [
        {"grantee_type": "user", "grantee_id": str(ids["chain-alice"]), "can_delegate": True},
        {"grantee_type": "user", "grantee_id": str(ids["chain-bob"]), "can_delegate": False},
    ]
    await _replace_user_grants(client, admin, kb_id, doc_id, roots)
    to_bob = await client.post(
        f"{root}/delegations",
        headers=auths["chain-alice"],
        json={"grantee_user_id": ids["chain-bob"], "can_delegate": True},
    )
    assert to_bob.status_code == 201, to_bob.text
    to_carol = await client.post(
        f"{root}/delegations",
        headers=auths["chain-bob"],
        json={"grantee_user_id": ids["chain-carol"], "can_delegate": False},
    )
    assert to_carol.status_code == 201, to_carol.text
    await _assert_file_access(client, root, auths["chain-carol"], allowed=True)
    carol_meta = await client.get(f"{root}/permissions", headers=auths["chain-carol"])
    assert carol_meta.status_code == 200, carol_meta.text
    assert carol_meta.json()["effective"]["read"] is True
    assert carol_meta.json()["can_manage"] is False
    assert carol_meta.json()["can_delegate"] is False
    cycle = await client.post(
        f"{root}/delegations",
        headers=auths["chain-bob"],
        json={"grantee_user_id": ids["chain-alice"], "can_delegate": True},
    )
    assert cycle.status_code == 403, cycle.text
    no_next_hop = await client.post(
        f"{root}/delegations",
        headers=auths["chain-carol"],
        json={"grantee_user_id": ids["chain-outsider"], "can_delegate": False},
    )
    assert no_next_hop.status_code == 403, no_next_hop.text
    owner_view = await client.get(f"{root}/permissions", headers=admin)
    assert owner_view.status_code == 200, owner_view.text
    assert {grant["grantee_id"] for grant in owner_view.json()["entry"]["grants"]} == {
        str(ids["chain-alice"]),
        str(ids["chain-bob"]),
    }
    roots[0]["can_delegate"] = False
    await _replace_user_grants(client, admin, kb_id, doc_id, roots)
    await _assert_file_access(client, root, auths["chain-alice"], allowed=True)
    await _assert_file_access(client, root, auths["chain-bob"], allowed=True)
    await _assert_file_access(client, root, auths["chain-carol"], allowed=False)
    bob_meta = await client.get(f"{root}/permissions", headers=auths["chain-bob"])
    assert bob_meta.status_code == 200, bob_meta.text
    assert bob_meta.json()["can_delegate"] is False
    for name, retained in (("chain-bob", True), ("chain-carol", False)):
        notices = await client.get("/api/knowledge-access-notifications", headers=auths[name])
        assert notices.status_code == 200, notices.text
        mine = [item for item in notices.json()["items"] if item["resource_id"] == doc_id]
        assert len(mine) == 1
        assert mine[0]["access_retained"] is retained
        assert mine[0]["read_at"] is None
    carol_notices = (
        await client.get("/api/knowledge-access-notifications", headers=auths["chain-carol"])
    ).json()
    notice_id = carol_notices["items"][0]["id"]
    foreign_mark = await client.post(
        f"/api/knowledge-access-notifications/{notice_id}/read", headers=auths["chain-outsider"]
    )
    assert foreign_mark.status_code == 404, foreign_mark.text
    # An affected user still receives the durable inbox after its KB module is denied.
    limited = await client.patch(
        f"/api/users/{ids['chain-carol']}",
        headers=admin,
        json={"denied_permissions": ["knowledge_bases", "knowledge_settings"]},
    )
    assert limited.status_code == 200, limited.text
    carol_login = await auth_header(client, username="chain-carol")
    after_login = await client.get("/api/knowledge-access-notifications", headers=carol_login)
    assert after_login.status_code == 200, after_login.text
    assert notice_id in {item["id"] for item in after_login.json()["items"]}
    for _ in range(2):
        read = await client.post(
            f"/api/knowledge-access-notifications/{notice_id}/read", headers=carol_login
        )
        assert read.status_code == 204, read.text
    unread = await client.get(
        "/api/knowledge-access-notifications", headers=carol_login, params={"unread_only": True}
    )
    assert unread.status_code == 200, unread.text
    assert notice_id not in {item["id"] for item in unread.json()["items"]}
    roots[0]["can_delegate"] = True
    await _replace_user_grants(client, admin, kb_id, doc_id, roots)
    # Restoring a switch does not resurrect previously deleted descendants.
    await _assert_file_access(client, root, auths["chain-carol"], allowed=False)
    bob_delegation = await client.post(
        f"{root}/delegations",
        headers=auths["chain-bob"],
        json={"grantee_user_id": ids["chain-carol"], "can_delegate": False},
    )
    assert bob_delegation.status_code == 403, bob_delegation.text


async def test_delegated_authorization_is_scoped_to_one_document(env: Any) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    alice = await create_user(
        client, admin, username="isolated-alice", permissions=["knowledge_bases"]
    )
    bob = await create_user(client, admin, username="isolated-bob", permissions=["knowledge_bases"])
    alice_id = await resolve_user_id(client, admin, "isolated-alice")
    bob_id = await resolve_user_id(client, admin, "isolated-bob")
    kb_id, doc_id = await _private_document(client, services, admin, admin_id)
    second = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=admin,
        data={"access_scope": "personal", "access_permission": "write"},
        files={"upload": ("sibling.txt", b"sibling secret", "text/plain")},
    )
    assert second.status_code == 201, second.text
    sibling_id = second.json()["id"]
    await _replace_user_grants(
        client,
        admin,
        kb_id,
        doc_id,
        [{"grantee_type": "user", "grantee_id": str(alice_id), "can_delegate": True}],
    )
    root = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}"
    shared = await client.post(
        f"{root}/delegations", headers=alice, json={"grantee_user_id": bob_id, "can_delegate": True}
    )
    assert shared.status_code == 201, shared.text
    await _assert_file_access(client, root, bob, allowed=True)
    sibling_root = f"/api/knowledge-bases/{kb_id}/documents/{sibling_id}"
    denied = await client.get(f"{sibling_root}/file", headers=bob)
    assert denied.status_code in (403, 404), denied.text
    forged = await client.post(
        f"{sibling_root}/delegations",
        headers=alice,
        json={"grantee_user_id": bob_id, "can_delegate": True},
    )
    assert forged.status_code in (403, 404), forged.text
    issued = next(item for item in shared.json()["grants"] if item["grantee_user_id"] == bob_id)
    wrong_resource = await client.delete(
        f"{sibling_root}/delegations/{issued['id']}", headers=admin
    )
    assert wrong_resource.status_code == 404, wrong_resource.text
    own_resource = await client.delete(f"{root}/delegations/{issued['id']}", headers=alice)
    assert own_resource.status_code == 204, own_resource.text
    await _assert_file_access(client, root, bob, allowed=False)


async def test_removing_non_delegable_personal_grant_notifies_only_its_recipient(env: Any) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    alice = await create_user(
        client, admin, username="direct-revoke-alice", permissions=["knowledge_bases"]
    )
    observer = await create_user(
        client, admin, username="direct-revoke-observer", permissions=["knowledge_bases"]
    )
    alice_id = await resolve_user_id(client, admin, "direct-revoke-alice")
    kb_id, doc_id = await _private_document(client, services, admin, admin_id)
    root = f"/api/knowledge-bases/{kb_id}/documents/{doc_id}"
    await _replace_user_grants(
        client,
        admin,
        kb_id,
        doc_id,
        [{"grantee_type": "user", "grantee_id": str(alice_id), "can_delegate": False}],
    )
    await _assert_file_access(client, root, alice, allowed=True)
    await _replace_user_grants(client, admin, kb_id, doc_id, [])
    await _assert_file_access(client, root, alice, allowed=False)
    notices = await client.get("/api/knowledge-access-notifications", headers=alice)
    assert notices.status_code == 200, notices.text
    mine = [item for item in notices.json()["items"] if item["resource_id"] == doc_id]
    assert len(mine) == 1
    assert mine[0]["event_kind"] == "authorization_revoked"
    assert mine[0]["access_retained"] is False
    assert mine[0]["read_at"] is None
    unrelated = await client.get("/api/knowledge-access-notifications", headers=observer)
    assert unrelated.status_code == 200, unrelated.text
    assert all(item["resource_id"] != doc_id for item in unrelated.json()["items"])
    # Re-saving an already-revoked ACL must not send duplicate revocations.
    await _replace_user_grants(client, admin, kb_id, doc_id, [])
    again = await client.get("/api/knowledge-access-notifications", headers=alice)
    assert again.status_code == 200, again.text
    assert [item["id"] for item in again.json()["items"] if item["resource_id"] == doc_id] == [
        mine[0]["id"]
    ]


@pytest.mark.parametrize("destination", ["platform", "source"])
async def test_explicit_read_upload_does_not_grant_write(
    env: Any, tmp_path: Path, destination: str
) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    writer = await create_user(
        client, admin, username="read-level-uploader", permissions=["knowledge_bases"]
    )
    kb = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Selected read level")
    _set_acl(services, "knowledge_base", kb.id, admin_id, visibility="public")
    root = f"/api/knowledge-bases/{kb.id}"
    upload_url = f"{root}/documents"
    params = {}
    if destination == "source":
        physical_root = tmp_path / "read-only-documents"
        physical_root.mkdir()
        source = await client.post(
            f"{root}/data-sources",
            headers=admin,
            json={
                "name": "Opt-in writable source",
                "kind": "local",
                "folder": {"root_path": str(physical_root), "read_only": False},
            },
        )
        assert source.status_code == 201, source.text
        source_id = source.json()["id"]
        params["data_source_id"] = source_id
        upload_url = f"{root}/data-sources/{source_id}/documents"
    choices = await client.get(f"{root}/upload-permissions", headers=writer, params=params)
    assert choices.status_code == 200, choices.text
    assert choices.json()["default"]["scope"] == "company"
    assert choices.json()["default"]["permission"] == "write"
    assert any(
        option["scope"] == "company" and option["permission"] == "read"
        for option in choices.json()["options"]
    )
    response = await client.post(
        upload_url,
        headers=writer,
        data={"access_scope": "company", "access_permission": "read"},
        files={"upload": ("read-only.txt", b"read-only selected evidence", "text/plain")},
    )
    assert response.status_code == 201, response.text
    doc_id = response.json()["id"]
    refused_write = await client.delete(f"{root}/documents/{doc_id}", headers=writer)
    assert refused_write.status_code == 403, refused_write.text
    original = await client.get(f"{root}/documents/{doc_id}/file", headers=writer)
    assert original.status_code == 200, original.text
    assert original.content == b"read-only selected evidence"


async def test_department_move_removes_base_visibility_despite_an_existing_delegation(
    env: Any,
) -> None:
    client, server, admin = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    for key in ("delegation-origin", "delegation-destination"):
        services.org_unit_repo.create(key=key, label_zh=key, label_en=key)
    alice = await create_user(
        client,
        admin,
        username="moving-alice",
        org_unit="delegation-origin",
        permissions=["knowledge_bases"],
    )
    bob = await create_user(
        client,
        admin,
        username="moving-bob",
        org_unit="delegation-origin",
        permissions=["knowledge_bases"],
    )
    alice_id = await resolve_user_id(client, admin, "moving-alice")
    bob_id = await resolve_user_id(client, admin, "moving-bob")
    kb_id, doc_id = await _private_document(client, services, admin, admin_id)
    _set_acl(
        services,
        "knowledge_base",
        kb_id,
        admin_id,
        visibility="unit",
        unit_key="delegation-origin",
    )
    await _replace_user_grants(
        client,
        admin,
        kb_id,
        doc_id,
        [{"grantee_type": "user", "grantee_id": str(alice_id), "can_delegate": True}],
    )
    delegated = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/delegations",
        headers=alice,
        json={"grantee_user_id": bob_id},
    )
    assert delegated.status_code == 201, delegated.text
    before = await client.get("/api/knowledge-bases", headers=bob)
    assert before.status_code == 200, before.text
    assert kb_id in {item["id"] for item in before.json()}
    moved = await client.patch(
        f"/api/users/{bob_id}",
        headers=admin,
        json={"org_unit": "delegation-destination"},
    )
    assert moved.status_code == 200, moved.text
    after = await client.get("/api/knowledge-bases", headers=bob)
    assert after.status_code == 200, after.text
    assert kb_id not in {item["id"] for item in after.json()}
    await _assert_file_access(
        client, f"/api/knowledge-bases/{kb_id}/documents/{doc_id}", bob, allowed=False
    )
