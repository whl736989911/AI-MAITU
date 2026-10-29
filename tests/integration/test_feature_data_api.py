"""Feature business data over HTTP: schema, records, history and attachments."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.auth import create_user, resolve_user_id

AGENT_ID = "feat-data-test"
EXAMPLES = Path(__file__).resolve().parents[2] / "docs" / "examples"


async def _create_feature(client, auth) -> None:
    response = await client.post(
        "/api/features", headers=auth, json={"feature_id": "data-test", "name": "数据测试"}
    )
    assert response.status_code == 201, response.text


async def _activate_workflow(client, auth) -> None:
    workflow = {
        "version": 1,
        "status": "active",
        "inputs": {
            "type": "object",
            "properties": {"topic": {"type": "string", "title": {"zh": "主题", "en": "Topic"}}},
        },
        "steps": [{"id": "run", "name": "执行", "prompt": "执行任务"}],
        "outputs": [],
        "rules": [],
    }
    response = await client.put(
        f"/api/agents/{AGENT_ID}/workflow", headers=auth, json={"workflow": workflow}
    )
    assert response.status_code == 200, response.text


def _schema() -> dict:
    supplier = json.loads((EXAMPLES / "feature-data-supplier.json").read_text(encoding="utf-8"))
    document = json.loads(
        (EXAMPLES / "feature-data-controlled-document.json").read_text(encoding="utf-8")
    )
    return {"version": 1, "objects": [supplier["objects"][0], document["objects"][0]]}


async def test_feature_data_schema_records_history_and_files(env_with_provider) -> None:
    client, _server, admin_auth = env_with_provider
    author = await create_user(client, admin_auth, username="data_author")
    await _create_feature(client, author)
    await _activate_workflow(client, author)
    base = f"/api/agents/{AGENT_ID}/feature-data"

    schema = await client.put(base + "/schema", headers=admin_auth, json={"schema": _schema()})
    assert schema.status_code == 200, schema.text
    read_schema = await client.get(base + "/schema", headers=author)
    assert read_schema.status_code == 200
    assert read_schema.json()["schema"]["objects"][0]["key"] == "supplier"

    created = await client.post(
        base + "/supplier/records",
        headers=author,
        json={"values": {"name": "Acme", "expires_at": "2030-01-01"}},
    )
    assert created.status_code == 200, created.text
    record = created.json()
    assert record["visibility"] == "feature"
    assert record["title"] == "Acme"
    assert record["reminders"]

    controlled = await client.post(
        base + "/controlled_document/records",
        headers=author,
        json={
            "values": {
                "doc_no": "Q-001",
                "title": "Quality manual",
                "version": "1.0",
                "status": "effective",
                "effective_at": "2030-01-01",
            }
        },
    )
    assert controlled.status_code == 200, controlled.text
    assert controlled.json()["title"] == "Quality manual"

    listed = await client.get(base + "/supplier/records", headers=author)
    assert listed.status_code == 200
    assert listed.json()["total"] == 1

    fetched = await client.get(base + f"/records/{record['id']}", headers=author)
    assert fetched.status_code == 200
    assert fetched.json()["revision"] == 1

    patched = await client.patch(
        base + f"/records/{record['id']}",
        headers=author,
        json={"values": {"name": "Acme Ltd"}, "expected_revision": 1},
    )
    assert patched.status_code == 200, patched.text
    assert patched.json()["revision"] == 2

    stale = await client.patch(
        base + f"/records/{record['id']}",
        headers=author,
        json={"values": {"name": "stale"}, "expected_revision": 1},
    )
    assert stale.status_code == 409

    history = await client.get(base + f"/records/{record['id']}/history", headers=author)
    assert history.status_code == 200
    assert [item["kind"] for item in history.json()["changes"]] == ["update", "create"]

    uploaded = await client.post(
        base + f"/records/{record['id']}/files/license",
        headers=author,
        data={"note": "first"},
        files={"file": ("license.pdf", b"first version", "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    first = uploaded.json()
    assert first["version"] == 1 and first["current"] is True

    second = await client.post(
        base + f"/records/{record['id']}/files/license",
        headers=author,
        files={"file": ("license-2.pdf", b"second version", "application/pdf")},
    )
    assert second.status_code == 200, second.text
    assert second.json()["version"] == 2

    files = await client.get(base + f"/records/{record['id']}/files", headers=author)
    assert files.status_code == 200
    assert [item["version"] for item in files.json()["files"]] == [2, 1]

    downloaded = await client.get(
        base + f"/records/{record['id']}/files/{first['id']}/download", headers=author
    )
    assert downloaded.status_code == 200
    assert downloaded.content == b"first version"


async def test_filters_sort_can_write_handover_and_selectors(env_with_provider) -> None:
    client, _server, admin_auth = env_with_provider
    author = await create_user(client, admin_auth, username="handover_author")
    colleague = await create_user(client, admin_auth, username="handover_peer")
    author_id = await resolve_user_id(client, admin_auth, "handover_author")
    colleague_id = await resolve_user_id(client, admin_auth, "handover_peer")
    await _create_feature(client, author)
    base = f"/api/agents/{AGENT_ID}/feature-data"

    # Draft feature: a record created now stays private and out of reach.
    schema = _schema()
    schema["objects"][0]["fields"].append({"key": "weight", "type": "number", "label": "Weight"})
    saved = await client.put(base + "/schema", headers=author, json={"schema": schema})
    assert saved.status_code == 200, saved.text
    draft = await client.post(
        base + "/supplier/records",
        headers=author,
        json={"values": {"name": "DraftOnly", "weight": 1}},
    )
    assert draft.status_code == 200, draft.text
    assert draft.json()["visibility"] == "private"
    assert draft.json()["responsible_user_id"] == author_id
    await _activate_workflow(client, author)
    first = await client.post(
        base + "/supplier/records",
        headers=author,
        json={"values": {"name": "Acme", "weight": 100}},
    )
    second = await client.post(
        base + "/supplier/records",
        headers=author,
        json={"values": {"name": "Borealis", "weight": 200}},
    )
    assert first.status_code == 200 and second.status_code == 200
    assert first.json()["can_write"] is True

    # The feature is still a draft (its enterprise binding happens at boot
    # reconciliation), so an administrator files the per-user grant, which
    # applies without approval: the colleague may now reach the feature, sees
    # the two shared records and can never see the draft.
    grant = await client.post(
        "/api/sharing/acl/feature/data-test",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [{"grantee_type": "user", "grantee_id": str(colleague_id)}],
        },
    )
    assert grant.status_code == 200, grant.text
    listed = await client.get(base + "/supplier/records", headers=colleague)
    assert listed.status_code == 200
    visible_ids = {item["id"] for item in listed.json()["records"]}
    assert visible_ids == {first.json()["id"], second.json()["id"]}
    assert all(item["can_write"] is False for item in listed.json()["records"])
    denied = await client.get(base + f"/records/{draft.json()['id']}", headers=colleague)
    assert denied.status_code == 403

    # AND filters and sorting are validated against the schema; totals survive.
    winnowed = await client.get(
        base + "/supplier/records",
        headers=author,
        params={
            "filters": json.dumps([{"field": "weight", "op": "gte", "value": 150}]),
            "sort_by": "weight",
            "sort_order": "desc",
        },
    )
    assert winnowed.status_code == 200, winnowed.text
    assert winnowed.json()["total"] == 1
    assert winnowed.json()["records"][0]["values"]["name"] == "Borealis"
    bad = await client.get(
        base + "/supplier/records",
        headers=author,
        params={"filters": json.dumps([{"field": "weight", "op": "contains", "value": 1}])},
    )
    assert bad.status_code == 400

    # The selector lists reference options through the same visibility rule.
    options = await client.get(
        base + "/references/records",
        headers=colleague,
        params={"object_key": "supplier"},
    )
    assert options.status_code == 200
    assert {item["id"] for item in options.json()["records"]} == visible_ids

    # Principals feed the pickers: both feature readers are listed, active only.
    principals = await client.get(base + "/principals", headers=author)
    assert principals.status_code == 200, principals.text
    principal_ids = {item["id"] for item in principals.json()["principals"]}
    assert {author_id, colleague_id} <= principal_ids

    # Handover is manager-only and never touches private drafts.
    refused = await client.post(
        base + "/handover",
        headers=colleague,
        json={"from_user_id": author_id, "to_user_id": colleague_id},
    )
    assert refused.status_code == 403
    moved = await client.post(
        base + "/handover",
        headers=admin_auth,
        json={"from_user_id": author_id, "to_user_id": colleague_id},
    )
    assert moved.status_code == 200, moved.text
    assert moved.json()["transferred_count"] == 2
    assert set(moved.json()["record_ids"]) == visible_ids

    # Publication transfers management to the enterprise: the original author
    # loses record-write authority after handing over responsibility.
    after = await client.get(base + f"/records/{first.json()['id']}", headers=colleague)
    assert after.status_code == 200
    assert after.json()["responsible_user_id"] == colleague_id
    assert after.json()["can_write"] is True
    assert after.json()["created_by"] == author_id
    creator_view = await client.get(base + f"/records/{first.json()['id']}", headers=author)
    assert creator_view.json()["can_write"] is False
    refused_patch = await client.patch(
        base + f"/records/{first.json()['id']}",
        headers=author,
        json={"values": {"name": "Not allowed"}, "expected_revision": after.json()["revision"]},
    )
    assert refused_patch.status_code == 403
    assignee_patch = await client.patch(
        base + f"/records/{first.json()['id']}",
        headers=colleague,
        json={"values": {"name": "Acme Prime"}, "expected_revision": after.json()["revision"]},
    )
    assert assignee_patch.status_code == 200, assignee_patch.text
    history = await client.get(base + f"/records/{first.json()['id']}/history", headers=colleague)
    kinds = [item["kind"] for item in history.json()["changes"]]
    assert "handover" in kinds
    handover_change = next(item for item in history.json()["changes"] if item["kind"] == "handover")
    assert handover_change["user_id"] != author_id

    # The private draft was excluded from the transfer entirely.
    still_private = await client.get(base + f"/records/{draft.json()['id']}", headers=author)
    assert still_private.status_code == 200
    assert still_private.json()["responsible_user_id"] == author_id
    assert still_private.json()["can_write"] is True

    # A Chinese attachment filename survives the download header (RFC 5987).
    uploaded = await client.post(
        base + f"/records/{draft.json()['id']}/files/license",
        headers=author,
        files={"file": ("许可证书.pdf", b"cert", "application/pdf")},
    )
    assert uploaded.status_code == 200, uploaded.text
    file_download = await client.get(
        base + f"/records/{draft.json()['id']}/files/{uploaded.json()['id']}/download",
        headers=author,
    )
    assert file_download.status_code == 200
    assert "filename*=UTF-8''" in file_download.headers["content-disposition"]
