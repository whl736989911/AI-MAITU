"""Unit tests: data sources inherit their knowledge base's ACL over HTTP.

The module key (``knowledge_bases``) only opens the door. A member holding it
who can *read* a base still may not create, delete, or sync its data sources —
and an unimplemented sync must answer with an error, never a success.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """A bootstrapped server, an owner (admin), a member, and one seeded source."""
    async with octop_client(tmp_path) as (client, srv):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        member_auth = await create_user(
            client, admin_auth, username="reader", permissions=["knowledge_bases"]
        )
        assert srv.services is not None
        services = srv.services
        admin_id = int(services.user_repo.get_by_username("admin").id)
        # ``shared`` puts the base (and therefore its sources) on the ACL as
        # public, so the member may read it without owning it.
        base = services.knowledge_repo.create_base(owner_user_id=admin_id, name="Docs", shared=True)
        document = services.knowledge_repo.create_document(
            kb_id=base.id, filename="handbook.md", content_type="text/markdown", byte_size=9
        )
        source = services.data_sources_repo.create(
            knowledge_base_id=base.id,
            name="Handbook",
            kind="upload",
            config={"document_id": document.id, "path": "handbook.md"},
            created_by=admin_id,
        )
        yield {
            "client": client,
            "admin": admin_auth,
            "member": member_auth,
            "kb": base.id,
            "document": document.id,
            "source": source.id,
        }


async def test_member_reads_the_sources_of_a_visible_base(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    listed = await client.get(
        f"/api/knowledge-bases/{api['kb']}/data-sources", headers=api["member"]
    )
    assert listed.status_code == 200, listed.text
    rows = listed.json()
    assert [row["data_source_id"] for row in rows] == [api["source"]]
    assert rows[0]["kind"] == "upload"
    assert rows[0]["config"]["path"] == "handbook.md"

    fetched = await client.get(f"/api/data-sources/{api['source']}", headers=api["member"])
    assert fetched.status_code == 200, fetched.text
    assert fetched.json()["data_source_id"] == api["source"]


async def test_member_may_not_create_delete_or_sync(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    created = await client.post(
        f"/api/knowledge-bases/{api['kb']}/data-sources",
        headers=api["member"],
        json={"name": "Sneaky", "kind": "url", "config": {"url": "https://example.com"}},
    )
    assert created.status_code == 403, created.text
    assert created.json()["error"]["code"] == "KNOWLEDGE_FORBIDDEN"

    synced = await client.post(f"/api/data-sources/{api['source']}/sync", headers=api["member"])
    assert synced.status_code == 403, synced.text

    deleted = await client.delete(f"/api/data-sources/{api['source']}", headers=api["member"])
    assert deleted.status_code == 403, deleted.text

    # Nothing was created, synced, or removed by the refused calls.
    still_there = await client.get(f"/api/data-sources/{api['source']}", headers=api["admin"])
    assert still_there.status_code == 200, still_there.text
    assert still_there.json()["sync_status"] == "idle"


async def test_owner_creates_and_deletes_a_source(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    created = await client.post(
        f"/api/knowledge-bases/{api['kb']}/data-sources",
        headers=api["admin"],
        json={
            "name": "Handbook copy",
            "kind": "upload",
            "config": {"document_id": api["document"]},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["data_source_id"]
    assert body["sync_status"] == "idle"

    deleted = await client.delete(
        f"/api/data-sources/{body['data_source_id']}", headers=api["admin"]
    )
    assert deleted.status_code == 204, deleted.text
    assert (
        await client.get(f"/api/data-sources/{body['data_source_id']}", headers=api["admin"])
    ).status_code == 404


async def test_sync_of_an_unimplemented_kind_is_an_error(api: dict[str, Any]) -> None:
    """``url``/``connector`` sources are objects only: sync must not pretend."""
    client: httpx.AsyncClient = api["client"]
    created = await client.post(
        f"/api/knowledge-bases/{api['kb']}/data-sources",
        headers=api["admin"],
        json={"name": "Site", "kind": "url", "config": {"url": "https://example.com"}},
    )
    assert created.status_code == 201, created.text
    ds_id = created.json()["data_source_id"]

    synced = await client.post(f"/api/data-sources/{ds_id}/sync", headers=api["admin"])

    assert synced.status_code != 200, synced.text
    assert synced.status_code == 400, synced.text
    assert synced.json()["error"]["code"] == "DATA_SOURCE_SYNC_UNSUPPORTED"
    assert synced.json()["error"]["details"]["kind"] == "url"

    failed = await client.get(f"/api/data-sources/{ds_id}", headers=api["admin"])
    assert failed.status_code == 200, failed.text
    assert failed.json()["sync_status"] == "failed"
    assert failed.json()["sync_error"]
    assert failed.json()["last_synced_at"] is None


async def test_upload_sync_that_cannot_ingest_reports_the_failure(api: dict[str, Any]) -> None:
    """No embedding backend is configured here, so the ingest cannot run.

    The point is the report: the call fails and the row records the failure
    instead of a silent ``ok``.
    """
    client: httpx.AsyncClient = api["client"]

    synced = await client.post(f"/api/data-sources/{api['source']}/sync", headers=api["admin"])

    assert synced.status_code != 200, synced.text
    assert synced.json()["error"]["code"] in {
        "KNOWLEDGE_FEATURE_DISABLED",
        "KNOWLEDGE_PREREQUISITES_FAILED",
    }
    failed = await client.get(f"/api/data-sources/{api['source']}", headers=api["admin"])
    assert failed.status_code == 200, failed.text
    assert failed.json()["sync_status"] == "failed"
    assert failed.json()["sync_error"]
    assert failed.json()["last_synced_at"] is None


async def test_unknown_knowledge_base_is_a_404(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    missing = await client.get("/api/knowledge-bases/nosuchkb/data-sources", headers=api["member"])

    assert missing.status_code == 404, missing.text
