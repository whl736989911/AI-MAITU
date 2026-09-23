"""Unit tests for FeatureRunRepo — one run's evidence, written once."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_runs import FeatureRunRepo
from octop.infra.db.repos.users import UserRepo

_DEFINITION = {"version": 1, "status": "active", "steps": [{"id": "draft", "name": "起草"}]}


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> FeatureRunRepo:
    return FeatureRunRepo(db)


@pytest.fixture
def caller(db: SqlitePool) -> int:
    return UserRepo(db).create(username="caller", password_hash="h", role="user")


def _record(repo: FeatureRunRepo, caller: int, **overrides: object):
    payload: dict[str, object] = {
        "feature_id": "quote-helper",
        "agent_id": "feat-quote-helper",
        "user_id": caller,
        "thread_id": "thr-1",
        "inputs": {"customer_name": "ACME"},
        "definition": _DEFINITION,
    }
    payload.update(overrides)
    return repo.record(**payload)  # type: ignore[arg-type]


def test_a_recorded_run_keeps_its_inputs_and_its_definition(
    repo: FeatureRunRepo, caller: int
) -> None:
    row = _record(repo, caller)

    assert row.id
    assert row.feature_id == "quote-helper"
    assert row.agent_id == "feat-quote-helper"
    assert row.user_id == caller
    assert row.thread_id == "thr-1"
    assert row.inputs == {"customer_name": "ACME"}
    assert row.definition == _DEFINITION
    assert repo.get(row.id) == row


def test_a_run_without_values_still_records_the_run(repo: FeatureRunRepo, caller: int) -> None:
    """A submitted card with nothing filled in is a run, not an absence of one."""
    row = _record(repo, caller, inputs={}, thread_id=None)

    assert row.inputs == {}
    assert row.thread_id is None


def test_runs_are_listed_newest_first_and_only_for_their_own_caller(
    db: SqlitePool, repo: FeatureRunRepo, caller: int
) -> None:
    other = UserRepo(db).create(username="other", password_hash="h", role="user")
    first = _record(repo, caller, inputs={"n": 1})
    second = _record(repo, caller, inputs={"n": 2})
    _record(repo, other, inputs={"n": 3})
    _record(repo, caller, feature_id="minutes", inputs={"n": 4})

    mine = repo.list_for(feature_id="quote-helper", user_id=caller)
    assert [row.id for row in mine] == [second.id, first.id]
    assert [row.inputs for row in mine] == [{"n": 2}, {"n": 1}]
    assert repo.list_for(feature_id="quote-helper", user_id=other) != []


def test_the_snapshot_is_the_definition_as_it_stood(repo: FeatureRunRepo, caller: int) -> None:
    """Editing a workflow afterwards must not rewrite what an earlier run ran under."""
    row = _record(repo, caller)
    edited = dict(_DEFINITION, status="draft")

    assert repo.get(row.id).definition == _DEFINITION  # type: ignore[union-attr]
    assert edited != _DEFINITION


def test_deleting_a_feature_takes_its_runs(repo: FeatureRunRepo, caller: int) -> None:
    _record(repo, caller)
    _record(repo, caller, feature_id="minutes")

    repo.delete_for_feature("quote-helper")

    assert repo.list_for(feature_id="quote-helper", user_id=caller) == []
    assert repo.list_for(feature_id="minutes", user_id=caller) != []
