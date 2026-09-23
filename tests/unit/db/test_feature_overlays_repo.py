"""Unit tests for FeatureOverlayRepo — one caller's own text on one feature."""

from __future__ import annotations

from pathlib import Path

import pytest

from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_overlays import FeatureOverlayRepo
from octop.infra.db.repos.users import UserRepo


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def repo(db: SqlitePool) -> FeatureOverlayRepo:
    return FeatureOverlayRepo(db)


@pytest.fixture
def caller(db: SqlitePool) -> int:
    return UserRepo(db).create(username="caller", password_hash="h", role="user")


def test_the_table_is_part_of_the_current_schema(db: SqlitePool) -> None:
    with db.connect() as conn:
        names = {
            r["name"] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        version = conn.execute("SELECT version FROM _schema_version").fetchone()[0]
        columns = {
            r["name"] for r in conn.execute("PRAGMA table_info(feature_user_overlays)").fetchall()
        }
    assert "feature_user_overlays" in names
    assert version == 36
    assert columns == {"feature_id", "user_id", "content", "updated_at"}


def test_a_caller_without_an_overlay_reads_as_none(repo: FeatureOverlayRepo, caller: int) -> None:
    assert repo.get(feature_id="quote-helper", user_id=caller) is None
    assert repo.content(feature_id="quote-helper", user_id=caller) == ""


def test_setting_an_overlay_replaces_it_rather_than_appending(
    repo: FeatureOverlayRepo, caller: int
) -> None:
    first = repo.set(feature_id="quote-helper", user_id=caller, content="含税")
    second = repo.set(feature_id="quote-helper", user_id=caller, content="含税，且不要写交期")

    assert first.content == "含税"
    assert second.content == "含税，且不要写交期"
    assert repo.count_for_feature(feature_id="quote-helper") == 1
    assert repo.content(feature_id="quote-helper", user_id=caller) == "含税，且不要写交期"


def test_two_callers_keep_their_own_overlay_on_one_feature(
    db: SqlitePool, repo: FeatureOverlayRepo, caller: int
) -> None:
    other = UserRepo(db).create(username="other", password_hash="h", role="user")
    repo.set(feature_id="quote-helper", user_id=caller, content="我的话")
    repo.set(feature_id="quote-helper", user_id=other, content="他的话")

    assert repo.content(feature_id="quote-helper", user_id=caller) == "我的话"
    assert repo.content(feature_id="quote-helper", user_id=other) == "他的话"
    assert repo.count_for_feature(feature_id="quote-helper") == 2


def test_one_caller_keeps_overlays_on_different_features_apart(
    repo: FeatureOverlayRepo, caller: int
) -> None:
    repo.set(feature_id="quote-helper", user_id=caller, content="报价的规矩")

    assert repo.content(feature_id="minutes", user_id=caller) == ""
    assert repo.count_for_feature(feature_id="minutes") == 0
    assert repo.count_for_feature(feature_id="quote-helper") == 1


def test_deleting_is_idempotent(repo: FeatureOverlayRepo, caller: int) -> None:
    repo.set(feature_id="quote-helper", user_id=caller, content="含税")

    repo.delete(feature_id="quote-helper", user_id=caller)
    repo.delete(feature_id="quote-helper", user_id=caller)

    assert repo.get(feature_id="quote-helper", user_id=caller) is None
