"""Unit tests for FeatureCaseRepo and migration 019's case table."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_cases import FeatureCaseRepo
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def _user(db: SqlitePool, username: str) -> int:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 0)",
            (username, "h", "user"),
        )
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return int(row[0])


def _finalized_task(
    db: SqlitePool,
    user_id: int,
    *,
    feature_id: str = "quote-draft",
    final: str = "甲\n丙",
) -> str:
    """One finalized run, ready to be promoted."""
    repo = FeatureTaskRepo(db)
    row = repo.create(
        feature_id=feature_id,
        user_id=user_id,
        inputs='{"customer": "ACME"}',
        status="succeeded",
        draft="甲\n乙",
    )
    assert repo.finalize(
        row.id, final=final, diff_json='[{"op": "replace", "draft": "乙", "final": "丙"}]'
    )
    return row.id


def test_promote_reads_the_content_through_the_task_reference(db: SqlitePool) -> None:
    user_id = _user(db, "owner")
    task_id = _finalized_task(db, user_id)
    repo = FeatureCaseRepo(db)

    case = repo.promote(task_id, feature_id="quote-draft", promoted_by=user_id, note="客户名写全称")

    assert case is not None
    assert case.task_id == task_id
    assert case.feature_id == "quote-draft"
    assert case.promoted_by == user_id
    assert case.promoted_at > 0
    assert case.note == "客户名写全称"
    assert case.inputs == '{"customer": "ACME"}'
    assert case.final == "甲\n丙"
    assert case.diff_json == '[{"op": "replace", "draft": "乙", "final": "丙"}]'
    assert repo.get(task_id) == case


def test_promote_keeps_the_first_judgement(db: SqlitePool) -> None:
    """Re-promoting must not rewrite who promoted it, when, or why."""
    user_id = _user(db, "owner")
    other_id = _user(db, "other")
    task_id = _finalized_task(db, user_id)
    repo = FeatureCaseRepo(db)
    first = repo.promote(task_id, feature_id="quote-draft", promoted_by=user_id, note="范例")

    again = repo.promote(task_id, feature_id="quote-draft", promoted_by=other_id, note="改主意")

    assert again == first
    assert again is not None
    assert again.promoted_by == user_id
    assert again.note == "范例"


def test_promote_requires_an_existing_task(db: SqlitePool) -> None:
    user_id = _user(db, "owner")

    promoted = FeatureCaseRepo(db).promote(
        "missing-task", feature_id="quote-draft", promoted_by=user_id
    )

    assert promoted is None


def test_list_for_feature_is_newest_first_and_scoped(db: SqlitePool) -> None:
    user_id = _user(db, "owner")
    older = _finalized_task(db, user_id)
    newer = _finalized_task(db, user_id)
    other = _finalized_task(db, user_id, feature_id="meeting-notes")
    repo = FeatureCaseRepo(db)
    repo.promote(older, feature_id="quote-draft", promoted_by=user_id)
    repo.promote(newer, feature_id="quote-draft", promoted_by=user_id)
    repo.promote(other, feature_id="meeting-notes", promoted_by=user_id)
    with db.transaction() as conn:
        conn.execute("UPDATE feature_cases SET promoted_at = 100 WHERE task_id = ?", (older,))
        conn.execute("UPDATE feature_cases SET promoted_at = 200 WHERE task_id = ?", (newer,))

    assert [c.task_id for c in repo.list_for_feature("quote-draft")] == [newer, older]
    assert [c.task_id for c in repo.list_for_feature("meeting-notes")] == [other]
    assert repo.list_for_feature("unknown-feature") == []


def test_case_rows_cascade_with_task_delete(db: SqlitePool) -> None:
    user_id = _user(db, "owner")
    task_id = _finalized_task(db, user_id)
    repo = FeatureCaseRepo(db)
    assert repo.promote(task_id, feature_id="quote-draft", promoted_by=user_id) is not None

    with db.transaction() as conn:
        conn.execute("DELETE FROM feature_tasks WHERE id = ?", (task_id,))

    assert repo.get(task_id) is None
    assert repo.list_for_feature("quote-draft") == []
