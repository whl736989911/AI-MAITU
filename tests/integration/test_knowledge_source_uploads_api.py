"""Both upload destinations keep their original bytes and enforce source policy."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octop.infra.knowledge.files import document_path
from tests.support.auth import create_user


async def test_both_upload_destinations_and_source_rescan(env: Any, tmp_path: Path) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id = services.knowledge_repo.get_enterprise_space().id
    root = tmp_path / "external-files"
    (root / "reports").mkdir(parents=True)
    (root / "reports" / "existing.txt").write_text("existing document", encoding="utf-8")
    created = await client.post(
        f"/api/knowledge-bases/{kb_id}/data-sources",
        headers=auth,
        json={
            "name": "Company source",
            "kind": "local",
            "folder": {
                "root_path": str(root),
                "read_only": False,
            },
        },
    )
    assert created.status_code == 201, created.text
    ds_id = created.json()["id"]
    synced = await client.post(f"/api/data-sources/{ds_id}/sync", headers=auth)
    assert synced.status_code == 200, synced.text
    targets = await client.get(f"/api/knowledge-bases/{kb_id}/upload-targets", headers=auth)
    assert targets.status_code == 200, targets.text
    assert targets.json() == [{"id": ds_id, "name": "Company source", "folders": ["", "reports"]}]
    assert str(root) not in targets.text

    platform_bytes = b"platform-owned note"
    platform = await client.post(
        f"/api/knowledge-bases/{kb_id}/documents",
        headers=auth,
        files={"upload": ("platform.txt", platform_bytes, "text/plain")},
    )
    assert platform.status_code == 201, platform.text
    platform_doc = platform.json()
    assert platform_doc["data_source_id"] is None
    assert document_path(kb_id, platform_doc["id"], "platform.txt").read_bytes() == platform_bytes

    source_bytes = b"source-owned calibration evidence"
    uploaded = await client.post(
        f"/api/knowledge-bases/{kb_id}/data-sources/{ds_id}/documents",
        headers=auth,
        data={"path": "reports/certificate.txt"},
        files={"upload": ("certificate.txt", source_bytes, "text/plain")},
    )
    assert uploaded.status_code == 201, uploaded.text
    source_doc = uploaded.json()
    assert source_doc["data_source_id"] == ds_id
    assert source_doc["source_path"] == "reports/certificate.txt"
    assert source_doc["has_original"] is True
    assert (root / "reports" / "certificate.txt").read_bytes() == source_bytes
    assert not document_path(kb_id, source_doc["id"], "certificate.txt").exists()
    downloaded = await client.get(
        f"/api/knowledge-bases/{kb_id}/documents/{source_doc['id']}/file",
        headers=auth,
    )
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.content == source_bytes
    rescan = await client.post(f"/api/data-sources/{ds_id}/sync", headers=auth)
    assert rescan.status_code == 200, rescan.text
    rows = [
        r
        for r in services.knowledge_repo.list_source_files(ds_id)
        if r.source_path == "reports/certificate.txt"
    ]
    assert [r.id for r in rows] == [source_doc["id"]]


async def test_source_upload_http_refuses_readonly_conflict_and_ungranted_user(
    env: Any, tmp_path: Path
) -> None:
    client, server, auth = env
    services = server.services
    services.settings_repo.set("knowledge_bases_enabled", "true")
    kb_id = services.knowledge_repo.get_enterprise_space().id
    root = tmp_path / "source"
    root.mkdir()
    source = await client.post(
        f"/api/knowledge-bases/{kb_id}/data-sources",
        headers=auth,
        json={"name": "Read-only source", "kind": "local", "folder": {"root_path": str(root)}},
    )
    assert source.status_code == 201, source.text
    ds_id = source.json()["id"]
    path = f"/api/knowledge-bases/{kb_id}/data-sources/{ds_id}/documents"
    denied = await client.post(
        path, headers=auth, files={"upload": ("new.txt", b"no", "text/plain")}
    )
    assert denied.status_code == 403, denied.text
    assert denied.json()["error"]["code"] == "DATA_SOURCE_READ_ONLY"
    assert not (root / "new.txt").exists()
    assert services.knowledge_repo.count_documents(kb_id) == 0

    changed = await client.patch(
        f"/api/data-sources/{ds_id}",
        headers=auth,
        json={"folder": {"root_path": str(root), "read_only": False}},
    )
    assert changed.status_code == 200, changed.text
    (root / "existing.txt").write_bytes(b"keep original")
    conflict = await client.post(
        path, headers=auth, files={"upload": ("existing.txt", b"replacement", "text/plain")}
    )
    assert conflict.status_code == 409, conflict.text
    assert conflict.json()["error"]["code"] == "DATA_SOURCE_UPLOAD_CONFLICT"
    assert (root / "existing.txt").read_bytes() == b"keep original"

    member = await create_user(
        client, auth, username="read-only-member", permissions=["knowledge_bases"]
    )
    unauthorized = await client.post(
        path, headers=member, files={"upload": ("member.txt", b"no", "text/plain")}
    )
    assert unauthorized.status_code == 403, unauthorized.text
    assert unauthorized.json()["error"]["code"] == "KNOWLEDGE_WRITE_FORBIDDEN"
    assert not (root / "member.txt").exists()
