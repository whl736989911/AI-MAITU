"""Unit tests for FeatureRunRepo and migrations 024–025.

The repo is where a run's state has to survive a gate, so what these tests pin
down is exactly that: the row keeps which step it stopped at, the artifacts it
holds, and — for the audit — the value of every artifact a human changed, both
before and after. Migration 025 adds what a step turn dispatched to its subagents
(7.8's 分解留痕), which is written once and read back as it was observed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_runs import FeatureRunRepo
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo
from octop.infra.features.dispatch import DispatchEntry
from octop.infra.features.steps import STEP_SUCCEEDED, STEP_VOIDED, Artifact

_PLAN = [
    {
        "id": "extract_l1",
        "name": "提取 L1 项",
        "mode": "agent",
        "inputs": [],
        "output": {"name": "bom_rows", "schema": "table:4cols"},
        "prompt": "读 BOM。",
        "gate": "auto",
        "on_failure": "abort",
    },
    {
        "id": "report",
        "name": "摘要报告",
        "mode": "agent",
        "inputs": ["bom_rows"],
        "output": {"name": "summary", "schema": "object"},
        "prompt": "写摘要。",
        "gate": "confirm",
        "allow_edit": True,
        "on_failure": "abort",
    },
]
_ROWS = [[1, "螺栓", "M8", 4]]


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def _user_id(db: SqlitePool, username: str = "owner") -> int:
    with db.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 0)",
            (username, "h", "user"),
        )
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return int(row[0])


def _started(db: SqlitePool, user_id: int) -> tuple[FeatureRunRepo, str]:
    """A logged task plus the run row and step rows that go with it."""
    task = FeatureTaskRepo(db).create(
        feature_id="bom-extraction", user_id=user_id, inputs="{}", status="running"
    )
    repo = FeatureRunRepo(db)
    repo.create(
        task_id=task.id,
        feature_id="bom-extraction",
        user_id=user_id,
        status="running",
        plan=_PLAN,
        snapshot={"agent_id": "agent-1", "steps": [step["id"] for step in _PLAN]},
        step_ids=[str(step["id"]) for step in _PLAN],
    )
    return repo, task.id


def _rows(repo: FeatureRunRepo, task_id: str) -> list[tuple[str, str, int]]:
    return [(row.step_id, row.status, row.attempts) for row in repo.steps(task_id)]


def _dispatch(
    role: str,
    task: str,
    *,
    status: str = "succeeded",
    error: str | None = None,
    result: str | None = "rows=4",
    truncated: bool = False,
    waited: bool = False,
    waited_ms: int = 0,
    slots: int = 1,
    started_at: int = 1_700_000_000,
    ended_at: int | None = 1_700_000_002,
) -> DispatchEntry:
    """One dispatch as the boundary records it; a test names only the fields it pins."""
    return DispatchEntry(
        role=role,
        task=task,
        status=status,
        error=error,
        result=result,
        truncated=truncated,
        waited=waited,
        waited_ms=waited_ms,
        slots=slots,
        started_at=started_at,
        ended_at=ended_at,
    )


def test_a_run_starts_with_every_step_pending(db: SqlitePool) -> None:
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    run = repo.get(task_id)
    assert run is not None
    assert run.status == "running"
    assert run.pending_gate is None
    assert run.snapshot_payload()["agent_id"] == "agent-1"
    assert _rows(repo, task_id) == [
        ("extract_l1", "pending", 0),
        ("report", "pending", 0),
    ]


def test_a_run_the_database_does_not_have_is_none(db: SqlitePool) -> None:
    assert FeatureRunRepo(db).get("missing") is None


def test_an_artifact_round_trips_as_the_data_it_is(db: SqlitePool) -> None:
    """A step hands the *value* on, not the text it was written in."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.start_step(task_id, 0)
    repo.finish_step(
        task_id,
        0,
        status=STEP_SUCCEEDED,
        artifact=Artifact(name="bom_rows", schema="table:4cols", value=_ROWS, step_id="extract_l1"),
        error=None,
    )

    row = repo.steps(task_id)[0]
    assert row.status == STEP_SUCCEEDED
    assert row.attempts == 1
    assert row.started_at is not None and row.ended_at is not None
    artifact = row.artifact()
    assert artifact is not None
    assert artifact.value == _ROWS and artifact.schema == "table:4cols"


def test_a_failed_step_keeps_its_reason_and_no_artifact(db: SqlitePool) -> None:
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.start_step(task_id, 0)
    repo.finish_step(task_id, 0, status="escalated", artifact=None, error="冲突待人工决定")

    row = repo.steps(task_id)[0]
    assert row.status == "escalated"
    assert row.error == "冲突待人工决定"
    assert row.artifact() is None


def test_rewinding_voids_the_artifact_but_keeps_the_attempt_count(db: SqlitePool) -> None:
    """作废的是产物，不是"这一步跑过几次"——the run spent those turns."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)
    repo.start_step(task_id, 0)
    repo.finish_step(
        task_id,
        0,
        status=STEP_SUCCEEDED,
        artifact=Artifact(name="bom_rows", schema="table:4cols", value=_ROWS, step_id="extract_l1"),
        error=None,
    )

    repo.void_steps_from(task_id, 0)

    row = repo.steps(task_id)[0]
    assert row.status == STEP_VOIDED
    assert row.artifact() is None
    assert row.started_at is None and row.ended_at is None
    assert row.attempts == 1


def test_voiding_leaves_the_steps_before_the_target_alone(db: SqlitePool) -> None:
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)
    for seq in (0, 1):
        repo.start_step(task_id, seq)
        repo.finish_step(
            task_id,
            seq,
            status=STEP_SUCCEEDED,
            artifact=Artifact(
                name=f"artifact_{seq}", schema="list", value=[seq], step_id=_PLAN[seq]["id"]
            ),
            error=None,
        )

    repo.void_steps_from(task_id, 1)

    assert _rows(repo, task_id) == [("extract_l1", STEP_SUCCEEDED, 1), ("report", STEP_VOIDED, 1)]
    assert repo.steps(task_id)[0].artifact().value == [0]


def test_a_human_edit_is_recorded_before_and_after(db: SqlitePool) -> None:
    """7.9's requirement: 改前改后都在，attributed to whoever wrote it."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.record_edit(
        task_id,
        step_id="extract_l1",
        artifact="bom_rows",
        before=_ROWS,
        after=[[1, "螺栓", "M8", 8]],
        by_user_id=user_id,
        kind="edit",
        source="rewind",
    )

    (edit,) = repo.edits(task_id)
    assert (edit.artifact, edit.kind, edit.source) == ("bom_rows", "edit", "rewind")
    assert edit.before() == _ROWS
    assert edit.after() == [[1, "螺栓", "M8", 8]]
    assert edit.by_user_id == user_id
    assert edit.created_at > 0


def test_a_void_is_recorded_with_what_it_threw_away(db: SqlitePool) -> None:
    """The discarded value survives the rewind, or the audit cannot explain it."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.record_edit(
        task_id,
        step_id="report",
        artifact="summary",
        before={"l1_count": 2},
        after=None,
        by_user_id=user_id,
        kind="void",
        source="rewind",
    )

    (edit,) = repo.edits(task_id)
    assert edit.kind == "void" and edit.after() is None
    assert edit.before() == {"l1_count": 2}


def test_a_step_turn_records_the_subagents_it_dispatched(db: SqlitePool) -> None:
    """7.8's 分解留痕: what a step split itself into, in the order it dispatched.

    The rows are read back and never rewritten, so the round-trip has to be exact:
    the flags come back as booleans, each row still says which step turn it belongs
    to, and a dispatch with no end keeps its NULL rather than a time nobody saw.
    """
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.record_dispatches(
        task_id,
        0,
        step_id="extract_l1",
        entries=[
            # Written first, dispatched second: the read order is when each subagent
            # went out, not the order the rows landed in.
            _dispatch(
                "extractor",
                "读 BOM 前 100 行",
                truncated=True,
                slots=1,
                started_at=1_700_000_001,
                ended_at=1_700_000_004,
            ),
            _dispatch(
                "verifier",
                "复核数量",
                status="failed",
                error="RuntimeError: 子 agent 超时",
                result=None,
                waited=True,
                waited_ms=812,
                slots=2,
                started_at=1_700_000_000,
                ended_at=None,
            ),
        ],
    )

    rows = repo.dispatches(task_id)
    assert [row.role for row in rows] == ["verifier", "extractor"]
    first, second = rows
    assert (first.task_id, first.seq, first.step_id) == (task_id, 0, "extract_l1")
    assert (first.task, first.status, first.error, first.result) == (
        "复核数量",
        "failed",
        "RuntimeError: 子 agent 超时",
        None,
    )
    assert (first.truncated, first.waited) == (False, True)
    assert (first.waited_ms, first.slots) == (812, 2)
    assert first.started_at == 1_700_000_000
    assert first.ended_at is None, "a dispatch that never came back has no end time"
    assert first.created_at > 0
    assert (second.status, second.truncated, second.waited) == ("succeeded", True, False)
    assert second.result == "rows=4"
    assert (second.started_at, second.ended_at) == (1_700_000_001, 1_700_000_004)

    # A turn that dispatched nothing writes nothing: the absence is the record.
    repo.record_dispatches(task_id, 1, step_id="report", entries=[])
    assert [row.role for row in repo.dispatches(task_id)] == ["verifier", "extractor"]


def test_the_run_state_only_moves_where_it_is_told(db: SqlitePool) -> None:
    """UNSET means "leave it": current_step and the gate are not one value."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.update(task_id, status="awaiting_gate", current_step="report", current_seq=1)
    repo.update(task_id, status="running")

    run = repo.get(task_id)
    assert run is not None
    assert (run.status, run.current_step, run.current_seq) == ("running", "report", 1)

    repo.update(task_id, status="succeeded", current_step=None, pending_gate=None)
    run = repo.get(task_id)
    assert run is not None and run.current_step is None


def test_a_run_can_be_claimed_once_per_state(db: SqlitePool) -> None:
    """The UPDATE is the guard: two approvals cannot both walk the run on."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)
    repo.update(task_id, status="awaiting_gate", current_step="report", current_seq=1)

    first = repo.claim(task_id, expected_status="awaiting_gate", new_status="running")
    second = repo.claim(task_id, expected_status="awaiting_gate", new_status="running")

    assert first is True
    assert second is False, "the second action must lose, or a step runs twice"
    run = repo.get(task_id)
    assert run is not None and run.status == "running"


def test_a_gate_is_stored_as_the_request_that_opened_it(db: SqlitePool) -> None:
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)

    repo.update(
        task_id,
        status="awaiting_gate",
        pending_gate={"step_id": "report", "gate": "confirm", "allow_edit": True},
    )

    run = repo.get(task_id)
    assert run is not None
    assert run.pending_gate_payload() == {
        "step_id": "report",
        "gate": "confirm",
        "allow_edit": True,
    }


def test_deleting_the_task_takes_the_run_with_it(db: SqlitePool) -> None:
    """A run is part of the task log: the two cannot drift apart."""
    user_id = _user_id(db)
    repo, task_id = _started(db, user_id)
    repo.record_dispatches(
        task_id, 0, step_id="extract_l1", entries=[_dispatch("extractor", "读 BOM 前 100 行")]
    )
    with db.transaction() as conn:
        conn.execute("DELETE FROM feature_tasks WHERE id = ?", (task_id,))

    assert repo.get(task_id) is None
    assert repo.steps(task_id) == []
    assert repo.dispatches(task_id) == []
