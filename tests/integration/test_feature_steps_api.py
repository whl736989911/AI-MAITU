"""End-to-end: a feature whose definition declares task steps.

The scenario is the machining / BOM case the redesign note is built on (7.1):
read the BOM, cross-check it, fill the inferred fields, stop for a human,
self-check, deliver. What these tests pin down is the engine's own contract:

* artifacts cross the steps as **data** — the next step's prompt carries the
  previous step's parsed JSON, not a wall of text;
* a ``confirm`` gate really stops the run, and approving it continues the *same*
  run: earlier steps are not re-run and their artifacts are still there;
* a ``validate`` gate that does not pass is never delivered;
* a step the platform may not decide alone escalates and waits for a human;
* a human correction (``edits``) is injected *and* audited, before and after;
* rewinding voids the artifacts of every later step;
* ``mode: "orchestrate"`` is refused outright rather than quietly run as one agent.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any
from unittest import mock

import httpx
import pytest
from langchain.agents.middleware import ToolCallRequest
from langchain_core.messages import ToolMessage

from octop.infra.agents.middleware import feature_dispatch
from octop.infra.features.dispatch import DEFAULT_MAX_PARALLEL
from octop.infra.server import OctopServer
from tests.support.app import octop_client
from tests.support.auth import (
    auth_header,
    bootstrap_admin,
    resolve_user_id,
    seed_openai_provider,
)
from tests.support.fakes import FakeHarnessAgent

FEATURE_ID = "bom-extraction"
INPUTS = {"bom_pdf": "总装图-B.pdf"}

# The two rows every step of the scenario works on: L1 项, 名称, 规格, 数量.
ROWS_READ = [[1, "螺栓", "M8", 4], [2, "垫片", "φ8", 8]]
ROWS_CHECKED = [[1, "螺栓", "M8", 4], [2, "垫片", "φ8", 9]]
ROWS_FILLED = [[1, "螺栓", "M8", 4, "达克罗"], [2, "垫片", "φ8", 9, "达克罗"]]

# JSON each step's turn answers with, keyed by the marker in that step's prompt.
ANSWER_ROWS = json.dumps(ROWS_READ, ensure_ascii=False)
ANSWER_CHECKED = json.dumps(ROWS_CHECKED, ensure_ascii=False)
ANSWER_FILLED = json.dumps({"rows": ROWS_FILLED, "l1_count": 2}, ensure_ascii=False)
ANSWER_PASSED = json.dumps({"passed": True, "problems": []}, ensure_ascii=False)
ANSWER_FAILED = json.dumps({"passed": False, "problems": ["Coating 缺失"]}, ensure_ascii=False)
ANSWER_PACKAGE = "已生成 BOM.xlsx（2 行 L1）"


def _steps() -> list[dict[str, Any]]:
    """The skeleton from the case: five steps, one human gate, one check gate.

    ``cross_check`` escalates instead of guessing (7.2's 中止上报), ``fill_inferred``
    is the human gate the user may edit through, and ``self_check`` is the gate a
    delivery must not pass without.
    """
    return [
        {
            "id": "extract_l1",
            "name": "提取 L1 项",
            "mode": "agent",
            "inputs": [],
            "tools": ["read_file"],
            "max_parallel": 4,
            "output": {"name": "bom_rows", "schema": "table:4cols"},
            "prompt": "STEP-1 读 BOM PDF，识别层级列，过滤 L1。",
            "gate": "auto",
            "on_failure": "abort",
        },
        {
            "id": "cross_check",
            "name": "交叉核对总装图",
            "mode": "agent",
            "inputs": ["bom_rows"],
            "output": {"name": "checked_rows", "schema": "table:4cols"},
            "prompt": "STEP-2 用总装图交叉核对数量。",
            "gate": "auto",
            "allow_edit": True,
            "on_failure": "escalate",
        },
        {
            "id": "fill_inferred",
            "name": "填充推断字段",
            "mode": "agent",
            "inputs": ["checked_rows"],
            "output": {"name": "filled_rows", "schema": "object"},
            "prompt": "STEP-3 按材质推导 Coating。",
            "gate": "confirm",
            "allow_edit": True,
            "on_failure": "abort",
        },
        {
            "id": "self_check",
            "name": "自检清单验证",
            "mode": "agent",
            "inputs": ["filled_rows"],
            "output": {"name": "verdict", "schema": "object"},
            "prompt": "STEP-4 按自检清单验证。",
            "gate": "validate",
            "allow_edit": True,
            "on_failure": "abort",
        },
        {
            "id": "deliver",
            "name": "交付",
            "mode": "agent",
            "inputs": ["verdict", "filled_rows"],
            "output": {"name": "package", "schema": "text"},
            "prompt": "STEP-5 出包并交付。",
            "gate": "auto",
            "on_failure": "abort",
        },
    ]


def _definition(*, steps: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """A stepped definition exactly as the settings UI submits it."""
    return {
        "id": FEATURE_ID,
        "label": {"zh": "BOM 提取", "en": "BOM extraction"},
        "description": {"zh": "从 BOM 与总装图提取 L1", "en": "Extract L1 rows"},
        "icon_name": "file-spreadsheet",
        "unit": "general",
        "input_schema": {
            "type": "object",
            "required": ["bom_pdf"],
            "properties": {
                "bom_pdf": {
                    "type": "string",
                    "format": "textarea",
                    "title": {"zh": "BOM 图纸", "en": "BOM PDF"},
                },
            },
        },
        "prompt": {"user_template": "按步骤提取：\n{{inputs}}"},
        "output": {"kind": "json"},
        "steps": _steps() if steps is None else steps,
    }


class _ScriptedHarness:
    """A harness double whose answer depends on the step's prompt marker.

    ``FakeHarnessAgent`` replays one fixed chunk list for every turn, and a
    stepped run needs a different answer per step — plus, on the correction
    paths, a different answer for the *second* turn of one step.

    This deliberately does **not** subclass ``FakeHarnessAgent``: the patched
    harness manager clones any ``FakeHarnessAgent`` it is handed (one instance per
    agent run), and a clone would drop the script — the single thing this double
    exists for. Everything else the pipeline needs is delegated to one wrapped
    fake.

    A marker with an exhausted script raises: a step running more turns than the
    test scripted is a failure of the test's premise, not a step failure to retry.

    *dispatches* adds the one thing a text-only double cannot do: a step turn that
    calls the harness ``task`` tool. The double then plays langgraph's ``ToolNode``
    — it hands every ``task`` call of that turn to the real
    ``FeatureDispatchMiddleware`` the agent registered, concurrently, which is what
    the real node does (``asyncio.gather`` over the calls of one assistant
    message). Only the *harness* is a double here: the ceiling, the record and the
    ledger are the shipped ones.
    """

    def __init__(
        self,
        script: dict[str, list[str]],
        dispatches: dict[str, list[tuple[str, str]]] | None = None,
    ) -> None:
        self._fake = FakeHarnessAgent()
        self.script = {marker: list(answers) for marker, answers in script.items()}
        self.dispatches = dispatches or {}
        self.seen: list[tuple[str, dict[str, Any]]] = []
        self.dispatched: list[tuple[str, str]] = []
        """Every ``(role, task)`` a step turn handed to the dispatch boundary."""

    def __getattr__(self, name: str) -> Any:
        """Everything else the harness pipeline calls, off the wrapped fake."""
        if name == "_fake":
            raise AttributeError(name)
        return getattr(self._fake, name)

    async def stream(self, request: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        prompt = _prompt_of(request)
        marker = next((name for name in self.script if name in prompt), "")
        if not marker:
            raise AssertionError(f"no scripted answer for this prompt: {prompt[:200]}")
        answers = self.script[marker]
        if not answers:
            raise AssertionError(f"the script for {marker} is exhausted")
        self._fake.last_request = request
        self.seen.append((marker, request))
        calls = self.dispatches.get(marker, [])
        if calls:
            config = {"configurable": dict(request.get("configurable") or {})}
            with mock.patch.object(feature_dispatch, "get_config", lambda: config):
                # One assistant message, several ``task`` calls: ``ToolNode`` runs
                # them concurrently, and that is the only reason a step can
                # decompose into parallel work at all.
                await asyncio.gather(*(self._dispatch(role, task) for role, task in calls))
        yield {"type": "token", "node": "agent", "content": answers.pop(0)}
        yield {"type": "state_snapshot", "data": {}}

    async def _dispatch(self, role: str, task: str) -> Any:
        """One ``task`` tool call, driven exactly as ``ToolNode`` drives it."""
        self.dispatched.append((role, task))
        call = ToolCallRequest(
            tool_call={
                "name": "task",
                "args": {"subagent_type": role, "description": task},
                "id": f"call-{len(self.dispatched)}",
            },
            tool=None,
            state=None,
            runtime=None,
        )

        async def _subagent(_request: ToolCallRequest) -> ToolMessage:
            # Stands in for deepagents' ``task`` body — running the subagent. The
            # delay is what makes "these two ran at the same time" observable; a
            # fake that answered instantly could not tell a ceiling from a queue.
            await asyncio.sleep(DISPATCH_LATENCY)
            return ToolMessage(
                content=f"{role} 的产出：{task}", tool_call_id=str(_request.tool_call["id"])
            )

        return await feature_dispatch.FeatureDispatchMiddleware().awrap_tool_call(call, _subagent)

    def prompts(self, marker: str) -> list[str]:
        """Every prompt the run sent for *marker*, oldest first."""
        return [_prompt_of(request) for name, request in self.seen if name == marker]

    def requests(self, marker: str) -> list[dict[str, Any]]:
        return [request for name, request in self.seen if name == marker]

    def markers(self) -> list[str]:
        return [name for name, _request in self.seen]


def _squash(text: str) -> str:
    """Text with all whitespace removed — for comparing JSON across indentations."""
    return "".join(text.split())


def _prompt_of(request: dict[str, Any]) -> str:
    """Everything the turn told the model, as one string (message layout free)."""
    messages = request.get("messages") or []
    return "\n".join(str(message.get("content") or "") for message in messages)


@asynccontextmanager
async def _stepped_env(
    home: Path,
    script: dict[str, list[str]],
    *,
    definition: dict[str, Any] | None = None,
    dispatches: dict[str, list[tuple[str, str]]] | None = None,
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str], _ScriptedHarness]]:
    """A real server, an admin session, one stepped feature, one scripted harness."""
    harness = _ScriptedHarness(script, dispatches)
    async with octop_client(home, fake_agent=harness) as (client, srv):
        await bootstrap_admin(client, home)
        auth = await auth_header(client)
        await seed_openai_provider(client, auth)
        created = await client.post("/api/features", headers=auth, json=definition or _definition())
        assert created.status_code == 201, created.text
        yield client, srv, auth, harness


async def _start(client: httpx.AsyncClient, auth: dict[str, str]) -> dict[str, Any]:
    response = await client.post(
        f"/api/features/{FEATURE_ID}/run", headers=auth, json={"inputs": INPUTS}
    )
    assert response.status_code == 200, response.text
    return response.json()


async def _state(client: httpx.AsyncClient, auth: dict[str, str], task_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/features/{FEATURE_ID}/runs/{task_id}", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


async def _audit(client: httpx.AsyncClient, auth: dict[str, str], task_id: str) -> dict[str, Any]:
    response = await client.get(f"/api/features/{FEATURE_ID}/runs/{task_id}/audit", headers=auth)
    assert response.status_code == 200, response.text
    return response.json()


def _step(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    return next(step for step in state["steps"] if step["id"] == step_id)


def _artifact(state: dict[str, Any], step_id: str) -> Any:
    artifacts = _step(state, step_id)["artifacts"]
    assert artifacts, f"step {step_id} produced no artifact"
    return artifacts[0]["value"]


def _task_row(srv: OctopServer, task_id: str) -> Any:
    assert srv.services is not None
    return srv.services.repos.feature_tasks_repo.get(task_id)


HAPPY_SCRIPT: dict[str, list[str]] = {
    "STEP-1": [ANSWER_ROWS],
    "STEP-2": [ANSWER_CHECKED],
    "STEP-3": [ANSWER_FILLED],
    "STEP-4": [ANSWER_PASSED],
    "STEP-5": [ANSWER_PACKAGE],
}

DISPATCH_LATENCY = 0.05
"""How long one faked subagent takes — long enough for a real overlap to show."""

DISPATCH_ROLES = (
    ("engineering-engineering-code-reviewer", "选刀具"),
    ("engineering-engineering-backend-architect", "定工时"),
    ("engineering-engineering-data-engineer", "出 NC"),
)
"""The roles the double dispatches — subagent types as the ``task`` tool lists them."""


async def test_a_stepped_run_hands_typed_artifacts_between_steps_and_stops_at_the_gate(
    tmp_octop_home: Path,
) -> None:
    """The whole point of 7.2: a step consumes the previous step's *data*."""
    async with _stepped_env(tmp_octop_home, HAPPY_SCRIPT) as (client, srv, auth, harness):
        state = await _start(client, auth)

        assert state["status"] == "awaiting_gate"
        assert state["feature_id"] == FEATURE_ID
        assert [step["id"] for step in state["steps"]] == [step["id"] for step in _steps()]
        assert state["pending_gate"] == {
            "step_id": "fill_inferred",
            "name": "填充推断字段",
            "gate": "confirm",
            "allow_edit": True,
            "artifacts": [
                {"name": "filled_rows", "schema": "object", "value": json.loads(ANSWER_FILLED)}
            ],
        }
        # The gate stops the run: nothing after it has been attempted.
        assert harness.markers() == ["STEP-1", "STEP-2", "STEP-3"]
        assert _step(state, "deliver")["status"] == "pending"

        # Typed artifacts: the second step's prompt carries the first step's rows
        # as JSON, named by the artifact the first step declared.
        second_prompt = harness.prompts("STEP-2")[0]
        assert "bom_rows" in second_prompt
        assert _squash(json.dumps(ROWS_READ, ensure_ascii=False)) in _squash(second_prompt)
        # A step's tool whitelist rides this turn only (absent = inherit).
        scope = harness.requests("STEP-1")[0]["configurable"]["octop_feature_scope"]
        assert scope["tools_allowed"] == ["read_file"]
        assert "tools_allowed" not in harness.requests("STEP-2")[0].get("configurable", {}).get(
            "octop_feature_scope", {}
        )
        # The run is logged while it is still open — a gate is not a finish line.
        row = _task_row(srv, state["task_id"])
        assert row is not None and row.status == "running"

        approved = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert approved.status_code == 200, approved.text
        resumed = approved.json()

        # Same run, resumed — not a second one: the earlier steps kept their
        # artifacts and were never asked of the model again.
        assert resumed["task_id"] == state["task_id"]
        assert harness.markers() == ["STEP-1", "STEP-2", "STEP-3", "STEP-4", "STEP-5"]
        assert _artifact(resumed, "extract_l1") == ROWS_READ
        assert _artifact(resumed, "fill_inferred") == json.loads(ANSWER_FILLED)
        assert resumed["status"] == "succeeded"
        assert resumed["current_step"] is None
        assert resumed["pending_gate"] is None
        assert resumed["output"] == ANSWER_PACKAGE
        assert resumed["output_kind"] == "text"

        row = _task_row(srv, state["task_id"])
        assert row is not None and row.status == "succeeded" and row.draft == ANSWER_PACKAGE


async def test_a_step_that_cannot_decide_alone_escalates_and_a_human_correction_resumes_it(
    tmp_octop_home: Path,
) -> None:
    """``on_failure: escalate`` never guesses — the run waits with the reason."""
    correction = [[1, "螺栓", "M8", 4], [2, "垫片", "φ8", 9]]
    script = dict(HAPPY_SCRIPT)
    # The BOM and the assembly drawing disagree: a 3-column row is not the
    # declared table, and the step must not decide the conflict itself.
    script["STEP-2"] = [json.dumps([[1, "螺栓", "M8"], [2, "垫片", "φ8"]], ensure_ascii=False)]

    async with _stepped_env(tmp_octop_home, script) as (client, srv, auth, harness):
        state = await _start(client, auth)

        assert state["status"] == "escalated"
        assert state["pending_gate"]["gate"] == "escalate"
        assert state["pending_gate"]["step_id"] == "cross_check"
        step = _step(state, "cross_check")
        assert step["status"] == "escalated"
        assert "4" in (step["error"] or "") and "table" in (step["error"] or "")
        # Nothing past the conflict was attempted, and nothing was invented.
        assert harness.markers() == ["STEP-1", "STEP-2"]
        assert _step(state, "cross_check")["artifacts"] == []
        row = _task_row(srv, state["task_id"])
        assert row is not None and row.status == "running"

        approved = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={"edits": {"checked_rows": correction}},
        )
        assert approved.status_code == 200, approved.text
        resumed = approved.json()

        assert resumed["status"] == "awaiting_gate"
        assert _step(resumed, "cross_check")["status"] == "succeeded"
        assert _artifact(resumed, "cross_check") == correction
        # The human's value is what the next step was given, as data.
        third_prompt = harness.prompts("STEP-3")[0]
        assert "checked_rows" in third_prompt and '"φ8"' in third_prompt

        audit = await _audit(client, auth, state["task_id"])
        edits = _step(audit, "cross_check")["human_edits"]
        assert [
            (edit["artifact"], edit["kind"], edit["before"], edit["after"]) for edit in edits
        ] == [("checked_rows", "edit", None, correction)]
        assert edits[0]["by_user_id"] == await resolve_user_id(client, auth, "admin")


async def test_a_validate_gate_that_does_not_pass_is_never_delivered_and_rewind_corrects_it(
    tmp_octop_home: Path,
) -> None:
    """A failed check gate stops the delivery; the remedy is a corrected rerun."""
    corrected = {
        "rows": [[1, "螺栓", "M8", 4, "达克罗"], [2, "垫片", "φ8", 9, "达克罗"]],
        "l1_count": 2,
        "coating_fixed": True,
    }
    script = dict(HAPPY_SCRIPT)
    script["STEP-4"] = [ANSWER_FAILED, ANSWER_PASSED]

    async with _stepped_env(tmp_octop_home, script) as (client, srv, auth, harness):
        state = await _start(client, auth)
        approved = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert approved.status_code == 200, approved.text
        failed = approved.json()

        assert failed["status"] == "failed"
        assert _step(failed, "self_check")["status"] == "failed"
        assert "Coating 缺失" in (_step(failed, "self_check")["error"] or "")
        # 校验门不过不许交付: the delivery step never ran, and there is no output.
        assert harness.markers() == ["STEP-1", "STEP-2", "STEP-3", "STEP-4"]
        assert failed["output"] is None
        assert _step(failed, "deliver")["status"] == "pending"

        # The platform does not let a human approve past a check gate either.
        refused = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert refused.status_code == 409
        assert refused.json()["error"]["code"] == "FEATURE_RUN_NOT_AT_GATE"

        rewound = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/rewind",
            headers=auth,
            json={"to_step": "self_check", "edits": {"filled_rows": corrected}},
        )
        assert rewound.status_code == 200, rewound.text
        resumed = rewound.json()

        assert resumed["status"] == "succeeded"
        assert resumed["output"] == ANSWER_PACKAGE
        # 带修正重跑: the corrected artifact is what the re-run was given...
        assert '"coating_fixed"' in harness.prompts("STEP-4")[1]
        # ...and the change is in the audit, before and after.
        audit = await _audit(client, auth, state["task_id"])
        edits = _step(audit, "fill_inferred")["human_edits"]
        assert [(edit["kind"], edit["before"], edit["after"]) for edit in edits] == [
            ("edit", json.loads(ANSWER_FILLED), corrected)
        ]
        snapshot = audit["snapshot"]
        assert snapshot["agent_id"] and snapshot["feature_version"] == 1
        assert [entry["id"] for entry in snapshot["steps"]] == [step["id"] for step in _steps()]


async def test_rewind_voids_the_artifacts_of_every_later_step(
    tmp_octop_home: Path,
) -> None:
    """回退重跑: rerunning from step N throws away what N and later produced."""
    second_package = "已生成 BOM.xlsx（人工复核后 2 行 L1）"
    script = dict(HAPPY_SCRIPT)
    script["STEP-3"] = [ANSWER_FILLED, ANSWER_FILLED]
    script["STEP-4"] = [ANSWER_PASSED, ANSWER_PASSED]
    script["STEP-5"] = [ANSWER_PACKAGE, second_package]

    async with _stepped_env(tmp_octop_home, script) as (client, srv, auth, harness):
        state = await _start(client, auth)
        approved = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert approved.json()["status"] == "succeeded"

        rewound = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/rewind",
            headers=auth,
            json={"to_step": "fill_inferred"},
        )
        assert rewound.status_code == 200, rewound.text
        resumed = rewound.json()

        # The steps from the rewind point on ran again (a second turn each) and
        # the earlier ones did not — and the rerun stopped at the human gate again,
        # because that gate is part of the plan, not a one-time formality.
        assert harness.markers() == ["STEP-1", "STEP-2", "STEP-3", "STEP-4", "STEP-5", "STEP-3"]
        assert resumed["status"] == "awaiting_gate"
        assert resumed["pending_gate"]["step_id"] == "fill_inferred"

        approved_again = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert approved_again.status_code == 200, approved_again.text
        resumed = approved_again.json()
        assert harness.markers()[-2:] == ["STEP-4", "STEP-5"]
        assert resumed["status"] == "succeeded"
        assert resumed["output"] == second_package
        assert _step(resumed, "deliver")["status"] == "succeeded"

        # What the discarded artifacts held is kept in the audit, not on the step.
        audit = await _audit(client, auth, state["task_id"])
        voids = [
            edit
            for step in audit["steps"]
            for edit in step["human_edits"]
            if edit["kind"] == "void"
        ]
        assert [edit["artifact"] for edit in voids] == ["filled_rows", "verdict", "package"]
        assert voids[0]["before"] == json.loads(ANSWER_FILLED) and voids[0]["after"] is None
        assert voids[2]["before"] == ANSWER_PACKAGE


def _orchestrate_steps(
    *,
    max_parallel: int | None = 2,
    role: str | None = None,
    gate: str = "confirm",
    validate_step: bool = False,
) -> list[dict[str, Any]]:
    """The case's skeleton with its middle step — and optionally its check step —
    declared as the model's own to decompose (7.6's ``orchestrate``).

    The artifacts keep their names and types: what changes is *who* does the work,
    and a scenario that renamed its outputs at the same time would prove neither.
    """
    steps = _steps()
    decomposed: dict[str, Any] = {
        **steps[2],
        "mode": "orchestrate",
        "prompt": "STEP-3 把工艺包做出来。",
        "gate": gate,
    }
    decomposed.pop("max_parallel", None)
    if max_parallel is not None:
        decomposed["max_parallel"] = max_parallel
    if role is not None:
        decomposed["agent_role"] = role
    steps[2] = decomposed
    if validate_step:
        steps[3] = {**steps[3], "mode": "orchestrate", "prompt": "STEP-4 按自检清单验证。"}
    return steps


def _decomposition(state: dict[str, Any], step_id: str) -> dict[str, Any]:
    record = _step(state, step_id)["decomposition"]
    assert record is not None, f"step {step_id} recorded no decomposition"
    return record


async def test_a_decomposing_step_dispatches_subagents_in_parallel_under_the_ceiling(
    tmp_octop_home: Path,
) -> None:
    """7.6/7.7/7.8: the model decomposes, the platform caps it, the run remembers it.

    Three subagents are dispatched in one turn under a declared ceiling of two, so
    one of them has to queue for a slot — and the record has to say so. The step
    also keeps its human gate: decomposing changes who did the work, not whether a
    person still approves it.
    """
    script = {**HAPPY_SCRIPT, "STEP-3": [json.dumps({"plan": ["刀具", "工时", "NC"]})]}
    async with _stepped_env(
        tmp_octop_home,
        script,
        definition=_definition(steps=_orchestrate_steps(max_parallel=2)),
        dispatches={"STEP-3": list(DISPATCH_ROLES)},
    ) as (client, srv, auth, harness):
        state = await _start(client, auth)

        # The step ran, and its ``confirm`` gate still stopped the run there.
        assert _step(state, "fill_inferred")["status"] == "succeeded"
        assert state["status"] == "awaiting_gate"
        assert state["pending_gate"]["step_id"] == "fill_inferred"
        assert harness.dispatched == list(DISPATCH_ROLES)

        record = _decomposition(state, "fill_inferred")
        assert record["mode"] == "orchestrate"
        assert record["role"] is None
        assert record["declared"] == 2 and record["ceiling"] == 2
        # Three subagents, at most two at a time: exactly one waited for a slot.
        assert record["peak"] == 2
        assert record["waited"] == 1
        assert [entry["ordinal"] for entry in record["dispatches"]] == [0, 1, 2]
        assert all(entry["slots"] <= 2 for entry in record["dispatches"])
        assert {entry["role"] for entry in record["dispatches"]} == {
            role for role, _task in DISPATCH_ROLES
        }
        assert sum(1 for entry in record["dispatches"] if entry["waited"]) == 1
        # 「各自产出什么」 — the record answers it with the subagent's own answer.
        assert [entry["task"] for entry in record["dispatches"]] == [
            task for _role, task in DISPATCH_ROLES
        ]
        assert [entry["result"] for entry in record["dispatches"]] == [
            f"{role} 的产出：{task}" for role, task in DISPATCH_ROLES
        ]
        assert all(entry["status"] == "succeeded" for entry in record["dispatches"])
        assert all(not entry["truncated"] for entry in record["dispatches"])
        assert all(
            entry["ended_at"] is not None and entry["ended_at"] >= entry["started_at"]
            for entry in record["dispatches"]
        )

        # The audit reports the same record — one run, one truth, two endpoints.
        assert (
            _decomposition(await _audit(client, auth, state["task_id"]), "fill_inferred") == record
        )

        # And the rows are in the run's own log, not only in this response.
        assert srv.services is not None
        rows = srv.services.repos.feature_runs_repo.dispatches(state["task_id"])
        assert [(row.role, row.task, row.status) for row in rows] == [
            (role, task, "succeeded") for role, task in DISPATCH_ROLES
        ]

        # The turn really carried this step's ledger, and its prompt really told
        # the model that the step is its to split, with what ceiling.
        request = harness.requests("STEP-3")[0]
        assert feature_dispatch.dispatch_token(request["configurable"]) is not None
        prompt = harness.prompts("STEP-3")[0]
        assert "task" in prompt and "2" in prompt

        # Approving continues the same run, and the record is still there.
        approved = await client.post(
            f"/api/features/{FEATURE_ID}/runs/{state['task_id']}/approve",
            headers=auth,
            json={},
        )
        assert approved.status_code == 200, approved.text
        resumed = approved.json()
        assert resumed["status"] == "succeeded"
        assert resumed["output"] == ANSWER_PACKAGE
        assert _decomposition(resumed, "fill_inferred") == record


async def test_a_step_that_declares_no_ceiling_inherits_the_platform_default(
    tmp_octop_home: Path,
) -> None:
    """7.7's 「可配、可继承默认」: a step with no number still runs under a ceiling."""
    script = {**HAPPY_SCRIPT, "STEP-3": [json.dumps({"plan": ["刀具"]})]}
    async with _stepped_env(
        tmp_octop_home,
        script,
        definition=_definition(steps=_orchestrate_steps(max_parallel=None, gate="auto")),
        dispatches={"STEP-3": [DISPATCH_ROLES[0]]},
    ) as (client, _srv, auth, _harness):
        state = await _start(client, auth)

        record = _decomposition(state, "fill_inferred")
        assert record["declared"] is None
        assert record["ceiling"] == DEFAULT_MAX_PARALLEL
        assert record["peak"] == 1 and record["waited"] == 0
        assert state["status"] == "succeeded"


async def test_a_check_gate_still_refuses_delivery_when_the_step_decomposed(
    tmp_octop_home: Path,
) -> None:
    """The skeleton outranks the model: 校验门不过不许交付, decomposed or not."""
    script = {**HAPPY_SCRIPT, "STEP-4": [ANSWER_FAILED]}
    async with _stepped_env(
        tmp_octop_home,
        script,
        definition=_definition(steps=_orchestrate_steps(gate="auto", validate_step=True)),
        dispatches={"STEP-3": list(DISPATCH_ROLES), "STEP-4": [DISPATCH_ROLES[0]]},
    ) as (client, _srv, auth, _harness):
        state = await _start(client, auth)

        assert state["status"] == "failed"
        checked = _step(state, "self_check")
        assert checked["status"] == "failed"
        assert "did not pass its check gate" in checked["error"]
        # The check step dispatched a subagent before it failed its own gate, and
        # the record kept that: a refused delivery is still an auditable one.
        assert [entry["role"] for entry in _decomposition(state, "self_check")["dispatches"]] == [
            DISPATCH_ROLES[0][0]
        ]


async def test_an_agent_role_step_fails_loudly_when_it_never_ran_as_that_subagent(
    tmp_octop_home: Path,
) -> None:
    """``agent_role`` is part of the skeleton: the platform checks it was honoured.

    The turn dispatched a real subagent — just not the declared one. That is not
    the answer the definition asked for, so the step fails and says why, while the
    dispatch that *did* happen stays in the record.
    """
    role = DISPATCH_ROLES[0][0]
    other = DISPATCH_ROLES[1][0]
    script = {**HAPPY_SCRIPT, "STEP-3": [json.dumps({"plan": []})]}
    async with _stepped_env(
        tmp_octop_home,
        script,
        definition=_definition(steps=_orchestrate_steps(role=role, gate="auto")),
        dispatches={"STEP-3": [(other, "别的活")]},
    ) as (client, _srv, auth, _harness):
        state = await _start(client, auth)

        assert state["status"] == "failed"
        step = _step(state, "fill_inferred")
        assert step["status"] == "failed"
        assert role in step["error"] and other in step["error"]
        assert [
            entry["role"] for entry in _decomposition(state, "fill_inferred")["dispatches"]
        ] == [other]


async def test_a_step_that_declares_an_agent_role_runs_as_that_subagent(
    tmp_octop_home: Path,
) -> None:
    """The happy side of the same rule: the declared role is the one that ran."""
    role = DISPATCH_ROLES[0][0]
    script = {**HAPPY_SCRIPT, "STEP-3": [json.dumps({"plan": ["复核"]})]}
    async with _stepped_env(
        tmp_octop_home,
        script,
        definition=_definition(steps=_orchestrate_steps(role=role, gate="auto")),
        dispatches={"STEP-3": [(role, "复核工艺包")]},
    ) as (client, _srv, auth, _harness):
        state = await _start(client, auth)

        assert _step(state, "fill_inferred")["status"] == "succeeded"
        record = _decomposition(state, "fill_inferred")
        assert record["role"] == role
        assert [(entry["role"], entry["task"]) for entry in record["dispatches"]] == [
            (role, "复核工艺包")
        ]


async def test_a_run_that_could_not_dispatch_anything_is_refused_before_it_starts(
    tmp_octop_home: Path,
) -> None:
    """Without this, a decomposed step would quietly come back as one agent's answer."""
    definition = {
        **_definition(steps=_orchestrate_steps()),
        # The feature declares a subagent the caller's agent does not have, so the
        # run resolves to none — and step 3 is declared to need some.
        "agent": {"subagents": ["ghost/nobody"]},
    }
    async with _stepped_env(
        tmp_octop_home,
        HAPPY_SCRIPT,
        definition=definition,
        dispatches={"STEP-3": [DISPATCH_ROLES[0]]},
    ) as (client, srv, auth, harness):
        response = await client.post(
            f"/api/features/{FEATURE_ID}/run", headers=auth, json={"inputs": INPUTS}
        )

        assert response.status_code == 501, response.text
        error = response.json()["error"]
        assert error["code"] == "FEATURE_STEP_UNSUPPORTED"
        assert "fill_inferred" in error["message"]
        assert "ghost/nobody" in error["message"]
        # Nothing ran and no run was logged: a step that cannot dispatch is not a
        # degraded run, it is no run at all.
        assert harness.seen == []
        assert srv.services is not None
        assert srv.services.repos.feature_tasks_repo.list_for_feature(FEATURE_ID) == []


async def test_the_definition_round_trips_its_steps(tmp_octop_home: Path) -> None:
    """The editor has to read back exactly what it wrote — gates included."""
    async with _stepped_env(tmp_octop_home, HAPPY_SCRIPT) as (client, _srv, auth, _harness):
        detail = await client.get(f"/api/features/{FEATURE_ID}", headers=auth)

        assert detail.status_code == 200, detail.text
        assert detail.json()["steps"] == _steps()


@pytest.mark.parametrize(
    ("steps", "reason"),
    [
        ([{**_steps()[0], "gate": "whenever"}], "gate"),
        (
            [{**_steps()[0], "output": {"name": "bom_rows", "schema": "matrix"}}],
            "output.schema must be one of",
        ),
        ([{**_steps()[0], "max_parallel": 0}], "max_parallel"),
        ([{**_steps()[1], "inputs": ["nope"]}], "nope"),
    ],
)
async def test_a_step_the_engine_cannot_honour_is_refused_at_save_time(
    tmp_octop_home: Path, steps: list[dict[str, Any]], reason: str
) -> None:
    """Nothing is stored that the engine would have to guess about later."""
    async with octop_client(tmp_octop_home, fake_agent=FakeHarnessAgent()) as (client, srv):
        await bootstrap_admin(client, tmp_octop_home)
        auth = await auth_header(client)

        refused = await client.post("/api/features", headers=auth, json=_definition(steps=steps))

        assert refused.status_code == 400, refused.text
        error = refused.json()["error"]
        assert error["code"] == "FEATURE_INVALID"
        assert reason in json.dumps(error, ensure_ascii=False)
        assert srv.feature_catalog is not None
        assert srv.feature_catalog.get(FEATURE_ID) is None
