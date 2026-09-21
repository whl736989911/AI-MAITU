"""Unit tests for FeatureRunRepo and migration 024.

The repo is where a run's state has to survive a gate, so what these tests pin
down is exactly that: the row keeps which step it stopped at, the artifacts it
holds, and — for the audit — the value of every artifact a human changed, both
before and after.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_runs import FeatureRunRepo
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo
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
    with db.transaction() as conn:
        conn.execute("DELETE FROM feature_tasks WHERE id = ?", (task_id,))

    assert repo.get(task_id) is None
    assert repo.steps(task_id) == []
