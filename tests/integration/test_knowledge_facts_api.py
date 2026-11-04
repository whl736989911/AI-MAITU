"""Located fact search over HTTP with document ACL filtering."""

from __future__ import annotations

from typing import Any

from octop.infra.sharing import AclEntry
from tests.support.auth import create_user, resolve_user_id

FACTS = [
    {
        "fact_type": "diameter",
        "value": "φ120",
        "normalized": "φ120",
        "locator": {"page": 1},
        "extractor": "measurement",
    },
    {
        "fact_type": "clause",
        "value": "5.3",
        "normalized": "5.3",
        "locator": {"page": 2},
        "extractor": "clause",
    },
]


async def test_fact_listing_and_search_respect_document_acl(env: Any) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    admin_id = await resolve_user_id(client, admin, "admin")
    base = server.services.knowledge_repo.create_base(owner_user_id=admin_id, name="Facts")
    server.services.repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_base",
            resource_id=base.id,
            owner_user_id=admin_id,
            visibility="public",
            unit_key=None,
            version=1,
            permission="read",
        )
    )
    visible = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers=admin,
        files={"upload": ("visible.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert visible.status_code == 201, visible.text
    hidden = await client.post(
        f"/api/knowledge-bases/{base.id}/documents",
        headers=admin,
        files={"upload": ("hidden.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert hidden.status_code == 201, hidden.text
    visible_id = visible.json()["document_id"]
    hidden_id = hidden.json()["document_id"]
    repo = server.services.knowledge_repo
    repo.replace_document_facts(visible_id, base.id, FACTS)
    repo.replace_document_facts(hidden_id, base.id, FACTS)
    server.services.repos.resource_acl_repo.upsert(
        AclEntry(
            resource_type="knowledge_document",
            resource_id=hidden_id,
            owner_user_id=admin_id,
            visibility="private",
            unit_key=None,
            version=1,
            permission="read",
        )
    )
    reader = await create_user(client, admin, username="facts_reader")
    root = f"/api/knowledge-bases/{base.id}"

    own = await client.get(f"{root}/documents/{visible_id}/facts", headers=admin)
    assert own.status_code == 200, own.text
    assert {fact["value"] for fact in own.json()} == {"φ120", "5.3"}

    search = await client.get(
        f"{root}/facts", headers=reader, params={"q": "120", "fact_type": "diameter"}
    )
    assert search.status_code == 200, search.text
    rows = search.json()
    assert [row["document_id"] for row in rows] == [visible_id]
    assert rows[0]["filename"] == "visible.pdf"
    assert rows[0]["locator"] == {"page": 1}

    denied = await client.get(f"{root}/documents/{hidden_id}/facts", headers=reader)
    assert denied.status_code in (403, 404)
