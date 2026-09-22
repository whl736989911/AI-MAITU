"""End-to-end: a feature's own agent — who makes it, who runs on it, who may edit it.

Design 5.1 (S1) and 5.2 (S2), end to end over HTTP:

* a definition nobody personalized runs on the caller's own agent, exactly as every
  run did before features could have agents at all;
* the personalization entry point creates ``feat-<feature_id>`` — app-owned
  (``user_id IS NULL``), idempotent, live — and a run of that definition then runs
  on it, single-shot and stepped alike, while every *data* lookup stays the
  caller's;
* administrators may edit that agent through the ordinary agent endpoints, members
  may not (403), and members keep running the feature;
* several callers run the same feature agent at once without mixing sessions.
"""

from __future__ import annotations

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

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

FEATURE_ID = "weekly-report"
AGENT_ID = f"feat-{FEATURE_ID}"
SYSTEM_PROMPT = "Write the summary in three sections."
OUTPUT = "草稿"


def _definition(feature_id: str = FEATURE_ID, **overrides: Any) -> dict[str, Any]:
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
        "prompt": {"user_template": "整理成周报：\n{{inputs}}", "system_prompt": SYSTEM_PROMPT},
        "output": {"kind": "markdown"},
        "permissions": {"allow_units": ["*"]},
    }
    payload.update(overrides)
    return payload


STEPS: list[dict[str, Any]] = [
    {
        "id": "draft",
        "name": "起草",
        "mode": "agent",
        "inputs": [],
        "output": {"name": "draft", "schema": "text"},
        "prompt": "STEP-1 写初稿。",
        "gate": "auto",
        "on_failure": "abort",
    },
    {
        "id": "polish",
        "name": "润色",
        "mode": "agent",
        "inputs": ["draft"],
        "output": {"name": "final", "schema": "text"},
        "prompt": "STEP-2 润色。",
        "gate": "auto",
        "on_failure": "abort",
    },
]


@pytest.fixture
async def env_speaking(
    tmp_octop_home: Path,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str]]]:
    """A server whose harness answers with text, plus a usable model.

    The default fake yields no tokens, and a feature run treats an empty answer as
    a failure, so every run test here needs a harness that speaks.
    """
    fake = FakeHarnessAgent(chunks=[{"type": "token", "node": "agent", "content": OUTPUT}])
    async with octop_client(tmp_octop_home, fake_agent=fake) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        auth = await auth_header(client)
        await seed_openai_provider(client, auth)
        yield client, srv, auth


def _task_row(srv: OctopServer, task_id: str) -> Any:
    assert srv.services is not None
    row = srv.services.repos.feature_tasks_repo.get(task_id)
    assert row is not None
    return row


def _run_request(srv: OctopServer, task_id: str) -> dict[str, Any]:
    """The harness request one finished run carried — off the agent that ran it.

    The agent comes from the run's own log row, so this never guesses which agent a
    run used: it reads the answer back from the source that recorded it.
    """
    row = _task_row(srv, task_id)
    assert row.agent_id
    handle = srv.app_runtime.agent_registry.get_agent(str(row.agent_id))
    request = handle.last_request
    assert request is not None, "the harness never saw this run"
    return request


def _scope(request: dict[str, Any]) -> dict[str, Any]:
    """The feature scope one run stamped (design 7.x's tool/subagent narrowing)."""
    return dict(request["configurable"]["octop_feature_scope"])


async def _run(
    client: httpx.AsyncClient,
    auth: dict[str, str],
    *,
    feature_id: str = FEATURE_ID,
    notes: str = "a",
) -> dict[str, Any]:
    response = await client.post(
        f"/api/features/{feature_id}/run", headers=auth, json={"inputs": {"notes": notes}}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _agent_ids(
    client: httpx.AsyncClient, auth: dict[str, str], scope: str = "mine"
) -> list[str]:
    response = await client.get("/api/agents", headers=auth, params={"scope": scope})
    assert response.status_code == 200, response.text
    return [row["agent_id"] for row in response.json()]


async def _personalize(client: httpx.AsyncClient, auth: dict[str, str]) -> dict[str, Any]:
    response = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


# --- the unchanged branch ---------------------------------------------------


async def test_a_definition_nobody_personalized_runs_on_the_callers_agent(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """No agent is created for it, and the run is the one it always was."""
    client, srv, auth = env_speaking
    created = await client.post("/api/features", headers=auth, json=_definition())
    assert created.status_code == 201, created.text
    own = await _agent_ids(client, auth)
    assert own, "the bootstrap admin owns an agent"

    body = await _run(client, auth)

    assert body["output"] == OUTPUT
    row = _task_row(srv, body["task_id"])
    assert row.agent_id == own[0]
    assert _run_request(srv, body["task_id"])["agent_id"] == own[0]
    assert srv.app_runtime.agent_registry.get_row(AGENT_ID) is None


# --- materialization --------------------------------------------------------


async def test_the_entry_point_creates_the_features_own_agent_once(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env_speaking
    await client.post(
        "/api/features",
        headers=auth,
        json=_definition(agent={"model": "openai/gpt-4o"}, color="#1f6feb"),
    )

    first = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=auth)
    second = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=auth)

    assert first.status_code == 200, first.text
    assert first.json() == {"feature_id": FEATURE_ID, "agent_id": AGENT_ID, "created": True}
    assert second.json() == {"feature_id": FEATURE_ID, "agent_id": AGENT_ID, "created": False}

    row = srv.app_runtime.agent_registry.get_row(AGENT_ID)
    assert row is not None
    assert row.user_id is None, "app-owned, not the administrator's"
    assert row.name == "周报"
    assert row.description == "把零散记录整理成周报"
    assert row.icon_name == "clipboard-list"
    assert row.color == "#1f6feb"
    assert row.default_model == "openai/gpt-4o", "the declared model is the agent's base"
    assert srv.paths.agent_workspace(AGENT_ID).is_dir(), "the workspace exists on disk"
    # Live, not merely created: the panels that read skills and workspace files
    # need a handle, and this is what makes one exist.
    assert srv.app_runtime.agent_registry.get_agent(AGENT_ID) is not None
    # It is nobody's agent: absent from every "my agents" list, present only in an
    # administrator's "all" view — which is where editing it happens.
    assert AGENT_ID not in await _agent_ids(client, auth)
    assert AGENT_ID in await _agent_ids(client, auth, scope="all")


async def test_a_personalized_definition_runs_on_its_own_agent(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env_speaking
    await client.post("/api/features", headers=auth, json=_definition())
    await _personalize(client, auth)
    own = await _agent_ids(client, auth)

    body = await _run(client, auth)

    assert body["output"] == OUTPUT
    row = _task_row(srv, body["task_id"])
    assert row.agent_id == AGENT_ID
    request = _run_request(srv, body["task_id"])
    assert request["agent_id"] == AGENT_ID
    assert srv.app_runtime.agent_registry.get_agent(own[0]).last_request is None, (
        "the caller's own agent never saw the run"
    )
    # The session is keyed by (agent, caller) — the feature's agent has its own
    # thread for this caller, and the caller's agent has none from this run.
    threads = srv.app_runtime.gateway.thread_registry
    admin_id = await resolve_user_id(client, auth, "admin")
    assert [
        thread.thread_id for thread in threads.list_threads(agent_id=AGENT_ID, user_id=admin_id)
    ]
    assert not threads.list_threads(agent_id=own[0], user_id=admin_id)


async def test_a_stepped_definition_runs_on_its_own_agent(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """The stepped path switches too — it is a second resolution site, not a shared one."""
    client, srv, auth = env_speaking
    await client.post("/api/features", headers=auth, json=_definition(steps=STEPS))
    await _personalize(client, auth)

    response = await client.post(
        f"/api/features/{FEATURE_ID}/run", headers=auth, json={"inputs": {"notes": "a"}}
    )

    assert response.status_code == 200, response.text
    task_id = response.json()["task_id"]
    assert _task_row(srv, task_id).agent_id == AGENT_ID
    audit = await client.get(f"/api/features/{FEATURE_ID}/runs/{task_id}/audit", headers=auth)
    assert audit.status_code == 200, audit.text
    assert audit.json()["snapshot"]["agent_id"] == AGENT_ID


async def test_the_feature_agent_is_started_once_and_reused(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A live handle is reused: neither the run path nor the entry point restarts it."""
    client, srv, auth = env_speaking
    await client.post("/api/features", headers=auth, json=_definition())
    await _personalize(client, auth)
    registry = srv.app_runtime.agent_registry
    handle = registry.get_agent(AGENT_ID)
    starts: list[str] = []
    real_start = registry.start

    async def _start(agent_id: str) -> None:
        starts.append(agent_id)
        await real_start(agent_id)

    monkeypatch.setattr(registry, "start", _start)

    await _run(client, auth)
    await _run(client, auth, notes="b")
    third = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=auth)

    assert third.json()["created"] is False
    assert starts == [], "a running feature agent is never started twice"
    assert registry.get_agent(AGENT_ID) is handle


async def test_editing_the_definition_does_not_unpersonalize_it(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """The agent row is the record, so a definition PUT cannot drop the association."""
    client, srv, auth = env_speaking
    await client.post("/api/features", headers=auth, json=_definition())
    await _personalize(client, auth)

    saved = await client.put(
        f"/api/features/{FEATURE_ID}",
        headers=auth,
        json=_definition(unit="support", prompt={"user_template": "换个说法：{{inputs}}"}),
    )

    assert saved.status_code == 200, saved.text
    detail = (await client.get(f"/api/features/{FEATURE_ID}", headers=auth)).json()
    assert detail["unit"] == "support"
    assert (await _personalize(client, auth))["created"] is False
    assert _task_row(srv, (await _run(client, auth))["task_id"]).agent_id == AGENT_ID


# --- permissions (S2) -------------------------------------------------------


async def test_only_an_administrator_may_edit_the_feature_agent(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    client, srv, auth = env_speaking
    alice = await create_user(client, auth, username="alice")
    await client.post("/api/features", headers=auth, json=_definition())
    await _personalize(client, auth)

    personalizing = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=alice)
    member_patch = await client.patch(
        f"/api/agents/{AGENT_ID}", headers=alice, json={"name": "alice 的"}
    )
    member_get = await client.get(f"/api/agents/{AGENT_ID}", headers=alice)
    admin_patch = await client.patch(
        f"/api/agents/{AGENT_ID}", headers=auth, json={"name": "周报助手"}
    )
    run = await client.post(
        f"/api/features/{FEATURE_ID}/run", headers=alice, json={"inputs": {"notes": "a"}}
    )

    assert [personalizing.status_code, member_patch.status_code, member_get.status_code] == [
        403,
        403,
        403,
    ]
    assert member_patch.json()["error"]["code"] == "FORBIDDEN"
    assert admin_patch.status_code == 200, admin_patch.text
    assert admin_patch.json()["name"] == "周报助手"
    # Running is not editing: the feature's own permission still governs it.
    assert run.status_code == 200, run.text
    assert _task_row(srv, run.json()["task_id"]).agent_id == AGENT_ID


# --- refusals ---------------------------------------------------------------


async def test_a_bundled_definition_cannot_be_personalized(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """Read-only is read-only: no agent is created for a shipped definition."""
    client, srv, auth = env_speaking

    refused = await client.post("/api/features/meeting-notes/agent", headers=auth)

    assert refused.status_code == 403, refused.text
    error = refused.json()["error"]
    assert error["code"] == "FORBIDDEN"
    assert error["details"] == {"feature_id": "meeting-notes", "reason": "bundled"}
    assert srv.app_runtime.agent_registry.get_row("feat-meeting-notes") is None


async def test_a_feature_whose_id_cannot_name_an_agent_is_refused_yet_still_runs(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """A 60-character id cannot carry ``feat-`` into a legal agent id.

    Personalization says so instead of creating an id the agent layer would refuse;
    the run keeps the caller's own agent, because no agent for this definition could
    ever exist (the boundary itself is pinned in ``tests/unit/features``).
    """
    client, srv, auth = env_speaking
    long_id = "r" * 60
    assert (
        await client.post("/api/features", headers=auth, json=_definition(long_id))
    ).status_code == 201

    refused = await client.post(f"/api/features/{long_id}/agent", headers=auth)
    body = await _run(client, auth, feature_id=long_id)

    assert refused.status_code == 400, refused.text
    error = refused.json()["error"]
    assert error["code"] == "FEATURE_INVALID"
    assert f"feat-{long_id}" in error["details"]["errors"], "the refusal names the id it tried"
    assert _task_row(srv, body["task_id"]).agent_id in await _agent_ids(client, auth)


async def test_personalizing_reports_a_feature_agent_that_cannot_start(
    env: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """An agent that will not start is an error, never a success the next call fails on.

    ``env`` has no provider, so no harness agent can start. The row is still created
    — from then on the definition *is* personalized — and the caller is told why the
    agent it just asked for cannot be used yet, instead of a 200 that makes the
    editor's next call fail.
    """
    client, srv, auth = env
    await client.post("/api/features", headers=auth, json=_definition())

    response = await client.post(f"/api/features/{FEATURE_ID}/agent", headers=auth)

    assert response.status_code == 500, response.text
    assert response.json()["error"]["code"] == "AGENT_FAILED"
    row = srv.app_runtime.agent_registry.get_row(AGENT_ID)
    assert row is not None, "the definition is personalized from here on"
    assert row.user_id is None


# --- the data plane still follows the caller (design 1.3) -------------------

PROBE_SCRIPT = Path(__file__).resolve().parents[1] / "fixtures" / "mcp_probe_stdio.py"
CALLER_TOKEN = "TOKEN-CALLER"


def _probe_spec(token: str) -> dict[str, Any]:
    """One caller's own ``probe`` MCP server, launched with that caller's credential."""
    return {
        "transport": "stdio",
        "command": sys.executable,
        "args": [str(PROBE_SCRIPT)],
        "env": {"PROBE_TOKEN": token},
    }


def _tool_named(tools: list[Any], name: str) -> Any:
    for tool in tools:
        if getattr(tool, "name", "") == name:
            return tool
    raise AssertionError(f"no {name!r} tool in {[getattr(tool, 'name', tool) for tool in tools]}")


async def _credential(tool: Any) -> str:
    """What a probe tool reports — the account its call goes out with."""
    result = await tool.ainvoke({})
    if isinstance(result, str):
        return result
    if isinstance(result, list) and result and isinstance(result[0], dict):
        return str(result[0].get("text") or "")
    raise AssertionError(f"unexpected tool result: {result!r}")


async def test_a_personalized_run_uses_the_callers_connector_not_the_agents(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """The caller's credentials ride the turn, never the agent (design 1.3/5.3).

    The feature's agent is app-owned, so ``prepare_chat_mcp`` sees a caller that is
    not the agent's owner and loads that caller's connectors into the per-turn
    registry — the same lane a shared agent uses
    (``test_shared_agent_mcp_credentials.py``), keyed by ``(agent_id, caller)``.
    Nothing of the caller's is loaded onto an agent every other caller also runs.
    """
    client, srv, auth = env_speaking
    alice = await create_user(client, auth, username="alice")
    alice_id = await resolve_user_id(client, auth, "alice")
    saved = await client.put(
        "/api/connectors/custom-mcp",
        headers=alice,
        json={"servers": {"probe": _probe_spec(CALLER_TOKEN)}},
    )
    assert saved.status_code == 200, saved.text
    created = await client.post(
        "/api/features", headers=auth, json=_definition(agent={"mcp_servers": ["probe"]})
    )
    assert created.status_code == 201, created.text
    await _personalize(client, auth)

    body = await _run(client, alice)

    assert _task_row(srv, body["task_id"]).agent_id == AGENT_ID
    registry = srv.app_runtime.agent_registry
    tools = registry.turn_mcp_tools(AGENT_ID, alice_id, ["probe"])["probe"]
    assert await _credential(_tool_named(tools, "probe_whoami")) == f"token={CALLER_TOKEN}"
    assert getattr(registry.get_agent(AGENT_ID), "_mcp_tools", []) == []


# --- the inheritance semantics the switch changes (design 1.3) ---------------


async def test_the_declared_scope_is_intersected_with_the_agent_that_runs(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
) -> None:
    """``resolve_capability`` intersects with the *run* agent — measured, not assumed.

    A declared ``agent.subagents`` is a scope, and the scope is resolved against
    whichever agent the run uses. Unpersonalized that is the caller's agent, so a
    subagent installed there is kept; once the definition has an agent of its own,
    the same declaration is resolved against *it* — the subagent it does not have is
    withheld, and the run is stamped with an empty scope. "Inherit" now means "the
    feature's agent decides" (design 5.1), which is the point of giving a feature an
    agent at all, and it only happens for definitions somebody personalized.
    """
    client, srv, auth = env_speaking
    alice = await create_user(client, auth, username="alice")
    alice_agent = await create_agent(client, alice)
    installed = await client.post(
        f"/api/agents/{alice_agent}/subagents/install",
        headers=alice,
        json={"slug": "engineering-software-architect", "locale": "en"},
    )
    assert installed.status_code == 201, installed.text
    listed = await client.get(f"/api/agents/{alice_agent}/subagents", headers=alice)
    role = next(
        row["name"] for row in listed.json() if row["slug"] == "engineering-software-architect"
    )
    created = await client.post(
        "/api/features", headers=auth, json=_definition(agent={"subagents": [role]})
    )
    assert created.status_code == 201, created.text

    before = await _run(client, alice)
    await _personalize(client, auth)
    after = await _run(client, alice)

    assert _task_row(srv, before["task_id"]).agent_id == alice_agent
    assert _scope(_run_request(srv, before["task_id"]))["subagents"] == [role]
    assert _task_row(srv, after["task_id"]).agent_id == AGENT_ID
    assert _scope(_run_request(srv, after["task_id"]))["subagents"] == []


# --- concurrency (design 7.2) -----------------------------------------------


class _OverlapProbe:
    """The feature agent's ``stream``, wrapped to observe how turns overlap.

    Nothing in the run path is instrumented: this stands in for the harness the way
    the fake already does, and records what the harness would see — how many turns
    were inside the one agent at once, and which session each of them carried. The
    delay is what makes a real overlap visible; without it a run that was serialized
    behind another looks exactly like one that was not.
    """

    def __init__(self, agent: Any, *, delay: float = 0.05) -> None:
        self._agent = agent
        self._stream = agent.stream
        self._delay = delay
        self.peak = 0
        self.thread_ids: list[str] = []
        self._depth = 0

    def __call__(self, request: dict[str, Any]) -> Any:
        self.thread_ids.append(str(request.get("thread_id") or ""))
        return self._observe(request)

    async def _observe(self, request: dict[str, Any]) -> Any:
        self._depth += 1
        self.peak = max(self.peak, self._depth)
        try:
            await asyncio.sleep(self._delay)
            async for chunk in self._stream(request):
                yield chunk
        finally:
            self._depth -= 1


async def test_several_callers_run_the_same_feature_agent_at_once(
    env_speaking: tuple[httpx.AsyncClient, OctopServer, dict[str, str]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """One shared agent, one session per caller, nothing shared but the configuration.

    Nothing serializes different callers against each other — ``run_in_session``
    serializes *one session* — and nothing has to: the feature's agent is shared
    configuration, while the session, the connectors, the knowledge scope and the
    turn-scoped tools are keyed by ``(agent, caller)``.
    """
    client, srv, auth = env_speaking
    await client.post("/api/features", headers=auth, json=_definition())
    await _personalize(client, auth)
    users = await ensure_users(client, auth, "alice", "bob", "carol")
    registry = srv.app_runtime.agent_registry
    handle = registry.get_agent(AGENT_ID)
    probe = _OverlapProbe(handle)
    monkeypatch.setattr(handle, "stream", probe)
    names = ("alice", "bob", "carol")

    responses = await asyncio.gather(
        *(
            client.post(
                f"/api/features/{FEATURE_ID}/run",
                headers=users[name],
                json={"inputs": {"notes": name}},
            )
            for name in names
        )
    )

    assert [response.status_code for response in responses] == [200, 200, 200], [
        response.text for response in responses
    ]
    for name, response in zip(names, responses, strict=True):
        row = _task_row(srv, response.json()["task_id"])
        assert row.agent_id == AGENT_ID
        assert row.status == "succeeded"
        assert json.loads(row.inputs) == {"notes": name}, "each row kept its own inputs"
    # All three were inside the one agent at the same time, each on its own session:
    # the calls really overlapped, and they did not share a thread.
    assert probe.peak == len(names)
    assert sorted(probe.thread_ids) == sorted(set(probe.thread_ids))
    threads = srv.app_runtime.gateway.thread_registry
    for name in names:
        user_id = await resolve_user_id(client, auth, name)
        rows = threads.list_threads(agent_id=AGENT_ID, user_id=user_id)
        assert [row.thread_id for row in rows] == [probe.thread_ids[names.index(name)]]
    # One live handle served all three: the agent is a shared configuration carrier,
    # and a caller who has never created an agent of their own can still run it.
    assert registry.get_agent(AGENT_ID) is handle
    assert await _agent_ids(client, users["alice"]) == []
