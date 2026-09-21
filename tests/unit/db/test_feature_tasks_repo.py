"""Unit tests for FeatureTaskRepo and migration 016."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_tasks import FINALIZED_STATUS, FeatureTaskRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


def _create_user(db: SqlitePool, username: str) -> int:
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users(username, password_hash, role, created_at) VALUES (?, ?, ?, 0)",
            (username, "h", "user"),
        )
        row = conn.execute("SELECT id FROM users WHERE username = ?", (username,)).fetchone()
    return int(row[0])


def _stamp(db: SqlitePool, stamps: list[tuple[int, str]]) -> None:
    """Pin ``created_at`` so newest-first ordering does not depend on wall-clock seconds."""
    with db.transaction() as conn:
        for created_at, task_id in stamps:
            conn.execute(
                "UPDATE feature_tasks SET created_at = ? WHERE id = ?",
                (created_at, task_id),
            )


def test_feature_task_create_and_get_roundtrip(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")
    row = repo.create(
        feature_id="quote-draft",
        user_id=user_id,
        inputs='{"customer": "ACME"}',
        status="succeeded",
        draft="# Quote",
    )
    assert row.id
    assert row.feature_id == "quote-draft"
    assert row.user_id == user_id
    assert row.inputs == '{"customer": "ACME"}'
    assert row.draft == "# Quote"
    assert row.final is None
    assert row.status == "succeeded"
    assert row.error is None
    assert row.created_at > 0

    assert repo.get(row.id) == row
    assert repo.get("missing-task") is None


def test_list_for_user_orders_newest_first_and_ignores_other_users(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    owner_id = _create_user(db, "owner")
    other_id = _create_user(db, "other")
    oldest = repo.create(
        feature_id="meeting-notes", user_id=owner_id, inputs="{}", status="succeeded"
    )
    middle = repo.create(
        feature_id="quote-draft", user_id=owner_id, inputs="{}", status="failed", error="boom"
    )
    newest = repo.create(
        feature_id="meeting-notes", user_id=owner_id, inputs="{}", status="succeeded"
    )
    repo.create(feature_id="meeting-notes", user_id=other_id, inputs="{}", status="succeeded")
    _stamp(db, [(100, oldest.id), (200, middle.id), (300, newest.id)])

    listed = repo.list_for_user(owner_id)
    assert [r.id for r in listed] == [newest.id, middle.id, oldest.id]
    assert listed[1].error == "boom"
    assert [r.id for r in repo.list_for_user(owner_id, limit=1)] == [newest.id]


def test_list_for_feature_orders_newest_first(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")
    first = repo.create(
        feature_id="meeting-notes", user_id=user_id, inputs="{}", status="succeeded"
    )
    second = repo.create(
        feature_id="meeting-notes", user_id=user_id, inputs="{}", status="succeeded"
    )
    repo.create(feature_id="quote-draft", user_id=user_id, inputs="{}", status="succeeded")
    _stamp(db, [(10, first.id), (20, second.id)])

    assert [r.id for r in repo.list_for_feature("meeting-notes")] == [second.id, first.id]
    assert repo.list_for_feature("missing-feature") == []


def test_feature_task_rows_cascade_with_user_delete(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")
    row = repo.create(feature_id="meeting-notes", user_id=user_id, inputs="{}", status="succeeded")
    with db.transaction() as conn:
        conn.execute("DELETE FROM users WHERE id = ?", (user_id,))

    assert repo.get(row.id) is None


def test_create_stores_no_capture_before_finalize(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")

    row = repo.create(
        feature_id="quote-draft", user_id=user_id, inputs="{}", status="succeeded", draft="甲\n乙"
    )

    assert row.diff_json is None
    assert row.finalized_at is None


def test_finalize_stamps_the_capture_columns(db: SqlitePool) -> None:
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")
    row = repo.create(
        feature_id="quote-draft", user_id=user_id, inputs="{}", status="succeeded", draft="甲\n乙"
    )

    finalized = repo.finalize(
        row.id,
        final="甲\n丙",
        diff_json='[{"op": "replace", "draft": "乙", "final": "丙"}]',
    )

    assert finalized is not None
    assert finalized.final == "甲\n丙"
    assert finalized.diff_json == '[{"op": "replace", "draft": "乙", "final": "丙"}]'
    assert finalized.status == FINALIZED_STATUS
    assert finalized.finalized_at is not None
    assert repo.get(row.id) == finalized


def test_finalize_is_one_way(db: SqlitePool) -> None:
    """A second submission must not overwrite the human's decision."""
    repo = FeatureTaskRepo(db)
    user_id = _create_user(db, "owner")
    row = repo.create(
        feature_id="quote-draft", user_id=user_id, inputs="{}", status="succeeded", draft="甲"
    )
    first = repo.finalize(row.id, final="甲\n乙", diff_json='[{"op": "add", "text": "乙"}]')
    assert first is not None

    assert repo.finalize(row.id, final="改第二次", diff_json="[]") is None
    assert repo.finalize("missing-task", final="x", diff_json="[]") is None

    stored = repo.get(row.id)
    assert stored is not None
    assert stored.final == "甲\n乙"
    assert stored.finalized_at == first.finalized_at
