"""HTTP ACL for knowledge-base instance settings vs page CRUD."""

from __future__ import annotations

from typing import Any

from tests.support.auth import create_user


async def test_feature_toggle_requires_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_page_user")
    denied = await client.put(
        "/api/knowledge-bases/feature",
        headers=page_auth,
        json={"enabled": False},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_settings_user",
        permissions=["knowledge_bases", "knowledge_settings"],
    )
    allowed = await client.put(
        "/api/knowledge-bases/feature",
        headers=settings_auth,
        json={"enabled": False},
    )
    assert allowed.status_code == 200, allowed.text
    assert allowed.json()["feature_enabled"] is False


async def test_embedding_options_require_knowledge_settings(env: Any) -> None:
    client, _server, admin_auth = env
    page_auth = await create_user(client, admin_auth, username="kb_options_user")
    denied = await client.get("/api/knowledge-bases/embedding-options", headers=page_auth)
    assert denied.status_code == 403, denied.text

    settings_auth = await create_user(
        client,
        admin_auth,
        username="kb_options_admin",
        permissions=["knowledge_settings"],
    )
    allowed = await client.get("/api/knowledge-bases/embedding-options", headers=settings_auth)
    assert allowed.status_code == 200, allowed.text
    assert "onnx" in allowed.json()


async def _space_id(client: Any, admin_auth: dict[str, str]) -> str:
    """The deployment's one knowledge space, as an account that may read it."""
    listed = await client.get("/api/knowledge-bases", headers=admin_auth)
    assert listed.status_code == 200, listed.text
    return listed.json()[0]["knowledge_base_id"]


async def _private_base_id(server: Any, admin_id: int) -> str:
    """A base only its owner may read — the other half of the two refusals."""
    base = server.services.knowledge_repo.create_base(owner_user_id=admin_id, name="Private notes")
    return base.id


#: The page's own reads: the list, one base, and that base's documents.
def _page_reads(kb_id: str) -> list[str]:
    root = f"/api/knowledge-bases/{kb_id}"
    return ["/api/knowledge-bases", root, f"{root}/documents"]


async def test_reads_need_a_page_key(env: Any) -> None:
    """②: revoking both keys closes the reads, not only the page.

    The page is reachable through ``knowledge_bases`` *or* ``knowledge_settings``
    and its writes were already gated, so the reads used to be the quiet way back
    in: the entry point disappeared while ``GET /api/knowledge-bases``,
    ``GET /{kb_id}`` and ``GET /{kb_id}/documents`` still answered 200 to a
    direct API call (design §2.4).
    """
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)
    keyless = await create_user(client, admin_auth, username="kb_keyless", permissions=[])

    for path in _page_reads(kb_id):
        refused = await client.get(path, headers=keyless)
        assert refused.status_code == 403, f"{path}: {refused.status_code} {refused.text}"
        assert refused.json()["error"]["code"] == "FORBIDDEN"
        # The refusal names the keys that would open it, both of them: a client
        # cannot tell from one key which page it is being refused.
        assert refused.json()["error"]["details"]["permission"] == [
            "knowledge_bases",
            "knowledge_settings",
        ]


async def test_either_page_key_reads_the_page(env: Any) -> None:
    """②: holding a key still reads — ``knowledge_bases`` or ``knowledge_settings``."""
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)

    for key in ("knowledge_bases", "knowledge_settings"):
        auth = await create_user(
            client, admin_auth, username=f"kb_page_reader_{key}", permissions=[key]
        )
        for path in _page_reads(kb_id):
            allowed = await client.get(path, headers=auth)
            assert allowed.status_code == 200, f"{key} {path}: {allowed.text}"


async def test_search_is_the_pages_read_too(env: Any) -> None:
    """⑤: the page's own search accepts the key that opened the page."""
    client, _server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)
    path = f"/api/knowledge-bases/{kb_id}/search?q=handbook"

    keyless = await create_user(client, admin_auth, username="kb_search_keyless", permissions=[])
    refused = await client.get(path, headers=keyless)
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "FORBIDDEN"

    settings_auth = await create_user(
        client, admin_auth, username="kb_search_settings", permissions=["knowledge_settings"]
    )
    allowed = await client.get(path, headers=settings_auth)
    assert allowed.status_code == 200, allowed.text
    assert allowed.json() == []


async def test_search_rules_are_personal_and_reader_editable_for_files_and_folders(
    env: Any,
) -> None:
    client, server, admin_auth = env
    kb_id = await _space_id(client, admin_auth)
    root = f"/api/knowledge-bases/{kb_id}"
    created = await client.post(f"{root}/folders", headers=admin_auth, json={"path": "legal"})
    assert created.status_code == 201, created.text
    document = server.services.knowledge_repo.create_document(
        kb_id=kb_id,
        filename="contract.md",
        path="legal/contract.md",
        content_type="text/markdown",
        byte_size=1,
    )

    settings_reader = await create_user(
        client, admin_auth, username="kb_rule_settings", permissions=["knowledge_settings"]
    )
    page_reader = await create_user(
        client, admin_auth, username="kb_rule_page", permissions=["knowledge_bases"]
    )
    keyless = await create_user(client, admin_auth, username="kb_rule_keyless", permissions=[])
    rules = f"{root}/search-rules"
    denied = await client.put(
        rules, headers=keyless, json={"path": "legal", "mode": "exclude", "keywords": []}
    )
    assert denied.status_code == 403, denied.text

    saved = await client.put(
        rules,
        headers=settings_reader,
        json={"path": "legal", "mode": "keyword", "keywords": ["renewal"]},
    )
    assert saved.status_code == 200, saved.text
    assert saved.json() == {
        "path": "legal",
        "kind": "folder",
        "mode": "keyword",
        "keywords": ["renewal"],
    }
    file_rule = await client.put(
        rules,
        headers=settings_reader,
        json={"path": document.path, "mode": "hybrid", "keywords": ["signed contract"]},
    )
    assert file_rule.status_code == 200, file_rule.text
    assert file_rule.json() == {
        "path": document.path,
        "kind": "file",
        "mode": "hybrid",
        "keywords": ["signed contract"],
    }
    other = await client.get(rules, headers=page_reader)
    assert other.status_code == 200 and other.json() == []
    assert (await client.get(rules, headers=admin_auth)).json() == []

    own = await client.put(
        rules, headers=page_reader, json={"path": "legal", "mode": "exclude", "keywords": []}
    )
    assert own.status_code == 200, own.text
    invalid = await client.put(
        rules,
        headers=settings_reader,
        json={"path": "../legal", "mode": "keyword", "keywords": ["renewal"]},
    )
    assert invalid.status_code == 422, invalid.text
    current = (await client.get(rules, headers=settings_reader)).json()
    assert {row["path"]: row for row in current} == {
        "legal": {"path": "legal", "kind": "folder", "mode": "keyword", "keywords": ["renewal"]},
        document.path: {
            "path": document.path,
            "kind": "file",
            "mode": "hybrid",
            "keywords": ["signed contract"],
        },
    }

    deleted = await client.delete(rules, headers=settings_reader, params={"path": document.path})
    assert deleted.status_code == 204, deleted.text
    assert (await client.get(rules, headers=settings_reader)).json() == [
        {"path": "legal", "kind": "folder", "mode": "keyword", "keywords": ["renewal"]}
    ]
    deleted_folder = await client.delete(rules, headers=settings_reader, params={"path": "legal"})
    assert deleted_folder.status_code == 204, deleted_folder.text
    assert (await client.get(rules, headers=settings_reader)).json() == []
    assert (await client.get(rules, headers=page_reader)).json() == [
        {"path": "legal", "kind": "folder", "mode": "exclude", "keywords": []}
    ]


async def test_a_write_refusal_is_not_a_read_refusal(env: Any) -> None:
    """④: 403 says which permission was missing, not "no access" to everything."""
    client, server, admin_auth = env
    space_id = await _space_id(client, admin_auth)
    member = await create_user(
        client, admin_auth, username="kb_write_refused", permissions=["knowledge_bases"]
    )

    # The space is readable by everyone and owned by the system, so this account
    # may open it and may not change it: exactly the case the old wording got
    # wrong ("you do not have access to this knowledge base").
    readable = await client.get(f"/api/knowledge-bases/{space_id}", headers=member)
    assert readable.status_code == 200, readable.text
    refused = await client.patch(
        f"/api/knowledge-bases/{space_id}", headers=member, json={"name": "Renamed"}
    )
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "KNOWLEDGE_WRITE_FORBIDDEN"

    # A base this account may not read at all keeps the access refusal.
    admin_id = int(server.services.user_repo.get_by_username("admin").id)
    private_id = await _private_base_id(server, admin_id)
    denied = await client.get(f"/api/knowledge-bases/{private_id}", headers=member)
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "KNOWLEDGE_FORBIDDEN"
