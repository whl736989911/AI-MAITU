"""Unit tests: extraction templates over HTTP (design §7).

The gating is the knowledge line's usual one while the fine-grained permission
keys are still with the permissions workstream: reading needs
``knowledge_bases``, managing needs ``knowledge_settings``.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from tests.support.app import octop_client
from tests.support.auth import auth_header, bootstrap_admin, create_user

_FIELDS = [
    {"name": "summary", "type": "text", "required": True, "instruction": "不超过300字"},
    {"name": "keywords", "type": "string[]", "required": True},
]


@pytest.fixture
async def api(tmp_path: Path) -> AsyncIterator[dict[str, Any]]:
    """A bootstrapped server, an admin, a member, and one folder source."""
    async with octop_client(tmp_path) as (client, srv):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        member_auth = await create_user(
            client, admin_auth, username="reader", permissions=["knowledge_bases"]
        )
        assert srv.services is not None
        kb = srv.services.knowledge_repo.get_enterprise_space().id
        share = tmp_path / "share"
        share.mkdir(exist_ok=True)
        created = await client.post(
            f"/api/knowledge-bases/{kb}/data-sources",
            headers=admin_auth,
            json={"name": "Share", "kind": "local", "folder": {"root_path": str(share)}},
        )
        assert created.status_code == 201, created.text
        yield {
            "client": client,
            "admin": admin_auth,
            "member": member_auth,
            "source": created.json()["data_source_id"],
        }


async def _create_template(
    client: httpx.AsyncClient, headers: dict[str, str], **overrides: Any
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "name": "合同信息提取模板",
        "fields": _FIELDS,
        "instruction": "重点关注合同主体、期限、金额、付款和违约责任。",
        "applies_to": "doc, docx, pdf",
    }
    body.update(overrides)
    created = await client.post("/api/extract-templates", headers=headers, json=body)
    assert created.status_code == 201, created.text
    return created.json()


async def test_creating_a_template_returns_its_first_version(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    created = await _create_template(client, api["admin"])

    assert created["template_id"]
    assert created["current_version"] == 1
    assert created["status"] == "active"
    assert [field["name"] for field in created["fields"]] == ["summary", "keywords"]
    assert created["bindings"] == 0

    listed = await client.get("/api/extract-templates", headers=api["admin"])
    assert [row["template_id"] for row in listed.json()] == [created["template_id"]]


async def test_editing_adds_a_version_and_keeps_the_first(api: dict[str, Any]) -> None:
    """design §7.3: the edit is a new version, and the first one survives."""
    client: httpx.AsyncClient = api["client"]
    created = await _create_template(client, api["admin"])

    edited = await client.post(
        f"/api/extract-templates/{created['template_id']}/versions",
        headers=api["admin"],
        json={
            "fields": [*_FIELDS, {"name": "amount", "type": "amount"}],
            "instruction": "也提取金额。",
            "applies_to": "pdf",
            "note": "加了金额字段",
        },
    )

    assert edited.status_code == 201, edited.text
    assert edited.json()["version"] == 2
    versions = await client.get(
        f"/api/extract-templates/{created['template_id']}/versions", headers=api["admin"]
    )
    assert [row["version"] for row in versions.json()] == [2, 1]
    assert [field["name"] for field in versions.json()[1]["fields"]] == ["summary", "keywords"]


async def test_a_member_reads_but_does_not_manage(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]
    created = await _create_template(client, api["admin"])

    assert (await client.get("/api/extract-templates", headers=api["member"])).status_code == 200
    refused = await client.post(
        "/api/extract-templates", headers=api["member"], json={"name": "偷偷建的"}
    )
    assert refused.status_code == 403, refused.text
    assert (
        await client.patch(
            f"/api/extract-templates/{created['template_id']}",
            headers=api["member"],
            json={"status": "disabled"},
        )
    ).status_code == 403
    assert (
        await client.delete(
            f"/api/extract-templates/{created['template_id']}", headers=api["member"]
        )
    ).status_code == 403


async def test_a_bound_template_refuses_deletion_until_it_is_unbound(api: dict[str, Any]) -> None:
    """design §7.2: a used template is disabled, not removed behind its bindings."""
    client: httpx.AsyncClient = api["client"]
    created = await _create_template(client, api["admin"])
    bound = await client.post(
        f"/api/extract-templates/{created['template_id']}/bindings",
        headers=api["admin"],
        json={"data_source_id": api["source"], "path": "legal"},
    )
    assert bound.status_code == 201, bound.text

    refused = await client.delete(
        f"/api/extract-templates/{created['template_id']}", headers=api["admin"]
    )

    assert refused.status_code == 409, refused.text
    body = refused.json()["error"]
    assert body["code"] == "EXTRACT_TEMPLATE_IN_USE"
    assert body["details"]["bindings"] == 1

    assert (
        await client.delete(
            f"/api/extract-templates/bindings/{bound.json()['binding_id']}",
            headers=api["admin"],
        )
    ).status_code == 204
    assert (
        await client.delete(
            f"/api/extract-templates/{created['template_id']}", headers=api["admin"]
        )
    ).status_code == 204


async def test_a_template_that_cannot_be_carried_out_is_refused(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    refused = await client.post(
        "/api/extract-templates",
        headers=api["admin"],
        json={"name": "坏模板", "fields": [{"name": "a"}, {"name": "a"}]},
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "EXTRACT_TEMPLATE_INVALID"
    assert "duplicate" in refused.json()["error"]["details"]["reason"]


async def test_a_disabled_template_cannot_be_bound(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]
    created = await _create_template(client, api["admin"])
    await client.patch(
        f"/api/extract-templates/{created['template_id']}",
        headers=api["admin"],
        json={"status": "disabled"},
    )

    refused = await client.post(
        f"/api/extract-templates/{created['template_id']}/bindings",
        headers=api["admin"],
        json={"data_source_id": api["source"]},
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "EXTRACT_TEMPLATE_INVALID"


async def test_binding_refuses_a_source_with_no_folder(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]
    created = await _create_template(client, api["admin"])

    refused = await client.post(
        f"/api/extract-templates/{created['template_id']}/bindings",
        headers=api["admin"],
        json={"data_source_id": "nosuchsource"},
    )

    assert refused.status_code == 404, refused.text


async def test_resolve_picks_the_most_specific_binding(api: dict[str, Any]) -> None:
    """design §7.4's precedence, through the API that reports it."""
    client: httpx.AsyncClient = api["client"]
    source_wide = await _create_template(client, api["admin"], name="全部", applies_to="")
    contracts = await _create_template(client, api["admin"], name="合同", applies_to="")
    for template_id, path in (
        (source_wide["template_id"], ""),
        (contracts["template_id"], "legal"),
    ):
        bound = await client.post(
            f"/api/extract-templates/{template_id}/bindings",
            headers=api["admin"],
            json={"data_source_id": api["source"], "path": path},
        )
        assert bound.status_code == 201, bound.text

    inside = await client.get(
        "/api/extract-templates/resolve",
        headers=api["admin"],
        params={"data_source_id": api["source"], "path": "legal/a.pdf"},
    )
    elsewhere = await client.get(
        "/api/extract-templates/resolve",
        headers=api["admin"],
        params={"data_source_id": api["source"], "path": "handbook.md"},
    )

    assert inside.json()["template_id"] == contracts["template_id"]
    assert inside.json()["level"] == "folder"
    assert elsewhere.json()["template_id"] == source_wide["template_id"]
    assert elsewhere.json()["level"] == "source"


async def test_resolve_reports_two_templates_claiming_one_file(api: dict[str, Any]) -> None:
    """design §7.4: a clash is reported, and nothing is chosen."""
    client: httpx.AsyncClient = api["client"]
    first = await _create_template(client, api["admin"], name="甲", applies_to="")
    second = await _create_template(client, api["admin"], name="乙", applies_to="")
    for template_id in (first["template_id"], second["template_id"]):
        await client.post(
            f"/api/extract-templates/{template_id}/bindings",
            headers=api["admin"],
            json={"data_source_id": api["source"]},
        )

    resolved = await client.get(
        "/api/extract-templates/resolve",
        headers=api["admin"],
        params={"data_source_id": api["source"], "path": "handbook.md"},
    )

    body = resolved.json()
    assert body["template_id"] is None
    assert sorted(body["conflicts"]) == sorted([first["template_id"], second["template_id"]])
