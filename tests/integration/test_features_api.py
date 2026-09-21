"""End-to-end: authoring feature definitions over HTTP.

This is the settings UI's path: a definition is posted, lands in the user's own
feature directory, and the running server serves it without a restart. The
bundled library stays read-only throughout.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from octop.infra.features import default_library_root
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import (
    auth_header,
    bootstrap_admin,
    create_agent,
    create_user,
    ensure_users,
    resolve_user_id,
    seed_openai_provider,
)
from tests.support.fakes import FakeHarnessAgent

BUNDLED_ID = "meeting-notes"
SYSTEM_PROMPT = "Write the summary in three sections."


def _definition(feature_id: str = "weekly-report", **overrides: Any) -> dict[str, Any]:
    """A definition exactly as the settings UI submits it."""
    payload: dict[str, Any] = {
        "id": feature_id,
        "label": {"zh": "周报", "en": "Weekly report"},
        "description": {"zh": "把零散记录整理成周报", "en": "Turn notes into a weekly report"},
        "icon_name": "clipboard-list",
        "unit": "general",
        "input_schema": {
            "type": "object",
            "required": ["notes"],
            "properties": {
                "notes": {
                    "type": "string",
                    "format": "textarea",
                    "title": {"zh": "记录", "en": "Notes"},
                },
            },
        },
        "ui_schema": {"order": ["notes"], "widgets": {"notes": "textarea"}},
        "prompt": {"user_template": "整理成周报：\n{{inputs}}", "system_prompt": SYSTEM_PROMPT},
        "output": {"kind": "markdown"},
        "permissions": {"allow_units": ["*"]},
    }
    payload.update(overrides)
    return payload


def _capability_layer(**overrides: Any) -> dict[str, Any]:
    """An ``agent`` node as the settings editor submits it.

    ``browser_use`` is a real built-in tool the platform does not keep always-on
    (``CRITICAL_TOOLS``), so a definition may switch it off.
    """
    node: dict[str, Any] = {
        "model": "openai/gpt-4o",
        "temperature": 0.3,
        "max_tokens": 1024,
        "tools_disabled": ["browser_use"],
        "skills": [],
        "subagents": [],
    }
    node.update(overrides)
    return node


async def test_created_feature_is_written_and_served(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env

    response = await client.post("/api/features", headers=auth, json=_definition())

    assert response.status_code == 201, response.text
    assert response.json() == {"feature_id": "weekly-report"}
    feature_dir = srv.paths.features_dir / "weekly-report"
    assert (feature_dir / "feature.json").is_file()
    assert (feature_dir / "PROMPT.md").read_text(encoding="utf-8") == SYSTEM_PROMPT

    detail = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert detail["label"] == {"zh": "周报", "en": "Weekly report"}
    assert detail["unit"] == "general"
    assert detail["output_kind"] == "markdown"
    assert detail["user_template"] == "整理成周报：\n{{inputs}}"
    assert detail["system_prompt"] == SYSTEM_PROMPT
    assert detail["input_schema"]["required"] == ["notes"]

    cards = (await client.get("/api/features", headers=auth)).json()
    assert "weekly-report" in [card["id"] for card in cards["features"]]
    assert {"key": "general", "count": 2} in cards["units"]


async def test_updated_and_deleted_feature_is_reflected_immediately(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env
    await client.post("/api/features", headers=auth, json=_definition())

    response = await client.put(
        "/api/features/weekly-report",
        headers=auth,
        json=_definition(
            unit="support",
            prompt={"user_template": "换成周报格式：{{inputs}}"},
            output={"kind": "text"},
        ),
    )

    assert response.status_code == 200, response.text
    assert response.json() == {"feature_id": "weekly-report"}
    detail = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert detail["unit"] == "support"
    assert detail["output_kind"] == "text"
    assert detail["system_prompt"] is None
    assert not (srv.paths.features_dir / "weekly-report" / "PROMPT.md").exists()

    deleted = await client.delete("/api/features/weekly-report", headers=auth)

    assert deleted.status_code == 204
    assert deleted.content == b""
    assert not (srv.paths.features_dir / "weekly-report").exists()
    gone = await client.get("/api/features/weekly-report", headers=auth)
    assert gone.status_code == 404
    assert gone.json()["error"]["code"] == "NOT_FOUND"


async def test_invalid_definition_is_refused_without_writing(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env

    response = await client.post(
        "/api/features",
        headers=auth,
        json=_definition(input_schema={"type": "array", "properties": {"a": {"type": "string"}}}),
    )

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "FEATURE_INVALID"
    assert "input_schema.type must be 'object'" in error["message"]
    assert not (srv.paths.features_dir / "weekly-report").exists()
    cards = (await client.get("/api/features", headers=auth)).json()
    assert "weekly-report" not in [card["id"] for card in cards["features"]]


async def test_bundled_feature_cannot_be_modified_or_deleted(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """A shipped definition is refused, never copied into the user's directory."""
    client, srv, auth = env
    shipped = default_library_root() / BUNDLED_ID / "feature.json"
    before = shipped.read_text(encoding="utf-8")

    updated = await client.put(
        f"/api/features/{BUNDLED_ID}",
        headers=auth,
        json=_definition(BUNDLED_ID),
    )
    deleted = await client.delete(f"/api/features/{BUNDLED_ID}", headers=auth)

    assert updated.status_code == 403, updated.text
    assert deleted.status_code == 403, deleted.text
    assert updated.json()["error"]["code"] == "FORBIDDEN"
    assert shipped.read_text(encoding="utf-8") == before
    assert not (srv.paths.features_dir / BUNDLED_ID).exists()
    detail = (await client.get(f"/api/features/{BUNDLED_ID}", headers=auth)).json()
    assert detail["unit"] == "general"


async def test_member_with_the_features_permission_cannot_author(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Authoring is instance-wide configuration — members run features, not edit them."""
    client, srv, auth = env
    alice = await create_user(client, auth, username="alice")
    await client.post("/api/features", headers=auth, json=_definition())

    assert (await client.get("/api/features", headers=alice)).status_code == 200
    created = await client.post("/api/features", headers=alice, json=_definition("alice-feature"))
    updated = await client.put(
        "/api/features/weekly-report",
        headers=alice,
        json=_definition(prompt={"user_template": "x"}),
    )
    deleted = await client.delete("/api/features/weekly-report", headers=alice)

    assert [r.status_code for r in (created, updated, deleted)] == [403, 403, 403]
    assert created.json()["error"]["code"] == "FORBIDDEN"
    assert not (srv.paths.features_dir / "alice-feature").exists()


async def test_meta_offers_the_editors_choices(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, _srv, auth = env

    response = await client.get("/api/features/_meta", headers=auth)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert payload["output_kinds"] == ["markdown", "json", "text"]
    assert "file-text" in payload["icons"]
    assert "general" in payload["units"] and "sales" in payload["units"]
    assert payload["bundled_ids"] == ["meeting-notes", "quote-draft"]


async def test_meta_loads_without_a_startable_agent(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Opening the editor must not depend on an agent.

    ``env`` has no provider, so the bootstrap agent cannot start. Listing skills
    needs that agent, which is exactly why the capability choices are not part of
    ``_meta``: a definition's metadata has to load anyway.
    """
    client, _srv, auth = env

    response = await client.get("/api/features/_meta", headers=auth)

    assert response.status_code == 200, response.text
    assert "capabilities" not in response.json()


async def test_capabilities_fail_loudly_when_the_agent_cannot_start(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """The agent-dependent choices report the failure — never as empty lists.

    An empty ``skills`` list reads as "this agent has no skills" and would let an
    administrator declare a scope the run cannot honour.
    """
    client, _srv, auth = env

    response = await client.get("/api/features/_capabilities", headers=auth)

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "AGENT_FAILED"


async def test_capabilities_list_what_the_callers_agent_can_name(
    env_with_agent: tuple[httpx.AsyncClient, OctopServer, dict[str, str], str],
) -> None:
    client, _srv, auth, _agent_id = env_with_agent

    response = await client.get("/api/features/_capabilities", headers=auth)

    assert response.status_code == 200, response.text
    payload = response.json()
    assert any(row["ref"].startswith("openai/") for row in payload["models"])
    assert {row["name"] for row in payload["tools"]}
    assert isinstance(payload["skills"], list)
    assert isinstance(payload["subagents"], list)
    assert payload["mcp_servers"] == []
    assert payload["knowledge_bases"] == []


async def test_capability_layer_is_stored_and_served_as_declared(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """The ``agent`` node round-trips: written to ``feature.json``, read back."""
    client, srv, auth = env
    layer = _capability_layer(
        skills=["meeting-notes"],
        subagents=["researcher"],
        mcp_servers=["github"],
        knowledge_base_ids=["kb-1"],
    )

    created = await client.post("/api/features", headers=auth, json=_definition(agent=layer))

    assert created.status_code == 201, created.text
    manifest = json.loads(
        (srv.paths.features_dir / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )
    assert manifest["agent"] == layer
    served = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert served["agent"] == layer


async def test_capability_layer_without_a_declaration_serves_null(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """No ``agent`` node is not an empty one: the editor must tell them apart."""
    client, srv, auth = env

    await client.post("/api/features", headers=auth, json=_definition())

    served = (await client.get("/api/features/weekly-report", headers=auth)).json()
    assert served["agent"] is None
    manifest = json.loads(
        (srv.paths.features_dir / "weekly-report" / "feature.json").read_text(encoding="utf-8")
    )
    assert "agent" not in manifest


@pytest.mark.parametrize(
    ("layer", "expected"),
    [
        ({"tools_disabled": ["no-such-tool"]}, "agent.tools_disabled names unknown built-in tools"),
        ({"model": "gpt-4o"}, "agent.model must name a model as 'provider/model'"),
        ({"temperature": 3}, "agent.temperature must be between 0 and 2"),
        ({"skills": ["a", "a"]}, "agent.skills must not repeat the same entry"),
        ({"skys": []}, "agent uses unsupported keys"),
    ],
)
async def test_capability_layer_the_run_could_not_honour_is_refused(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    layer: dict[str, Any],
    expected: str,
) -> None:
    """A capability the platform cannot apply is refused at write time, not stored."""
    client, srv, auth = env

    response = await client.post("/api/features", headers=auth, json=_definition(agent=layer))

    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "FEATURE_INVALID"
    assert expected in error["message"]
    assert not (srv.paths.features_dir / "weekly-report").exists()


@pytest.mark.parametrize("path", ["/api/features", "/api/features/meeting-notes"])
async def test_member_without_the_features_permission_is_refused(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    path: str,
) -> None:
    client, _srv, auth = env
    alice = await create_user(client, auth, username="alice", permissions=[])

    response = await client.get(path, headers=alice)

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "FORBIDDEN"


# --- The capability layer on a real run ------------------------------------


@pytest.fixture
async def env_runnable(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    """A server whose harness answers with text, plus a usable model.

    ``env``'s fake yields no tokens, and a feature run treats an empty answer as
    a failure, so a run test needs a harness that speaks.
    """
    fake = FakeHarnessAgent(chunks=[{"type": "token", "node": "agent", "content": "草稿"}])
    async with octop_client(tmp_octop_home, fake_agent=fake) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        auth = await auth_header(client)
        await seed_openai_provider(client, auth)
        yield client, srv, auth


def _private_knowledge_base(srv: OctopServer, owner_user_id: int, name: str) -> str:
    """A knowledge base only its owner (and an admin) can read."""
    assert srv.services is not None
    row = srv.services.repos.knowledge_repo.create_base(
        owner_user_id=owner_user_id, name=name, shared=False
    )
    return str(row.id)


def _request_the_run_sent(srv: OctopServer, task_id: str) -> dict[str, Any]:
    """The harness request one finished run actually carried.

    Read off the run's own log row, so the agent is the one the run used — not one
    this test guessed at.
    """
    assert srv.services is not None
    row = srv.services.repos.feature_tasks_repo.get(task_id)
    assert row is not None
    handle = srv.app_runtime.agent_registry.get_agent(str(row.agent_id))
    request = handle.last_request
    assert request is not None, "the harness never saw this run"
    return request


async def test_a_run_applies_the_declared_capability_layer(
    env_runnable: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Every declared capability reaches the harness request the run sends."""
    client, srv, auth = env_runnable
    admin_id = await resolve_user_id(client, auth, "admin")
    kb_id = _private_knowledge_base(srv, admin_id, "报价口径")
    await client.post(
        "/api/features",
        headers=auth,
        json=_definition(
            agent=_capability_layer(knowledge_base_ids=[kb_id]),
        ),
    )

    response = await client.post(
        "/api/features/weekly-report/run", headers=auth, json={"inputs": {"notes": "a"}}
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["output"] == "草稿"
    request = _request_the_run_sent(srv, body["task_id"])
    assert request["model"] == "openai/gpt-4o"
    assert request["configurable"]["skills"] == []
    assert request["configurable"]["octop_feature_scope"] == {
        "tools_disabled": ["browser_use"],
        "subagents": [],
    }
    assert request["configurable"]["knowledge_base_ids"] == [kb_id]
    # Sampling knobs outrank the agent's own config for this run only.
    assert request["configurable"]["octop_agent_runtime_overrides"] == {
        "temperature": 0.3,
        "max_tokens": 1024,
    }


async def test_a_run_without_a_capability_layer_stamps_nothing(
    env_runnable: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """A definition that predates the layer runs exactly as it did before it."""
    client, srv, auth = env_runnable
    await client.post("/api/features", headers=auth, json=_definition())

    response = await client.post(
        "/api/features/weekly-report/run", headers=auth, json={"inputs": {"notes": "a"}}
    )

    assert response.status_code == 200, response.text
    request = _request_the_run_sent(srv, response.json()["task_id"])
    assert "model" not in request
    configurable = request.get("configurable") or {}
    assert "skills" not in configurable
    assert "octop_feature_scope" not in configurable
    assert "knowledge_base_ids" not in configurable
    assert "mcp_servers" not in request


async def test_the_same_feature_reads_different_data_for_different_callers(
    env_runnable: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Design 5.2: configuration is shared, data follows the caller.

    One definition declares a knowledge base alice owns privately. Alice's run
    mounts it; bob's run — same definition, same model, same tools — mounts
    nothing, because the base is not his to read.
    """
    client, srv, auth = env_runnable
    users = await ensure_users(client, auth, "alice", "bob")
    alice_id = await resolve_user_id(client, auth, "alice")
    for username in ("alice", "bob"):
        await create_agent(client, users[username])
    kb_id = _private_knowledge_base(srv, alice_id, "机加工 BOM")
    await client.post(
        "/api/features",
        headers=auth,
        json=_definition(agent=_capability_layer(knowledge_base_ids=[kb_id])),
    )

    alice = await client.post(
        "/api/features/weekly-report/run",
        headers=users["alice"],
        json={"inputs": {"notes": "a"}},
    )
    bob = await client.post(
        "/api/features/weekly-report/run",
        headers=users["bob"],
        json={"inputs": {"notes": "a"}},
    )

    assert alice.status_code == 200, alice.text
    assert bob.status_code == 200, bob.text
    alice_request = _request_the_run_sent(srv, alice.json()["task_id"])
    bob_request = _request_the_run_sent(srv, bob.json()["task_id"])
    assert alice_request["configurable"]["knowledge_base_ids"] == [kb_id]
    assert bob_request["configurable"]["knowledge_base_ids"] == []
    # The configuration half is identical for both: the feature's own model.
    assert alice_request["model"] == bob_request["model"] == "openai/gpt-4o"


async def test_a_declared_model_this_instance_cannot_run_fails_the_run(
    env_runnable: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """A feature never quietly runs on another model, and the failure is recorded."""
    client, srv, auth = env_runnable
    await client.post(
        "/api/features",
        headers=auth,
        json=_definition(agent=_capability_layer(model="groq/llama-3.1-70b")),
    )

    response = await client.post(
        "/api/features/weekly-report/run", headers=auth, json={"inputs": {"notes": "a"}}
    )

    assert response.status_code == 500, response.text
    assert srv.services is not None
    logged = srv.services.repos.feature_tasks_repo.list_for_user(
        user_id=await resolve_user_id(client, auth, "admin")
    )
    failed = [row for row in logged if row.feature_id == "weekly-report"]
    assert [row.status for row in failed] == ["failed"]
    # The reason the run stopped is on the row: ``INTERNAL_ERROR`` answers with a
    # generic message, so the log is the only place this diagnosis survives.
    assert "groq/llama-3.1-70b" in (failed[0].error or "")
