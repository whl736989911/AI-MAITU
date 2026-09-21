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

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import httpx
import pytest

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
    """

    def __init__(self, script: dict[str, list[str]]) -> None:
        self._fake = FakeHarnessAgent()
        self.script = {marker: list(answers) for marker, answers in script.items()}
        self.seen: list[tuple[str, dict[str, Any]]] = []

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
        yield {"type": "token", "node": "agent", "content": answers.pop(0)}
        yield {"type": "state_snapshot", "data": {}}

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
) -> AsyncIterator[tuple[httpx.AsyncClient, OctopServer, dict[str, str], _ScriptedHarness]]:
    """A real server, an admin session, one stepped feature, one scripted harness."""
    harness = _ScriptedHarness(script)
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


async def test_orchestrate_mode_is_refused_rather_than_quietly_run_as_one_agent(
    tmp_octop_home: Path,
) -> None:
    """7.6's second mode is not built yet: the run says so, and runs nothing."""
    steps = _steps()
    steps[3] = {**steps[3], "mode": "orchestrate", "max_parallel": 8}

    async with _stepped_env(tmp_octop_home, HAPPY_SCRIPT, definition=_definition(steps=steps)) as (
        client,
        srv,
        auth,
        harness,
    ):
        response = await client.post(
            f"/api/features/{FEATURE_ID}/run", headers=auth, json={"inputs": INPUTS}
        )

        assert response.status_code == 501
        error = response.json()["error"]
        assert error["code"] == "FEATURE_STEP_UNSUPPORTED"
        assert "orchestrate" in error["message"] and "self_check" in error["message"]
        # Nothing ran, and no run was logged: an unimplemented mode is not a
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
