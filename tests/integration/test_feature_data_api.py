"""Feature business data over HTTP: schema, records, history and attachments."""

from __future__ import annotations

import json
from pathlib import Path

from tests.support.auth import create_user

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
