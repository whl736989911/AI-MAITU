"""Unit tests: the sharing router — ACL reads, change requests, approvals.

The governance rules belong to ``SharingService``; what is tested here is the
HTTP contract the dashboard codes against: an owner's change lands (or is
parked, with the reason reported back), the approval queue is admin-gated for
the decisions that matter, and a caller cannot reach a resource it does not own.
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
    """A bootstrapped server, a resource-owning member, a peer, and one KB.

    The KB is owned by the member (not the admin), so the owner path is exercised
    for real instead of leaning on the admin bypass inside the service.
    """
    async with octop_client(tmp_path) as (client, srv):
        await bootstrap_admin(client, tmp_path)
        admin_auth = await auth_header(client)
        member_auth = await create_user(
            client, admin_auth, username="sharer", permissions=["users"]
        )
        peer_auth = await create_user(client, admin_auth, username="peer", permissions=["users"])
        assert srv.services is not None
        member_id = int(srv.services.user_repo.get_by_username("sharer").id)
        # ``resource_acl.unit_key`` is a foreign key, so a unit-visibility share
        # needs a unit that really exists.
        srv.services.repos.org_unit_repo.create(key="eng", label_zh="工程", label_en="Engineering")
        base = srv.services.knowledge_repo.create_base(
            owner_user_id=member_id, name="Handbook", shared=False
        )
        yield {
            "client": client,
            "admin": admin_auth,
            "member": member_auth,
            "peer": peer_auth,
            "member_id": member_id,
            "kb": base.id,
        }


def _acl_url(api: dict[str, Any], resource_id: str | None = None) -> str:
    return f"/api/sharing/acl/knowledge_base/{resource_id or api['kb']}"


async def test_owner_change_takes_effect_immediately(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    changed = await client.post(
        _acl_url(api),
        headers=api["member"],
        json={"visibility": "unit", "unit_key": "eng", "reason": "share with the team"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["status"] == "applied"
    assert body["applied"] is True
    assert body["impact_scope"] == "unit"
    assert body["entry"]["visibility"] == "unit"
    assert body["entry"]["unit_key"] == "eng"
    assert body["entry"]["owner_user_id"] == api["member_id"]
    assert body["resource_type"] == "knowledge_base"
    assert body["resource_id"] == api["kb"]

    # The row is in force, not merely logged.
    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.status_code == 200, read.text
    assert read.json()["entry"]["visibility"] == "unit"
    assert read.json()["entry"]["version"] == body["entry"]["version"]

    # Nothing was parked: an immediately applied change never enters the queue.
    queue = await client.get("/api/sharing/changes", headers=api["admin"])
    assert queue.status_code == 200, queue.text
    assert queue.json()["changes"] == []


async def test_widening_to_public_waits_for_an_admin(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    before = await client.get(_acl_url(api), headers=api["member"])
    previous = before.json()["entry"]

    changed = await client.post(
        _acl_url(api),
        headers=api["member"],
        json={"visibility": "public", "reason": "publish the handbook"},
    )
    assert changed.status_code == 200, changed.text
    body = changed.json()
    assert body["status"] == "pending_approval"
    assert body["applied"] is False
    assert body["impact_scope"] == "org"
    # The reply carries the state in force, which is still the old one.
    assert body["entry"]["visibility"] == "private"

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "private"
    assert read.json()["entry"]["version"] == previous["version"]

    queue = await client.get("/api/sharing/changes?status=pending_approval", headers=api["admin"])
    assert queue.status_code == 200, queue.text
    rows = queue.json()["changes"]
    assert [row["change_id"] for row in rows] == [body["change_id"]]
    assert rows[0]["status"] == "pending_approval"
    assert rows[0]["impact_scope"] == "org"
    assert rows[0]["actor_user_id"] == api["member_id"]
    assert rows[0]["reason"] == "publish the handbook"
    # A reviewer must be able to tell what is being approved.
    assert rows[0]["resource_id"] == api["kb"]
    assert rows[0]["resource"]["name"] == "Handbook"
    assert rows[0]["before"]["visibility"] == "private"
    assert rows[0]["after"]["visibility"] == "public"


async def test_a_member_cannot_approve_an_org_wide_change(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    parked = await client.post(_acl_url(api), headers=api["member"], json={"visibility": "public"})
    change_id = parked.json()["change_id"]

    refused = await client.post(f"/api/sharing/changes/{change_id}/approve", headers=api["member"])
    assert refused.status_code == 403, refused.text
    assert refused.json()["error"]["code"] == "FORBIDDEN"

    # Same for the refusal side of the gate, and neither action changed anything.
    rejected = await client.post(
        f"/api/sharing/changes/{change_id}/reject",
        headers=api["member"],
        json={"reason": "no"},
    )
    assert rejected.status_code == 403, rejected.text
    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "private"
    queue = await client.get("/api/sharing/changes", headers=api["admin"])
    assert [row["change_id"] for row in queue.json()["changes"]] == [change_id]


async def test_unknown_resource_type_is_refused(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    read = await client.get("/api/sharing/acl/widget/w1", headers=api["member"])
    assert read.status_code == 404, read.text
    assert read.json()["error"]["code"] == "NOT_FOUND"

    written = await client.post(
        "/api/sharing/acl/widget/w1", headers=api["member"], json={"visibility": "public"}
    )
    assert written.status_code == 404, written.text


async def test_admin_approval_publishes_the_queued_change(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    parked = await client.post(_acl_url(api), headers=api["member"], json={"visibility": "public"})
    change_id = parked.json()["change_id"]

    approved = await client.post(f"/api/sharing/changes/{change_id}/approve", headers=api["admin"])
    assert approved.status_code == 200, approved.text
    body = approved.json()
    assert body["status"] == "applied"
    assert body["impact_scope"] == "org"
    assert body["entry"]["visibility"] == "public"

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "public"

    # The queue is empty again, and the same change now reads as applied.
    pending = await client.get("/api/sharing/changes", headers=api["admin"])
    assert pending.json()["changes"] == []
    applied = await client.get("/api/sharing/changes?status=applied", headers=api["admin"])
    assert change_id in [row["change_id"] for row in applied.json()["changes"]]

    # Approving twice is a conflict, not a second write.
    again = await client.post(f"/api/sharing/changes/{change_id}/approve", headers=api["admin"])
    assert again.status_code == 409, again.text


async def test_admin_rejection_keeps_access_and_records_the_reason(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    parked = await client.post(_acl_url(api), headers=api["member"], json={"visibility": "public"})
    change_id = parked.json()["change_id"]

    rejected = await client.post(
        f"/api/sharing/changes/{change_id}/reject",
        headers=api["admin"],
        json={"reason": "not for everyone"},
    )
    assert rejected.status_code == 200, rejected.text
    body = rejected.json()
    assert body["status"] == "rejected"
    assert body["applied"] is False
    assert body["entry"]["visibility"] == "private"

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "private"
    queue = await client.get("/api/sharing/changes?status=rejected", headers=api["admin"])
    row = next(r for r in queue.json()["changes"] if r["change_id"] == change_id)
    assert row["reason"] == "not for everyone"


async def test_rollback_restores_the_replaced_state(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    applied = await client.post(
        _acl_url(api), headers=api["member"], json={"visibility": "unit", "unit_key": "eng"}
    )
    change_id = applied.json()["change_id"]
    assert applied.json()["entry"]["visibility"] == "unit"

    rolled_back = await client.post(
        f"/api/sharing/changes/{change_id}/rollback", headers=api["member"]
    )
    assert rolled_back.status_code == 200, rolled_back.text
    body = rolled_back.json()
    assert body["status"] == "rolled_back"
    assert body["applied"] is False
    # ``entry`` is the state now in force: the one the rollback restored.
    assert body["entry"]["visibility"] == "private"
    assert body["entry"]["version"] > applied.json()["entry"]["version"]

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "private"


async def test_a_resource_without_an_acl_row_reports_no_entry(api: dict[str, Any]) -> None:
    """An unshared resource is not a 404 — the UI renders “still private” from null."""
    client: httpx.AsyncClient = api["client"]

    read = await client.get(_acl_url(api, "kb_never_shared"), headers=api["member"])
    assert read.status_code == 200, read.text
    assert read.json()["entry"] is None


async def test_a_non_owner_cannot_change_or_roll_back_another_users_resource(
    api: dict[str, Any],
) -> None:
    client: httpx.AsyncClient = api["client"]

    changed = await client.post(
        _acl_url(api), headers=api["peer"], json={"visibility": "unit", "unit_key": "eng"}
    )
    assert changed.status_code == 403, changed.text
    assert changed.json()["error"]["code"] == "FORBIDDEN"

    applied = await client.post(
        _acl_url(api), headers=api["member"], json={"visibility": "unit", "unit_key": "eng"}
    )
    rolled_back = await client.post(
        f"/api/sharing/changes/{applied.json()['change_id']}/rollback", headers=api["peer"]
    )
    assert rolled_back.status_code == 403, rolled_back.text

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "unit"


async def test_a_unit_share_needs_a_unit_that_exists(api: dict[str, Any]) -> None:
    """``unit_key`` is a foreign key: an unknown unit is a 400, never a 500."""
    client: httpx.AsyncClient = api["client"]

    unknown = await client.post(
        _acl_url(api), headers=api["member"], json={"visibility": "unit", "unit_key": "nope"}
    )
    assert unknown.status_code == 400, unknown.text

    read = await client.get(_acl_url(api), headers=api["member"])
    assert read.json()["entry"]["visibility"] == "private"


async def test_the_queue_window_and_filter_are_bounded(api: dict[str, Any]) -> None:
    client: httpx.AsyncClient = api["client"]

    unknown = await client.get("/api/sharing/changes?status=everything", headers=api["admin"])
    assert unknown.status_code == 400, unknown.text

    assert (
        await client.get("/api/sharing/changes?limit=0", headers=api["admin"])
    ).status_code == 422
    assert (
        await client.get("/api/sharing/changes?limit=9999", headers=api["admin"])
    ).status_code == 422

    for index in range(3):
        await client.post(
            _acl_url(api, f"kb_{index}"),
            headers=api["member"],
            json={"visibility": "public", "reason": f"publish {index}"},
        )
    capped = await client.get("/api/sharing/changes?limit=2", headers=api["admin"])
    assert capped.status_code == 200, capped.text
    assert capped.json()["status"] == "pending_approval"
    assert len(capped.json()["changes"]) == 2

    # A member may watch the queue, but only an admin can act on it.
    assert (await client.get("/api/sharing/changes", headers=api["member"])).status_code == 200
