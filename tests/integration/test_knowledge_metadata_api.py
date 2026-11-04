"""Metadata schemas, values and filtered listings over HTTP."""

from __future__ import annotations

from typing import Any

from octop.infra.sharing import AclEntry
from tests.support.auth import create_user, resolve_user_id

SCHEMA = {
    "fields": [
        {"key": "drawing_no", "label": "Drawing no.", "type": "text", "required": True},
        {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A0", "A1"]},
    ]
}


async def _base(
    client: Any, server: Any, admin: dict[str, str], name: str = "Metadata"
) -> tuple[str, int]:
    admin_id = await resolve_user_id(client, admin, "admin")
    base = server.services.knowledge_repo.create_base(owner_user_id=admin_id, name=name)
    return base.id, admin_id


async def test_metadata_schema_values_and_filters(env: Any) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id, _admin_id = await _base(client, server, admin)
    root = f"/api/knowledge-bases/{kb_id}"

    put = await client.put(f"{root}/metadata-schema", headers=admin, json=SCHEMA)
    assert put.status_code == 200, put.text
    assert [field["key"] for field in put.json()["fields"]] == ["drawing_no", "sheet"]
    got = await client.get(f"{root}/metadata-schema", headers=admin)
    assert got.status_code == 200
    assert got.json()["fields"][0]["required"] is True

    upload = await client.post(
        f"{root}/documents",
        headers=admin,
        files={"upload": ("drawing.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    doc_id = upload.json()["document_id"]

    patch = await client.patch(
        f"{root}/documents/{doc_id}/metadata",
        headers=admin,
        json={"metadata": {"drawing_no": "A-102", "sheet": "A0"}},
    )
    assert patch.status_code == 200, patch.text
    assert patch.json()["metadata"] == {"drawing_no": "A-102", "sheet": "A0"}

    listing = await client.get(f"{root}/documents", headers=admin)
    assert listing.status_code == 200
    rows = {row["document_id"]: row for row in listing.json()}
    assert rows[doc_id]["metadata"] == {"drawing_no": "A-102", "sheet": "A0"}

    matched = await client.get(
        f"{root}/documents", headers=admin, params={"metadata": '{"drawing_no":"a-102"}'}
    )
    assert [row["document_id"] for row in matched.json()] == [doc_id]
    missed = await client.get(
        f"{root}/documents", headers=admin, params={"metadata": '{"sheet":"A1"}'}
    )
    assert missed.json() == []

    search = await client.get(
        f"{root}/search",
        headers=admin,
        params={"q": "anything", "metadata": '{"drawing_no":"A-102"}'},
    )
    assert search.status_code == 200


async def test_metadata_validation_errors_are_400(env: Any) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id, _admin_id = await _base(client, server, admin, "Metadata invalid")
    root = f"/api/knowledge-bases/{kb_id}"

    bad_field = await client.put(
        f"{root}/metadata-schema",
        headers=admin,
        json={"fields": [{"key": "1bad", "type": "text"}]},
    )
    assert bad_field.status_code == 400
    assert bad_field.json()["error"]["code"] == "KNOWLEDGE_METADATA_INVALID"

    await client.put(f"{root}/metadata-schema", headers=admin, json=SCHEMA)
    upload = await client.post(
        f"{root}/documents",
        headers=admin,
        files={"upload": ("a.txt", b"hello", "text/plain")},
    )
    doc_id = upload.json()["document_id"]
    bad_value = await client.patch(
        f"{root}/documents/{doc_id}/metadata",
        headers=admin,
        json={"metadata": {"sheet": "A9"}},
    )
    assert bad_value.status_code == 400
    assert bad_value.json()["error"]["code"] == "KNOWLEDGE_METADATA_INVALID"

    bad_filter = await client.get(
        f"{root}/documents", headers=admin, params={"metadata": "not-json"}
    )
    assert bad_filter.status_code == 400


async def test_reader_can_view_schema_but_cannot_change_it(env: Any) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id, admin_id = await _base(client, server, admin, "Read only metadata")
    server.services.repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=kb_id,
            owner_user_id=admin_id,
            visibility="public",
            unit_key=None,
            version=1,
            permission="read",
        )
    )
    reader = await create_user(client, admin, username="metadata_reader")
    root = f"/api/knowledge-bases/{kb_id}"

    assert (await client.get(f"{root}/metadata-schema", headers=reader)).status_code == 200
    denied = await client.put(f"{root}/metadata-schema", headers=reader, json=SCHEMA)
    assert denied.status_code == 403
