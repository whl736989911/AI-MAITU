"""Unit tests for FeatureChangeRepo — the record an undo is rebuilt from."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_workflow_changes import (
    STATUS_APPLIED,
    TARGET_DEFINITION,
    TARGET_OVERLAY,
    FeatureChangeRepo,
)
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> FeatureChangeRepo:
    return FeatureChangeRepo(db)


@pytest.fixture
def author(db: SqlitePool) -> int:
    return UserRepo(db).create(username="author", password_hash="h", role="user")


def test_a_recorded_change_keeps_its_diff(repo: FeatureChangeRepo, author: int) -> None:
    items = [
        {"path": "/rules/1", "before": None, "after": "金额逐行核对"},
        {"path": "/steps/1/gate", "before": "auto", "after": "confirm"},
    ]

    row = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="新增 1 条规则，第 2 步改为确认门",
        items=items,
        run_id="run-1",
    )

    assert row.status == STATUS_APPLIED
    assert row.reverted_at is None
    assert row.run_id == "run-1"
    assert row.items == items
    assert repo.get(row.id) == row


def test_definition_and_overlay_changes_are_private_to_their_authors(
    db: SqlitePool, repo: FeatureChangeRepo, author: int
) -> None:
    other = UserRepo(db).create(username="other", password_hash="h", role="user")
    definition_change = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="作者改的定义",
        items=[{"path": "/rules/-", "before": None, "after": "x"}],
    )
    overlays = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_OVERLAY,
        summary="我的差异",
        items=[{"path": "/overlay", "before": None, "after": "我们含税"}],
    )

    visible_to_other = repo.list_for_feature(feature_id="quote-helper", user_id=other)
    assert visible_to_other == []

    visible_to_author = repo.list_for_feature(feature_id="quote-helper", user_id=author)
    assert {row.id for row in visible_to_author} == {definition_change.id, overlays.id}


def test_changes_are_listed_newest_first(repo: FeatureChangeRepo, author: int) -> None:
    first = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="第一次",
        items=[{"path": "/rules/-", "before": None, "after": "a"}],
    )
    second = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="第二次",
        items=[{"path": "/rules/-", "before": None, "after": "b"}],
    )

    listed = repo.list_for_feature(feature_id="quote-helper", user_id=author)

    assert [row.id for row in listed] == [second.id, first.id]


def test_reverting_marks_the_change_without_losing_it(repo: FeatureChangeRepo, author: int) -> None:
    row = repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="会被撤销的一次",
        items=[{"path": "/rules/-", "before": None, "after": "x"}],
    )

    repo.mark_reverted(row.id)

    reverted = repo.get(row.id)
    assert reverted is not None
    assert reverted.is_reverted
    assert reverted.reverted_at is not None
    # The diff survives the undo: the history is the record of what happened.
    assert reverted.items == row.items


def test_deleting_a_feature_takes_its_changes(repo: FeatureChangeRepo, author: int) -> None:
    repo.record(
        feature_id="quote-helper",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="quote",
        items=[{"path": "/rules/-", "before": None, "after": "x"}],
    )
    repo.record(
        feature_id="minutes",
        user_id=author,
        target=TARGET_DEFINITION,
        summary="minutes",
        items=[{"path": "/rules/-", "before": None, "after": "y"}],
    )

    repo.delete_for_feature("quote-helper")

    assert repo.list_for_feature(feature_id="quote-helper", user_id=author) == []
    assert repo.list_for_feature(feature_id="minutes", user_id=author) != []
