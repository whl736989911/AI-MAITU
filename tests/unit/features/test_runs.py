"""Unit tests for the run engine's refusals — the rules it never bends.

The gates, the artifacts and the rerun paths are exercised end to end in
``tests/integration/test_feature_steps_api.py``. What lives here is the small set
of rules that must hold *whatever* the caller sends: a human write lands only
where the definition allows one, and a run that already moved on cannot be
decided on twice.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_runs import FeatureRunRepo
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo
from octop.infra.features.runs import (
    EDIT_SOURCE_REWIND,
    FeatureRunEngine,
    RunEditInvalid,
    RunNotAtGate,
    RunState,
    RunTaken,
)


def _step(step_id: str, artifact: str, *, allow_edit: bool = False) -> dict[str, Any]:
    node: dict[str, Any] = {
        "id": step_id,
        "name": step_id,
        "mode": "agent",
        "inputs": [],
        "output": {"name": artifact, "schema": "list"},
        "prompt": f"run {step_id}",
        "gate": "auto",
        "on_failure": "abort",
    }
    if allow_edit:
        node["allow_edit"] = True
    return node


_PLAN = [_step("first", "rows"), _step("second", "summary")]


@pytest.fixture
def engine(tmp_path: Path) -> tuple[FeatureRunEngine, FeatureRunRepo, str, int]:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 0)",
            ("owner", "h", "user"),
        )
        user_id = int(conn.execute("SELECT id FROM users WHERE username = 'owner'").fetchone()[0])
    task = FeatureTaskRepo(pool).create(
        feature_id="bom-extraction", user_id=user_id, inputs="{}", status="running"
    )
    repo = FeatureRunRepo(pool)
    repo.create(
        task_id=task.id,
        feature_id="bom-extraction",
        user_id=user_id,
        status="running",
        plan=_PLAN,
        snapshot={},
        step_ids=[str(step["id"]) for step in _PLAN],
    )
    return FeatureRunEngine(repo, FeatureTaskRepo(pool)), repo, task.id, user_id


async def _never_called(*_args: Any, **_kwargs: Any) -> str:
    raise AssertionError("no step should have been attempted")


def _state(engine: FeatureRunEngine, task_id: str) -> RunState:
    state = engine.state(task_id)
    assert state is not None
    return state


async def test_a_rewind_edit_lands_only_where_the_definition_allows_one(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    """A step declared without ``allow_edit`` keeps its artifact — loudly.

    Otherwise "correct the previous step's output" would be a way to rewrite any
    step of any run, and the flag would mean nothing.
    """
    runs_engine, repo, task_id, user_id = engine

    with pytest.raises(RunEditInvalid) as excinfo:
        await runs_engine.rewind(
            _state(runs_engine, task_id),
            to_step="second",
            edits={"rows": [[1]]},
            by_user_id=user_id,
            run_turn=_never_called,
        )

    assert "'first'" in str(excinfo.value) and "does not allow edits" in str(excinfo.value)
    # Nothing was written, and the run did not move on: a refused request leaves
    # the run exactly where it was.
    run = repo.get(task_id)
    assert run is not None and run.status == "running"
    assert repo.edits(task_id) == []


async def test_a_rewind_edit_is_refused_when_the_step_it_names_is_voided(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    """The correction is an *input* of the target step, not one of its own outputs."""
    runs_engine, repo, task_id, user_id = engine
    repo.start_step(task_id, 0)
    repo.finish_step(task_id, 0, status="succeeded", artifact=None, error="lost")
    if repo.steps(task_id)[0].artifact() is None:
        from octop.infra.features.steps import Artifact

        repo.finish_step(
            task_id,
            0,
            status="succeeded",
            artifact=Artifact(name="rows", schema="list", value=[[1]], step_id="first"),
            error=None,
        )

    with pytest.raises(RunEditInvalid) as excinfo:
        await runs_engine.rewind(
            _state(runs_engine, task_id),
            to_step="first",
            edits={"rows": [[2]]},
            by_user_id=user_id,
            run_turn=_never_called,
        )

    assert "voids" in str(excinfo.value)
    assert repo.claim(task_id, expected_status="running", new_status="running") is True


async def test_an_approval_loses_when_the_run_already_moved_on(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    """Two approvals of one gate must not both walk the run on."""
    runs_engine, repo, task_id, user_id = engine
    repo.update(
        task_id,
        status="awaiting_gate",
        current_step="second",
        current_seq=1,
        pending_gate={"step_id": "second", "gate": "confirm", "allow_edit": False},
    )
    # The other approval was decided on this same state and got there first.
    stale = _state(runs_engine, task_id)
    assert repo.claim(task_id, expected_status="awaiting_gate", new_status="running") is True

    with pytest.raises(RunTaken):
        await runs_engine.approve(
            stale,
            edits=None,
            by_user_id=user_id,
            run_turn=_never_called,
        )


async def test_approving_a_run_that_is_not_waiting_is_refused(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    runs_engine, _repo, task_id, user_id = engine

    with pytest.raises(RunNotAtGate, match="not waiting at a gate"):
        await runs_engine.approve(
            _state(runs_engine, task_id),
            edits=None,
            by_user_id=user_id,
            run_turn=_never_called,
        )


async def test_a_run_that_is_not_in_the_audit_log_is_not_a_run(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    runs_engine, _repo, _task_id, _user_id = engine

    assert runs_engine.state("no-such-run") is None


def test_the_engine_reads_its_plan_from_the_run_not_from_a_catalog(
    engine: tuple[FeatureRunEngine, FeatureRunRepo, str, int],
) -> None:
    """A run is audited as it ran: the frozen plan is the only plan it has."""
    runs_engine, _repo, task_id, _user_id = engine

    state = _state(runs_engine, task_id)

    assert [step.id for step in state.plan] == ["first", "second"]
    assert [(step.allow_edit, step.output.name) for step in state.plan] == [
        (False, "rows"),
        (False, "summary"),
    ]
    assert EDIT_SOURCE_REWIND == "rewind"
