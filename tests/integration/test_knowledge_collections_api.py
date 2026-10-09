"""Consumer-visible independent selection, reading accumulation and source invalidation."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octop.infra.knowledge.jobs import process_document
from octop.infra.knowledge.service import KnowledgeService
from tests.support.auth import create_user, resolve_user_id


async def test_independent_targets_reading_partition_and_source_update(env: Any) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    folder = services.knowledge_repo.ensure_folder(kb_id, "legal")
    original = service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="contract.md",
        path="legal/contract.md",
        content_type="text/markdown",
        content=b"# Payment\n\nPayment is due in 30 days.\n\n# Delivery\n\nDelivery is by rail.\n",
    )
    process_document(services, kb_id, original.id)
    root = f"/api/knowledge-bases/{kb_id}"
    created = await client.post(
        f"{root}/collections", headers=auth, json={"document_ids": [folder.id, original.id]}
    )
    assert created.status_code == 201, created.text
    rows = {row["target_document_id"]: row for row in created.json()}
    folder_collection, file_collection = rows[folder.id], rows[original.id]
    assert folder_collection["id"] != file_collection["id"]
    assert folder_collection["keywords"] == file_collection["keywords"] == []
    collection_url = f"{root}/collections/{folder_collection['id']}"
    changed = await client.patch(collection_url, headers=auth, json={"keywords": ["Payment"]})
    assert changed.status_code == 200, changed.text
    generated = await client.post(f"{collection_url}/generate", headers=auth)
    assert generated.status_code == 200, generated.text
    documents = await client.get(f"{collection_url}/documents", headers=auth)
    assert documents.status_code == 200, documents.text
    initial = documents.json()
    assert "Payment is due in 30 days." in initial["keyword_document"]
    assert "Delivery is by rail." not in initial["keyword_document"]
    assert "Delivery is by rail." not in initial["reading_document"]
    storage = Path(initial["storage_path"])
    assert storage.is_relative_to(services.paths.root / "knowledge-collections")
    assert {entry.name for entry in storage.iterdir()} == {"keyword-index.md", "reading-index.md"}
    found = await client.get(f"{root}/search", headers=auth, params={"q": "rail", "limit": 5})
    assert found.status_code == 200, found.text
    hit = next(
        hit for hit in found.json() if hit["document_id"] == original.id and hit["segment_id"]
    )
    verified = await client.get(
        f"{root}/documents/{original.id}/segments/{hit['segment_id']}", headers=auth
    )
    assert verified.status_code == 200, verified.text
    assert verified.json()["verified"] is True
    for collection in (folder_collection, file_collection):
        result = await client.get(f"{root}/collections/{collection['id']}/documents", headers=auth)
        assert "Delivery is by rail." in result.json()["reading_document"]
    service.update_text_document(
        kb_id,
        original.id,
        actor_user_id=actor,
        content="# Payment\n\nPayment is due in 45 days.\n\n# Delivery\n\nDelivery is by road.\n",
    )
    pending = await client.get(f"{collection_url}/documents", headers=auth)
    assert "Payment is due in 30 days." not in pending.json()["keyword_document"]
    assert "Delivery is by rail." not in pending.json()["reading_document"]
    process_document(services, kb_id, original.id)
    updated = await client.get(f"{collection_url}/documents", headers=auth)
    assert "Payment is due in 45 days." in updated.json()["keyword_document"]
    assert "Delivery is by rail." not in updated.json()["reading_document"]
    deleted = await client.delete(collection_url, headers=auth)
    assert deleted.status_code == 204, deleted.text
    assert services.knowledge_repo.get_document(original.id) is not None
    remaining = await client.get(f"{root}/collections", headers=auth)
    assert {row["id"] for row in remaining.json()} == {file_collection["id"]}


async def test_collections_are_personal_and_creation_validates_all_targets(env: Any) -> None:
    client, server, auth = env
    services = server.services
    kb_id = KnowledgeService(services).enterprise_space().id
    folder = services.knowledge_repo.ensure_folder(kb_id, "reports")
    root = f"/api/knowledge-bases/{kb_id}/collections"
    invalid = await client.post(root, headers=auth, json={"document_ids": [folder.id, "missing"]})
    assert invalid.status_code == 404, invalid.text
    assert (await client.get(root, headers=auth)).json() == []
    created = await client.post(root, headers=auth, json={"document_ids": [folder.id]})
    assert created.status_code == 201, created.text
    collection_id = created.json()[0]["id"]
    other = await create_user(client, auth, username="collection-reader")
    assert (await client.get(root, headers=other)).json() == []
    leaked = await client.get(f"{root}/{collection_id}/documents", headers=other)
    assert leaked.status_code in {403, 404}, leaked.text
    own = await client.post(root, headers=other, json={"document_ids": [folder.id]})
    assert own.status_code == 201, own.text
    assert own.json()[0]["id"] != collection_id


async def test_generation_failure_reports_source_index_state_and_keeps_completed_content(
    env: Any,
) -> None:
    import asyncio
    from io import BytesIO

    from pypdf import PdfWriter

    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    folder = services.knowledge_repo.ensure_folder(kb_id, "mixed")
    readable = service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="readable.md",
        path="mixed/readable.md",
        content_type="text/markdown",
        content=b"needle source evidence",
    )
    await asyncio.get_running_loop().run_in_executor(
        None, process_document, services, kb_id, readable.id
    )
    pdf = PdfWriter()
    pdf.add_blank_page(width=100, height=100)
    payload = BytesIO()
    pdf.write(payload)
    service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="blank.pdf",
        path="mixed/blank.pdf",
        content_type="application/pdf",
        content=payload.getvalue(),
    )
    root = f"/api/knowledge-bases/{kb_id}/collections"
    created = await client.post(root, headers=auth, json={"document_ids": [folder.id]})
    assert created.status_code == 201
    collection_url = f"{root}/{created.json()[0]['id']}"
    saved = await client.patch(collection_url, headers=auth, json={"keywords": ["needle"]})
    assert saved.status_code == 200
    generated = await client.post(f"{collection_url}/generate", headers=auth)
    assert generated.status_code == 409, generated.text
    assert generated.json()["error"]["code"] == "KNOWLEDGE_INDEX_NOT_READY"
    documents = await client.get(f"{collection_url}/documents", headers=auth)
    assert documents.status_code == 200
    current = documents.json()
    assert current["collection"]["status"] == "partial"
    assert current["collection"]["coverage"]["failed"] == 1
    assert "needle source evidence" in current["keyword_document"]
