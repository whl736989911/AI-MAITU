"""AI suggestion listing, review and write-back over HTTP."""

from __future__ import annotations

from typing import Any

from octop.infra.knowledge.enrichment import KnowledgeEnrichmentService
from octop.infra.sharing import AclEntry
from tests.support.auth import create_user, resolve_user_id

SCHEMA = {
    "fields": [
        {"key": "drawing_no", "label": "Drawing", "type": "text"},
        {"key": "sheet", "label": "Sheet", "type": "enum", "options": ["A0", "A1"]},
    ]
}


async def _base(client: Any, server: Any, admin: dict[str, str]) -> tuple[str, str, int]:
    admin_id = await resolve_user_id(client, admin, "admin")
    base = server.services.knowledge_repo.create_base(owner_user_id=admin_id, name="AI review")
    root = f"/api/knowledge-bases/{base.id}"
    assert (
        await client.put(f"{root}/metadata-schema", headers=admin, json=SCHEMA)
    ).status_code == 200
    upload = await client.post(
        f"{root}/documents",
        headers=admin,
        files={"upload": ("drawing.pdf", b"%PDF-1.4\n", "application/pdf")},
    )
    assert upload.status_code == 201, upload.text
    return base.id, upload.json()["document_id"], admin_id


async def test_suggestion_apply_and_reject(env: Any) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id, doc_id, admin_id = await _base(client, server, admin)
    repo = server.services.knowledge_repo
    document = repo.get_document(doc_id)
    assert document is not None
    current_hash = document.content_hash
    applied = repo.create_suggestion(
        suggestion_id="sug-apply",
        document_id=doc_id,
        kb_id=kb_id,
        content_hash=current_hash,
        summary="summary",
        keywords=["keyword"],
        fields={"drawing_no": "A-102", "sheet": "A0"},
        model="test/model",
    )
    repo.create_suggestion(
        suggestion_id="sug-reject",
        document_id=doc_id,
        kb_id=kb_id,
        content_hash=current_hash,
        summary="other",
        keywords=[],
        fields={},
        model="test/model",
    )
    root = f"/api/knowledge-bases/{kb_id}"

    listed = await client.get(f"{root}/suggestions", headers=admin, params={"status": "pending"})
    assert listed.status_code == 200, listed.text
    assert {row["suggestion_id"] for row in listed.json()} == {"sug-apply", "sug-reject"}

    apply = await client.post(
        f"{root}/suggestions/sug-apply/apply",
        headers=admin,
        json={"fields": {"drawing_no": "A-102", "sheet": "A0"}},
    )
    assert apply.status_code == 200, apply.text
    assert apply.json()["status"] == "applied"
    document = repo.get_document(doc_id)
    assert document is not None
    assert document.metadata == {"drawing_no": "A-102", "sheet": "A0"}
    assert applied["status"] == "pending"

    reject = await client.post(f"{root}/suggestions/sug-reject/reject", headers=admin)
    assert reject.status_code == 200, reject.text
    assert reject.json()["status"] == "rejected"

    reader = await create_user(client, admin, username="suggestion_reader")
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
    pending = repo.create_suggestion(
        suggestion_id="sug-denied",
        document_id=doc_id,
        kb_id=kb_id,
        content_hash="",
        summary="denied",
        keywords=[],
        fields={},
        model="test/model",
    )
    assert pending["status"] == "pending"
    denied = await client.post(f"{root}/suggestions/sug-denied/apply", headers=reader, json={})
    assert denied.status_code == 403, denied.text


async def test_enrich_endpoint_returns_the_pending_suggestion(env: Any, monkeypatch) -> None:
    client, server, admin = env
    server.services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id, doc_id, _admin_id = await _base(client, server, admin)
    canned = {
        "suggestion_id": "sug-enrich",
        "document_id": doc_id,
        "kb_id": kb_id,
        "status": "pending",
        "summary": "s",
        "keywords": [],
        "fields": {},
        "content_hash": "",
        "model": "test/model",
        "error": None,
        "created_at": 0,
        "updated_at": 0,
    }

    def fake_enrich(self, kb, doc, **kwargs):
        assert (kb, doc) == (kb_id, doc_id)
        return canned

    monkeypatch.setattr(KnowledgeEnrichmentService, "enrich_document", fake_enrich)
    response = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents/{doc_id}/enrich", headers=admin
    )
    assert response.status_code == 200, response.text
    assert response.json() == canned
