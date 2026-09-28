"""A feature's workflow over HTTP — who writes it, and what a refusal carries.

The definition is a document in the feature's own workspace
(``.octop/workflow.json``), written through ``PUT /api/agents/{id}/workflow``. What
this pins is the part the definition module cannot: only a feature has a workflow,
its author writes it while a grantee only reads it, a refusal lists every problem
and writes nothing, and a draft may be half-written where an active one may not.
"""

from __future__ import annotations

import json

import pytest

from octop.infra.agents import feature_workflow as wf
from octop.infra.users.permissions import BASELINE_PERMISSIONS
from tests.support.auth import TEST_ORG_UNIT, create_user, resolve_user_id


@pytest.fixture
async def env(env_with_provider):
    yield env_with_provider


def _create_feature(client, auth, *, feature_id: str = "quote-helper", name: str = "报价助手"):
    return client.post(
        "/api/features",
        headers=auth,
        json={"feature_id": feature_id, "name": name},
    )


def _draft(**overrides):
    """A half-written definition: no step ids, no prompts — legal as a draft."""
    document = {
        "version": 1,
        "status": "draft",
        "inputs": {
            "type": "object",
            "required": ["customer_name"],
            "properties": {
                "customer_name": {"type": "string", "title": {"zh": "客户名称", "en": "Customer"}}
            },
        },
        "steps": [{"name": "提取要点"}, {"name": "起草", "gate": "confirm"}],
        "outputs": [{"name": "报价单", "form": "markdown"}],
        "rules": ["金额逐行核对"],
    }
    document.update(overrides)
    return document


async def _stored_definition(server, agent_id: str):
    workspace = server.app_runtime.agent_registry.workspace_for_agent(agent_id)
    assert workspace is not None
    text = await workspace.aread_text(".octop/workflow.json")
    return json.loads(text) if text else None


async def test_author_writes_a_draft_and_reads_it_back(env) -> None:
    client, srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_author")
    assert (await _create_feature(client, author_auth)).status_code == 201

    path = "/api/agents/feat-quote-helper/workflow"
    empty = await client.get(path, headers=author_auth)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"workflow": None, "error": None}

    written = await client.put(path, headers=author_auth, json={"workflow": _draft()})
    assert written.status_code == 200, written.text
    assert written.json()["workflow"]["steps"][1]["gate"] == "confirm"

    read = await client.get(path, headers=author_auth)
    assert read.status_code == 200
    assert read.json() == {"workflow": _draft(), "error": None}
    # The definition is a workspace document, so it travels with the agent.
    assert await _stored_definition(srv, "feat-quote-helper") == _draft()


async def test_enterprise_admin_can_inspect_unshared_drafts_only_in_own_enterprise(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="draft_local")
    enterprise_auth = await create_user(
        client,
        admin_auth,
        username="draft_enterprise",
        role="enterprise_admin",
        org_unit=TEST_ORG_UNIT,
    )
    foreign_root = "foreign-enterprise"
    created_root = await client.post(
        "/api/org-units",
        headers=admin_auth,
        json={"key": foreign_root, "label_zh": "外部企业", "label_en": "Foreign Enterprise"},
    )
    assert created_root.status_code == 201, created_root.text
    foreign_author = await create_user(
        client, admin_auth, username="draft_foreign", org_unit=foreign_root
    )

    assert (await _create_feature(client, author_auth)).status_code == 201
    assert (
        await _create_feature(client, foreign_author, feature_id="foreign-helper", name="外部助手")
    ).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"
    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200

    listed = await client.get("/api/agents", headers=enterprise_auth)
    assert listed.status_code == 200, listed.text
    feature_rows = {row["agent_id"]: row for row in listed.json() if row["kind"] == "feature"}
    assert set(feature_rows) == {"feat-quote-helper"}
    assert feature_rows["feat-quote-helper"]["can_manage"] is False
    assert (await client.get(path, headers=enterprise_auth)).json()["workflow"] == _draft()
    assert (
        await client.put(path, headers=enterprise_auth, json={"workflow": _draft()})
    ).status_code == 403
    assert (
        await client.get("/api/agents/feat-foreign-helper/workflow", headers=enterprise_auth)
    ).status_code == 403


async def test_publication_transfers_feature_to_enterprise_role_and_survives_author_deletion(
    env,
) -> None:
    client, srv, admin_auth = env
    author_auth = await create_user(
        client,
        admin_auth,
        username="published_author",
        permissions=sorted(BASELINE_PERMISSIONS | {"users"}),
    )
    manager_auth = await create_user(
        client,
        admin_auth,
        username="published_manager",
        role="enterprise_admin",
        org_unit=TEST_ORG_UNIT,
    )
    second_manager_auth = await create_user(
        client,
        admin_auth,
        username="published_second",
        role="enterprise_admin",
        org_unit=TEST_ORG_UNIT,
    )
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"
    published = _draft(
        status="active",
        steps=[{"id": "extract", "name": "提取要点", "prompt": "提取客户要点"}],
    )
    activated = await client.put(path, headers=author_auth, json={"workflow": published})
    assert activated.status_code == 200, activated.text
    row = srv.services.agent_repo.get("feat-quote-helper")
    assert row is not None and row.user_id is None
    assert row.enterprise_unit_key == TEST_ORG_UNIT

    for auth, can_manage in (
        (author_auth, False),
        (manager_auth, True),
        (second_manager_auth, True),
    ):
        listed = await client.get("/api/agents", headers=auth)
        assert listed.status_code == 200, listed.text
        feature = next(item for item in listed.json() if item["agent_id"] == "feat-quote-helper")
        assert feature["can_manage"] is can_manage
    assert (
        await client.patch(
            "/api/agents/feat-quote-helper",
            headers=author_auth,
            json={"description": "unauthorized"},
        )
    ).status_code == 403
    sharing_path = "/api/sharing/acl/feature/quote-helper"
    grant = {"visibility": "private", "grants": []}
    assert (await client.post(sharing_path, headers=author_auth, json=grant)).status_code == 403
    changed = await client.post(sharing_path, headers=manager_auth, json=grant)
    assert changed.status_code == 200, changed.text
    rollback_path = f"/api/sharing/changes/{changed.json()['change_id']}/rollback"
    assert (await client.post(rollback_path, headers=author_auth)).status_code == 403
    assert (await client.post(rollback_path, headers=second_manager_auth)).status_code == 200

    assert (
        await client.put(path, headers=author_auth, json={"workflow": published})
    ).status_code == 403
    assert (
        await client.patch(
            "/api/agents/feat-quote-helper",
            headers=manager_auth,
            json={"description": "enterprise-owned"},
        )
    ).status_code == 200
    assert (
        await client.put(path, headers=second_manager_auth, json={"workflow": published})
    ).status_code == 200

    author_id = await resolve_user_id(client, admin_auth, "published_author")
    removed = await client.delete(f"/api/users/{author_id}", headers=admin_auth)
    assert removed.status_code == 204, removed.text
    retained = srv.services.agent_repo.get("feat-quote-helper")
    assert retained is not None and retained.user_id is None
    assert (await client.get(path, headers=manager_auth)).json()["workflow"] == published
    assert (
        await client.get("/api/agents/feat-quote-helper", headers=second_manager_auth)
    ).status_code == 200
    stopped = await client.post("/api/agents/feat-quote-helper/stop", headers=manager_auth)
    assert stopped.status_code == 204, stopped.text
    restarted = await client.post(
        "/api/agents/feat-quote-helper/start", headers=second_manager_auth
    )
    assert restarted.status_code == 204, restarted.text

    reset = await client.put(path, headers=manager_auth, json={"workflow": _draft()})
    assert reset.status_code == 200, reset.text
    memory_path = "/api/agents/feat-quote-helper/memory"
    access = await client.get(f"{memory_path}/access", headers=second_manager_auth)
    assert access.status_code == 200, access.text
    assert access.json()["stage"] == "draft"
    assert access.json()["shared_writable"] is False
    shared_write = await client.post(
        f"{memory_path}/atoms",
        headers=manager_auth,
        json={"assertion": "cannot retrain published shared memory", "entity_name": "feature"},
    )
    assert shared_write.status_code == 403


async def test_existing_active_feature_is_adopted_before_author_can_be_deleted(env) -> None:
    client, srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="legacy_active_author")
    manager_auth = await create_user(
        client,
        admin_auth,
        username="legacy_active_manager",
        role="enterprise_admin",
        org_unit=TEST_ORG_UNIT,
    )
    assert (await _create_feature(client, author_auth)).status_code == 201
    registry = srv.app_runtime.agent_registry
    workspace = registry.workspace_for_agent("feat-quote-helper")
    assert workspace is not None
    definition = _draft(
        status="active",
        steps=[{"id": "extract", "name": "提取要点", "prompt": "提取要点"}],
    )
    await wf.save_workflow(workspace, definition)  # Simulates a pre-transfer published document.
    author_id = await resolve_user_id(client, admin_auth, "legacy_active_author")
    assert srv.services.agent_repo.get("feat-quote-helper").user_id == author_id

    await registry._claim_existing_active_features()
    adopted = srv.services.agent_repo.get("feat-quote-helper")
    assert adopted is not None and adopted.user_id is None
    assert adopted.enterprise_unit_key == TEST_ORG_UNIT
    assert (await client.delete(f"/api/users/{author_id}", headers=admin_auth)).status_code == 204
    assert (
        await client.get("/api/agents/feat-quote-helper", headers=manager_auth)
    ).status_code == 200
    assert (
        await client.get("/api/agents/feat-quote-helper/workflow", headers=manager_auth)
    ).json()["workflow"] == definition


async def test_an_active_definition_must_stand_on_its_own(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_activate")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"

    draft = await client.put(path, headers=author_auth, json={"workflow": _draft()})
    assert draft.status_code == 200

    # The same document, declared published: no ids, no prompts — refused.
    refused = await client.put(
        path, headers=author_auth, json={"workflow": _draft(status="active")}
    )
    assert refused.status_code == 400, refused.text
    body = refused.json()["error"]
    assert body["code"] == "WORKFLOW_INVALID"
    assert "id is required" in body["message"]
    assert "prompt is required" in body["message"]

    # Nothing was written: the draft is still what the definition says.
    still = await client.get(path, headers=author_auth)
    assert still.json()["workflow"] == _draft()


async def test_a_refusal_names_every_problem_at_once(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_problems")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"

    broken = _draft(
        version=2,
        outputs=[{"name": "报价单", "form": "pdf"}],
        rules=[""],
    )
    response = await client.put(path, headers=author_auth, json={"workflow": broken})
    assert response.status_code == 400, response.text
    message = response.json()["error"]["message"]
    for expected in ("version must be 1", "form must be one of", "rules[0]"):
        assert expected in message, message
    assert (await client.get(path, headers=author_auth)).json()["workflow"] is None


async def test_only_a_features_own_agent_has_a_workflow(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_expert")
    created = await client.post("/api/agents", headers=author_auth, json={"name": "my-expert"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    path = f"/api/agents/{agent_id}/workflow"
    read = await client.get(path, headers=author_auth)
    assert read.status_code == 400, read.text
    assert read.json()["error"]["code"] == "WORKFLOW_NOT_A_FEATURE"

    written = await client.put(path, headers=author_auth, json={"workflow": _draft()})
    assert written.status_code == 400
    assert written.json()["error"]["code"] == "WORKFLOW_NOT_A_FEATURE"


async def test_a_grantee_sees_only_an_activated_definition_and_cannot_write_it(env) -> None:
    """A shared draft is for its author to train, not a published run contract."""
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_grant_author")
    caller_auth = await create_user(client, admin_auth, username="wf_grant_caller")
    caller_id = await resolve_user_id(client, admin_auth, "wf_grant_caller")
    assert (await _create_feature(client, author_auth)).status_code == 201

    path = "/api/agents/feat-quote-helper/workflow"
    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200

    # Before the grant the feature reaches nobody but its author.
    assert (await client.get(path, headers=caller_auth)).status_code == 403

    granted = await client.post(
        "/api/sharing/acl/feature/quote-helper",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [{"grantee_type": "user", "grantee_id": str(caller_id)}],
        },
    )
    assert granted.status_code == 200, granted.text

    read = await client.get(path, headers=caller_auth)
    assert read.status_code == 200, read.text
    assert read.json()["workflow"] is None
    change = await client.post(
        f"{path}/changes",
        headers=author_auth,
        json={
            "target": "definition",
            "summary": "Training note",
            "items": [{"path": "/rules/-", "before": None, "after": "Unpublished detail"}],
        },
    )
    assert change.status_code == 200, change.text
    assert (await client.get(f"{path}/changes", headers=caller_auth)).json() == {"changes": []}
    change_path = f"{path}/changes/{change.json()['id']}/revert"
    assert (await client.post(change_path, headers=author_auth)).status_code == 200
    # Even an already-reverted diff still contains the author's unpublished text.
    refused = await client.post(change_path, headers=caller_auth)
    assert refused.status_code == 403, refused.text

    active = _draft(
        status="active",
        steps=[
            {"id": "extract", "name": "提取要点", "prompt": "提取客户要点"},
            {"id": "write", "name": "起草", "prompt": "写报价单", "gate": "confirm"},
        ],
    )
    assert (
        await client.put(path, headers=author_auth, json={"workflow": active})
    ).status_code == 200
    published = await client.get(path, headers=caller_auth)
    assert published.json()["workflow"] == active
    assert (await client.get(f"{path}/changes", headers=caller_auth)).json() == {"changes": []}

    written = await client.put(
        path, headers=caller_auth, json={"workflow": _draft(status="active")}
    )
    assert written.status_code == 403, written.text


async def test_writing_null_removes_the_definition(env) -> None:
    client, srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_remove")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"

    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200
    removed = await client.put(path, headers=author_auth, json={"workflow": None})
    assert removed.status_code == 200
    assert removed.json() == {"workflow": None, "error": None}
    assert await _stored_definition(srv, "feat-quote-helper") is None


async def test_a_caller_keeps_their_own_overlay_above_the_definition(env) -> None:
    """The overlay is the caller's own text: theirs to write, nobody else's to read."""
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_overlay_author")
    first_auth = await create_user(client, admin_auth, username="wf_overlay_first")
    second_auth = await create_user(client, admin_auth, username="wf_overlay_second")
    first_id = await resolve_user_id(client, admin_auth, "wf_overlay_first")
    second_id = await resolve_user_id(client, admin_auth, "wf_overlay_second")
    assert (await _create_feature(client, author_auth)).status_code == 201

    overlay_path = "/api/agents/feat-quote-helper/workflow/overlay"
    # One entry, both callers: writing it twice would replace the first grant.
    granted = await client.post(
        "/api/sharing/acl/feature/quote-helper",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [
                {"grantee_type": "user", "grantee_id": str(first_id)},
                {"grantee_type": "user", "grantee_id": str(second_id)},
            ],
        },
    )
    assert granted.status_code == 200, granted.text
    for auth in (first_auth, second_auth):
        assert (await client.get(overlay_path, headers=auth)).json() == {"overlay": None}

    written = await client.put(
        overlay_path, headers=first_auth, json={"overlay": "我们的报价含税，不要写不含税价。"}
    )
    assert written.status_code == 200, written.text
    assert written.json() == {"overlay": "我们的报价含税，不要写不含税价。"}

    # Each caller reads their own; the first one's text is not the second's to see.
    assert (await client.get(overlay_path, headers=first_auth)).json() == {
        "overlay": "我们的报价含税，不要写不含税价。"
    }
    assert (await client.get(overlay_path, headers=second_auth)).json() == {"overlay": None}

    # Blank removes it — saying nothing is not the same as saying an empty line.
    cleared = await client.put(overlay_path, headers=first_auth, json={"overlay": "   "})
    assert cleared.status_code == 200
    assert cleared.json() == {"overlay": None}
    assert (await client.get(overlay_path, headers=first_auth)).json() == {"overlay": None}


async def test_publishing_a_definition_that_names_a_missing_skill_is_refused(env) -> None:
    """A draft may name what the author is about to install; publishing may not."""
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_publish_refs")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"

    draft = _draft()
    draft["steps"] = [
        {"id": "extract", "name": "提取", "prompt": "读附件", "skills": ["excel"]},
    ]
    assert (
        await client.put(path, headers=author_auth, json={"workflow": draft})
    ).status_code == 200

    refused = await client.put(
        path, headers=author_auth, json={"workflow": {**draft, "status": "active"}}
    )
    assert refused.status_code == 400, refused.text
    body = refused.json()["error"]
    assert body["code"] == "WORKFLOW_INVALID"
    assert "skills names unknown skill(s): excel" in body["message"]
    # Nothing was written: the draft is still the stored definition.
    assert (await client.get(path, headers=author_auth)).json()["workflow"]["status"] == "draft"


async def test_a_change_is_applied_recorded_and_revertible(env) -> None:
    """The whole promise: it lands, it is visible, and undo puts it back."""
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_change_author")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"
    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200

    applied = await client.post(
        f"{path}/changes",
        headers=author_auth,
        json={
            "target": "definition",
            "summary": "新增 1 条规则",
            "items": [{"path": "/rules/-", "before": None, "after": "不得编造交期"}],
        },
    )
    assert applied.status_code == 200, applied.text
    change = applied.json()
    assert change["status"] == "applied"
    assert change["items"] == [{"path": "/rules/1", "before": None, "after": "不得编造交期"}]

    stored = await client.get(path, headers=author_auth)
    assert stored.json()["workflow"]["rules"] == ["金额逐行核对", "不得编造交期"]

    listed = await client.get(f"{path}/changes", headers=author_auth)
    assert [row["id"] for row in listed.json()["changes"]] == [change["id"]]

    reverted = await client.post(f"{path}/changes/{change['id']}/revert", headers=author_auth)
    assert reverted.status_code == 200, reverted.text
    assert reverted.json()["status"] == "reverted"

    after = await client.get(path, headers=author_auth)
    assert after.json()["workflow"]["rules"] == ["金额逐行核对"]

    # Reverting twice is a no-op, not an error: the change is already undone.
    again = await client.post(f"{path}/changes/{change['id']}/revert", headers=author_auth)
    assert again.status_code == 200
    assert again.json()["status"] == "reverted"


async def test_a_stale_change_is_refused_whole(env) -> None:
    """An edit made after the change was prepared is never overwritten."""
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_change_stale")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"
    changes = f"{path}/changes"
    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200

    assert (
        await client.post(
            changes,
            headers=author_auth,
            json={
                "target": "definition",
                "summary": "先加一条",
                "items": [{"path": "/rules/-", "before": None, "after": "先加的"}],
            },
        )
    ).status_code == 200

    # The author edits that same rule by hand afterwards.
    edited = _draft(rules=["金额逐行核对", "手工改过的", "手工加的"])
    assert (
        await client.put(path, headers=author_auth, json={"workflow": edited})
    ).status_code == 200

    stale = await client.post(
        changes,
        headers=author_auth,
        json={
            "target": "definition",
            "summary": "基于旧值再改",
            "items": [{"path": "/rules/1", "before": "先加的", "after": "改过的"}],
        },
    )
    assert stale.status_code == 409, stale.text
    body = stale.json()["error"]
    assert body["code"] == "WORKFLOW_CHANGE_CONFLICT"
    assert "/rules/1" in body["message"]

    # Nothing was written: the hand edit still stands.
    assert (await client.get(path, headers=author_auth)).json()["workflow"]["rules"] == [
        "金额逐行核对",
        "手工改过的",
        "手工加的",
    ]


async def test_a_change_cannot_leave_the_definition_invalid(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_change_invalid")
    assert (await _create_feature(client, author_auth)).status_code == 201
    path = "/api/agents/feat-quote-helper/workflow"
    assert (
        await client.put(path, headers=author_auth, json={"workflow": _draft()})
    ).status_code == 200

    refused = await client.post(
        f"{path}/changes",
        headers=author_auth,
        json={
            "target": "definition",
            "summary": "把状态改成一个不存在的值",
            "items": [{"path": "/status", "before": "draft", "after": "live"}],
        },
    )

    assert refused.status_code == 400, refused.text
    assert refused.json()["error"]["code"] == "WORKFLOW_INVALID"
    assert (await client.get(path, headers=author_auth)).json()["workflow"]["status"] == "draft"
    assert (await client.get(f"{path}/changes", headers=author_auth)).json() == {"changes": []}


async def test_an_overlay_change_belongs_to_its_own_author(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_change_overlay_author")
    caller_auth = await create_user(client, admin_auth, username="wf_change_overlay_caller")
    caller_id = await resolve_user_id(client, admin_auth, "wf_change_overlay_caller")
    assert (await _create_feature(client, author_auth)).status_code == 201
    granted = await client.post(
        "/api/sharing/acl/feature/quote-helper",
        headers=admin_auth,
        json={
            "visibility": "private",
            "grants": [{"grantee_type": "user", "grantee_id": str(caller_id)}],
        },
    )
    assert granted.status_code == 200, granted.text
    changes = "/api/agents/feat-quote-helper/workflow/changes"

    applied = await client.post(
        changes,
        headers=caller_auth,
        json={
            "target": "overlay",
            "summary": "我的报价都含税",
            "items": [{"path": "/overlay", "before": None, "after": "我们含税"}],
        },
    )
    assert applied.status_code == 200, applied.text

    overlay = await client.get(
        "/api/agents/feat-quote-helper/workflow/overlay", headers=caller_auth
    )
    assert overlay.json() == {"overlay": "我们含税"}

    # The caller sees their own change; the feature's author does not.
    assert [
        row["id"] for row in (await client.get(changes, headers=caller_auth)).json()["changes"]
    ] == [applied.json()["id"]]
    assert (await client.get(changes, headers=author_auth)).json() == {"changes": []}

    # The author cannot undo somebody else's overlay change — asked before it is
    # undone, because an already-reverted change answers idempotently to anybody.
    refused = await client.post(f"{changes}/{applied.json()['id']}/revert", headers=author_auth)
    assert refused.status_code in (403, 404), refused.text

    reverted = await client.post(f"{changes}/{applied.json()['id']}/revert", headers=caller_auth)
    assert reverted.status_code == 200, reverted.text

    # Undoing an overlay change puts the caller's own text back to nothing.
    assert (
        await client.get("/api/agents/feat-quote-helper/workflow/overlay", headers=caller_auth)
    ).json() == {"overlay": None}


async def test_runs_list_what_this_caller_submitted(env) -> None:
    """A run is evidence of one caller's submission — theirs to read back."""
    client, srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_runs_author")
    author_id = await resolve_user_id(client, admin_auth, "wf_runs_author")
    assert (await _create_feature(client, author_auth)).status_code == 201

    empty = await client.get("/api/agents/feat-quote-helper/workflow/runs", headers=author_auth)
    assert empty.status_code == 200, empty.text
    assert empty.json() == {"runs": []}

    srv.services.feature_run_repo.record(
        feature_id="quote-helper",
        agent_id="feat-quote-helper",
        user_id=author_id,
        thread_id="thr-1",
        inputs={"customer_name": "ACME"},
        definition=_draft(),
    )

    listed = await client.get("/api/agents/feat-quote-helper/workflow/runs", headers=author_auth)
    assert listed.status_code == 200, listed.text
    runs = listed.json()["runs"]
    assert len(runs) == 1
    assert runs[0]["inputs"] == {"customer_name": "ACME"}
    assert runs[0]["thread_id"] == "thr-1"
    assert runs[0]["id"]


async def test_runs_are_refused_on_an_expert(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_runs_expert")
    created = await client.post("/api/agents", headers=author_auth, json={"name": "my-expert"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    response = await client.get(f"/api/agents/{agent_id}/workflow/runs", headers=author_auth)
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "WORKFLOW_NOT_A_FEATURE"


async def test_an_overlay_cannot_be_kept_on_an_expert(env) -> None:
    client, _srv, admin_auth = env
    author_auth = await create_user(client, admin_auth, username="wf_overlay_expert")
    created = await client.post("/api/agents", headers=author_auth, json={"name": "my-expert"})
    assert created.status_code == 201, created.text
    agent_id = created.json()["agent_id"]

    response = await client.put(
        f"/api/agents/{agent_id}/workflow/overlay", headers=author_auth, json={"overlay": "hi"}
    )
    assert response.status_code == 400, response.text
    assert response.json()["error"]["code"] == "WORKFLOW_NOT_A_FEATURE"
