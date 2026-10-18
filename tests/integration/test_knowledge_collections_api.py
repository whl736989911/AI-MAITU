"""Consumer-visible prompt collections, shared views and honest generation state."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

from octop.infra.db.repos.knowledge_collections import KnowledgeCollectionsRepo
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.knowledge.source_access import KnowledgeSourceService
from tests.support.auth import create_user, resolve_user_id


async def test_collections_register_prompt_and_share_by_source_acl(env: Any) -> None:
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
        content=b"# Payment\n\nPayment is due in 30 days.\n",
    )
    root = f"/api/knowledge-bases/{kb_id}"
    created = await client.post(
        root + "/collections",
        headers=auth,
        json={
            "document_ids": [folder.id, original.id],
            "prompt": "Extract every payment obligation",
        },
    )
    assert created.status_code == 201, created.text
    rows = created.json()
    assert {row["prompt"] for row in rows} == {"Extract every payment obligation"}
    assert {row["generation"]["state"] for row in rows} == {"idle"}
    folder_collection, file_collection = rows[0], rows[1]
    # Nothing is read or parsed at creation: no evidence exists yet.
    repo = KnowledgeCollectionsRepo(services.db)
    assert repo.reads_for_documents(kb_id, [original.id]) == []
    assert repo.topics(folder_collection["id"]) == []
    # Re-registering the same target returns the existing collection unchanged
    # (one collection per owner and target document).
    silent = await client.post(
        root + "/collections", headers=auth, json={"document_ids": [original.id]}
    )
    assert silent.status_code == 201, silent.text
    assert silent.json()[0]["id"] == file_collection["id"]
    # Collections are visible through the source ACL, not private to the creator.
    other = await create_user(client, auth, username="collection-reader")
    listed = await client.get(root + "/collections", headers=other)
    assert listed.status_code == 200, listed.text
    assert {row["id"] for row in listed.json()} >= {
        folder_collection["id"],
        file_collection["id"],
    }
    shared_view = await client.get(
        f"{root}/collections/{folder_collection['id']}/documents", headers=other
    )
    assert shared_view.status_code == 200, shared_view.text
    body = shared_view.json()
    assert set(body) == {"collection", "topic_document", "reading_document", "storage_path"}
    # Shared readers can view but never modify prompt, name or lifecycle.
    denied = await client.patch(
        f"{root}/collections/{file_collection['id']}",
        headers=other,
        json={"prompt": "override"},
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "KNOWLEDGE_WRITE_FORBIDDEN"
    denied_delete = await client.delete(
        f"{root}/collections/{file_collection['id']}", headers=other
    )
    assert denied_delete.status_code == 403, denied_delete.text
    changed = await client.patch(
        f"{root}/collections/{file_collection['id']}",
        headers=auth,
        json={"prompt": "Extract payment and delivery terms"},
    )
    assert changed.status_code == 200, changed.text
    assert changed.json()["prompt"] == "Extract payment and delivery terms"
    deleted = await client.delete(f"{root}/collections/{file_collection['id']}", headers=auth)
    assert deleted.status_code == 204, deleted.text
    assert services.knowledge_repo.get_document(original.id) is not None


async def test_source_read_updates_both_views_of_all_containing_collections(env: Any) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    folder = services.knowledge_repo.ensure_folder(kb_id, "accruals")
    original = service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="notes.md",
        path="accruals/notes.md",
        content_type="text/markdown",
        content=b"# Accrual\n\nInterest accrues monthly on outstanding balances.\n",
    )
    root = f"/api/knowledge-bases/{kb_id}"
    created = await client.post(
        root + "/collections",
        headers=auth,
        json={"document_ids": [folder.id, original.id], "prompt": "accrual rules"},
    )
    assert created.status_code == 201, created.text
    collections = created.json()
    folder_collection, file_collection = collections
    # One authorized source read fans out to every containing collection.
    source = KnowledgeSourceService(services)

    def read_once() -> dict[str, Any]:
        return source.read_document(kb_id, original.id, actor_user_id=actor)

    result = await asyncio.get_running_loop().run_in_executor(None, read_once)
    assert result["status"] == "read", result
    for row in collections:
        view = await client.get(f"{root}/collections/{row['id']}/documents", headers=auth)
        assert view.status_code == 200, view.text
        document = view.json()
        assert "Interest accrues monthly" in document["reading_document"]
        assert document["collection"]["coverage"]["read"] >= 1
        assert document["collection"]["coverage"]["total"] >= 1
    # A repeated identical read dedups instead of duplicating evidence: the
    # rendered reading view still shows the excerpt exactly once.
    await asyncio.get_running_loop().run_in_executor(None, read_once)
    deduped = await client.get(
        f"{root}/collections/{file_collection['id']}/documents", headers=auth
    )
    assert deduped.status_code == 200, deduped.text
    assert deduped.json()["reading_document"].count("Interest accrues monthly") == 1
    # A stale version is refused and never rendered.
    collections_api = KnowledgeCollectionService(services)

    def record_stale() -> dict[str, Any]:
        return collections_api.record_source_read(
            kb_id,
            original.id,
            {
                "status": "read",
                "version": "stale-version",
                "text": "fabricated",
                "evidence_id": "source:stale",
                "blocks": [],
                "verified": True,
            },
            actor_user_id=actor,
        )

    outcome = await asyncio.get_running_loop().run_in_executor(None, record_stale)
    assert outcome["reason"] == "stale_version"


async def test_generate_is_honest_without_model_and_requires_prompt(env: Any) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    actor = await resolve_user_id(client, auth, "admin")
    service = KnowledgeService(services)
    kb_id = service.enterprise_space().id
    folder = services.knowledge_repo.ensure_folder(kb_id, "org")
    service.upload_document(
        kb_id,
        actor_user_id=actor,
        filename="terms.md",
        path="org/terms.md",
        content_type="text/markdown",
        content=b"# Terms\n\nThe vendor invoices quarterly.\n",
    )
    root = f"/api/knowledge-bases/{kb_id}/collections"
    created = await client.post(root, headers=auth, json={"document_ids": [folder.id]})
    assert created.status_code == 201, created.text
    collection = created.json()[0]
    collection_url = f"{root}/{collection['id']}"
    blocked = await client.post(f"{collection_url}/generate", headers=auth)
    assert blocked.status_code == 400, blocked.text
    assert blocked.json()["error"]["code"] == "KNOWLEDGE_COLLECTION_INVALID"
    saved = await client.patch(
        collection_url, headers=auth, json={"prompt": "List invoicing cadence"}
    )
    assert saved.status_code == 200, saved.text
    generated = await client.post(f"{collection_url}/generate", headers=auth)
    assert generated.status_code == 200, generated.text
    payload = generated.json()
    # No model is configured in this environment: the failure is explicit and
    # the reads still accumulated reading evidence.
    assert payload["generation"]["state"] == "error", payload
    assert payload["coverage"]["failed"] >= 1
    documents = await client.get(f"{collection_url}/documents", headers=auth)
    assert documents.status_code == 200, documents.text
    body = documents.json()
    assert "The vendor invoices quarterly." in body["reading_document"]
    assert "topic model is not configured" in body["topic_document"]
    assert "The vendor invoices quarterly." not in body["topic_document"]
    storage = Path(body["storage_path"])
    assert storage.is_relative_to(services.paths.root / "knowledge-collections")
    assert {entry.name for entry in storage.iterdir()} == {
        "topic-index.md",
        "reading-index.md",
    }
