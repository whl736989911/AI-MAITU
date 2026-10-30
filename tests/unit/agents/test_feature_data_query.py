"""Structured filters and sorting for feature data lists (validation + SQL)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from deepagents.backends.local_shell import LocalShellBackend
from harness_agent.backends.workspace import BackendWorkspace

from octop.infra.agents import feature_data as fd
from octop.infra.agents import feature_data_query as fdq
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_data import FeatureDataRepo


def _schema() -> dict[str, Any]:
    return {
        "version": 1,
        "objects": [
            {
                "key": "deal",
                "kind": "ledger",
                "title_field": "name",
                "fields": [
                    {"key": "name", "type": "text", "required": True},
                    {"key": "weight", "type": "integer"},
                    {"key": "score", "type": "number"},
                    {"key": "made_on", "type": "date"},
                    {"key": "occurred_at", "type": "datetime"},
                    {"key": "active", "type": "boolean"},
                    {"key": "stage", "type": "enum", "options": ["draft", "won"]},
                    {"key": "owner", "type": "user"},
                    {"key": "unit", "type": "org_unit"},
                ],
            }
        ],
    }


def _obj() -> dict[str, Any]:
    return fd.object_definition(fd.validate_schema(_schema()), "deal")


def test_parse_filters_rejects_unknown_fields_and_ops() -> None:
    obj = _obj()
    assert fdq.parse_filters("", obj) == []
    assert fdq.parse_filters(None, obj) == []
    with pytest.raises(fdq.FilterError, match="not a declared field"):
        fdq.parse_filters([{"field": "nope", "op": "eq", "value": 1}], obj)
    with pytest.raises(fdq.FilterError, match="not supported"):
        fdq.parse_filters([{"field": "weight", "op": "matches", "value": 1}], obj)
    with pytest.raises(fdq.FilterError, match="does not apply"):
        # contains is a text operator; integers only take equality/range/in.
        fdq.parse_filters([{"field": "weight", "op": "contains", "value": 1}], obj)
    with pytest.raises(fdq.FilterError, match="does not apply"):
        fdq.parse_filters([{"field": "made_on", "op": "contains", "value": "2026"}], obj)


def test_parse_filters_validates_value_types() -> None:
    obj = _obj()
    with pytest.raises(fdq.FilterError, match="must be a number"):
        fdq.parse_filters([{"field": "weight", "op": "gte", "value": "100"}], obj)
    with pytest.raises(fdq.FilterError, match="YYYY-MM-DD"):
        fdq.parse_filters([{"field": "made_on", "op": "lte", "value": "2026/10/23"}], obj)
    with pytest.raises(fdq.FilterError, match="one of"):
        fdq.parse_filters([{"field": "stage", "op": "eq", "value": "lost"}], obj)
    with pytest.raises(fdq.FilterError, match="positive user id"):
        fdq.parse_filters([{"field": "owner", "op": "eq", "value": 0}], obj)
    with pytest.raises(fdq.FilterError, match="non-empty array"):
        fdq.parse_filters([{"field": "stage", "op": "in", "value": []}], obj)
    with pytest.raises(fdq.FilterError, match="true or false"):
        fdq.parse_filters([{"field": "active", "op": "eq", "value": "yes"}], obj)


@pytest.mark.parametrize(
    ("field_key", "operator", "value"),
    [
        ("created_at", "gte", {"bad": "type"}),
        ("updated_at", "eq", True),
        ("created_at", "in", [1, "invalid"]),
        ("created_at", "gte", float("nan")),
        ("updated_at", "lt", None),
        ("title", "contains", {"bad": "type"}),
        ("title", "in", ["valid", 3]),
    ],
)
def test_system_filter_values_cannot_bypass_typed_validation(
    field_key: str, operator: str, value: Any
) -> None:
    with pytest.raises(fdq.FilterError):
        fdq.parse_filters([{"field": field_key, "op": operator, "value": value}], _obj())


def test_parse_filters_caps_conditions_and_in_values() -> None:
    obj = _obj()
    many = [{"field": "weight", "op": "gte", "value": index} for index in range(21)]
    with pytest.raises(fdq.FilterError, match="at most 20"):
        fdq.parse_filters(many, obj)
    with pytest.raises(fdq.FilterError, match="at most 50"):
        fdq.parse_filters([{"field": "stage", "op": "in", "value": ["draft"] * 51}], obj)


def test_parse_filters_accepts_json_string_and_normalizes() -> None:
    obj = _obj()
    raw = json.dumps(
        [
            {"field": "weight", "op": "gte", "value": 100},
            {"field": "made_on", "op": "lte", "value": "2026-10-23"},
        ]
    )
    conditions = fdq.parse_filters(raw, obj)
    assert [condition["field"] for condition in conditions] == ["weight", "made_on"]


def test_parse_sort_rejects_unknown_and_bad_order() -> None:
    obj = _obj()
    with pytest.raises(fdq.FilterError, match="not a declared field"):
        fdq.parse_sort(obj, "mystery", "asc")
    with pytest.raises(fdq.FilterError, match="sort_order"):
        fdq.parse_sort(obj, "weight", "sideways")
    assert fdq.parse_sort(obj, None, None) == ("updated_at", "desc")
    assert fdq.parse_sort(obj, "made_on", "ASC") == ("made_on", "asc")


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    with pool.transaction() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (1, 'author', 'x', 'user', 1)"
        )
        conn.execute(
            "INSERT INTO agents(agent_id, user_id, name, kind, created_at, updated_at) "
            "VALUES ('feat-q', 1, 'q', 'feature', 1, 1)"
        )
    return pool


async def _seed(repo: FeatureDataRepo, workspace: BackendWorkspace, db: SqlitePool) -> None:
    from types import SimpleNamespace

    repos = SimpleNamespace(
        db=db,
        feature_data_repo=repo,
        user_repo=None,
        org_unit_repo=None,
        resource_acl_repo=None,
    )
    registry = SimpleNamespace(
        row=SimpleNamespace(agent_id="feat-q", kind="feature", user_id=1),
        get_row=lambda agent_id: SimpleNamespace(agent_id="feat-q", kind="feature", user_id=1),
        workspace_for_agent=lambda agent_id: workspace,
        octop_config=SimpleNamespace(default_timezone="UTC"),
    )
    await fd.save_schema(workspace, _schema())
    await fd.create_record(
        repos,
        workspace,
        agent_id="feat-q",
        object_key="deal",
        values={
            "name": "Alpha",
            "weight": 150,
            "occurred_at": "2026-10-29T10:00:00+02:00",
            "made_on": "2026-01-05",
            "active": True,
            "stage": "won",
            "score": 1.5,
        },
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )
    await fd.create_record(
        repos,
        workspace,
        agent_id="feat-q",
        object_key="deal",
        values={
            "name": "Beta",
            "weight": 90,
            "occurred_at": "2026-10-29T09:00:00Z",
            "made_on": "2026-03-01",
            "active": False,
            "stage": "draft",
        },
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )
    await fd.create_record(
        repos,
        workspace,
        agent_id="feat-q",
        object_key="deal",
        values={"name": "Gamma"},
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )


async def test_list_records_applies_filters_sort_and_total(db: SqlitePool, tmp_path: Path) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(root), virtual_mode=False), str(root)
    )
    repo = FeatureDataRepo(db)
    await _seed(repo, workspace, db)
    obj = _obj()

    fragments, params, order_by = fdq.filters_from_request(
        json.dumps([{"field": "weight", "op": "gte", "value": 100}]),
        "weight",
        "desc",
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order_by,
    )
    assert total == 1
    assert [record.values["name"] for record in records] == ["Alpha"]

    # lte on a date uses string comparison; the empty record is excluded by IS NULL semantics
    fragments, params, order_by = fdq.filters_from_request(
        json.dumps([{"field": "made_on", "op": "lte", "value": "2026-03-01"}]),
        None,
        None,
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order_by,
    )
    assert total == 2

    fragments, params, order_by = fdq.filters_from_request(
        json.dumps([{"field": "weight", "op": "is_empty"}]),
        "title",
        "asc",
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order_by,
    )
    assert total == 1
    assert records[0].values["name"] == "Gamma"

    # Sorting by a numeric field with explicit null ordering: Gamma (null) last.
    _fragments, _params, order_by = fdq.filters_from_request(
        "", "weight", "asc", obj, dialect="sqlite"
    )
    records, _total = repo.list_records(
        agent_id="feat-q", object_key="deal", user_id=1, order_by=order_by
    )
    assert [record.values.get("weight") for record in records] == [90, 150, None]

    # Datetime values with different offsets sort by instant, not their text.
    _fragments, _params, order_by = fdq.filters_from_request(
        "", "occurred_at", "asc", obj, dialect="sqlite"
    )
    records, _total = repo.list_records(
        agent_id="feat-q", object_key="deal", user_id=1, order_by=order_by
    )
    assert [record.values["name"] for record in records[:2]] == ["Alpha", "Beta"]
    # Missing datetimes remain empty without comparing a cast value to ''.
    fragments, params, _order_by = fdq.filters_from_request(
        json.dumps([{"field": "occurred_at", "op": "is_empty"}]),
        None,
        None,
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
    )
    assert total == 1
    assert records[0].values["name"] == "Gamma"
    # Comparisons use the same instant semantics as ordering.
    fragments, params, _order_by = fdq.filters_from_request(
        json.dumps([{"field": "occurred_at", "op": "gt", "value": "2026-10-29T08:30:00Z"}]),
        None,
        None,
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
    )
    assert total == 1
    assert records[0].values["name"] == "Beta"
    # Text contains with LIKE metacharacters matches literally.
    await fd.create_record(
        _simple_repos(db),
        workspace,
        agent_id="feat-q",
        object_key="deal",
        values={"name": "half-price_50%"},
        user_id=1,
        feature_active=True,
        registry=_simple_registry(workspace),
        timezone_name="UTC",
    )
    fragments, params, order_by = fdq.filters_from_request(
        json.dumps([{"field": "name", "op": "contains", "value": "50%"}]),
        None,
        None,
        obj,
        dialect="sqlite",
    )
    records, total = repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order_by,
    )
    assert total == 1
    assert records[0].values["name"] == "half-price_50%"


def _simple_repos(db: SqlitePool) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        db=db,
        feature_data_repo=FeatureDataRepo(db),
        user_repo=None,
        org_unit_repo=None,
        resource_acl_repo=None,
    )


def _simple_registry(workspace: BackendWorkspace) -> Any:
    from types import SimpleNamespace

    return SimpleNamespace(
        get_row=lambda agent_id: SimpleNamespace(agent_id="feat-q", kind="feature", user_id=1),
        workspace_for_agent=lambda agent_id: workspace,
        octop_config=SimpleNamespace(default_timezone="UTC"),
    )


@pytest.mark.parametrize("value", [float("nan"), float("inf"), float("-inf")])
def test_nonfinite_numbers_cannot_enter_records_or_filters(value: float) -> None:
    obj = _obj()
    with pytest.raises(ValueError, match="finite number"):
        fd.validate_values(obj, {"name": "Invalid", "score": value})
    with pytest.raises(fdq.FilterError, match="finite number"):
        fdq.parse_filters([{"field": "score", "op": "gte", "value": value}], obj)


async def test_numeric_query_survives_older_text_values_after_schema_change(
    db: SqlitePool, tmp_path: Path
) -> None:
    root = tmp_path / "workspace"
    root.mkdir()
    workspace = BackendWorkspace(
        LocalShellBackend(root_dir=str(root), virtual_mode=False), str(root)
    )
    repos = _simple_repos(db)
    registry = _simple_registry(workspace)
    old_schema = _schema()
    next(field for field in old_schema["objects"][0]["fields"] if field["key"] == "score")[
        "type"
    ] = "text"
    await fd.save_schema(workspace, old_schema)
    await fd.create_record(
        repos,
        workspace,
        agent_id="feat-q",
        object_key="deal",
        values={"name": "Legacy text", "score": "obsolete"},
        user_id=1,
        feature_active=True,
        registry=registry,
        timezone_name="UTC",
    )
    await fd.save_schema(workspace, _schema())
    for name, score in (("Twelve", 12), ("Two", 2)):
        await fd.create_record(
            repos,
            workspace,
            agent_id="feat-q",
            object_key="deal",
            values={"name": name, "score": score},
            user_id=1,
            feature_active=True,
            registry=registry,
            timezone_name="UTC",
        )
    obj = _obj()
    fragments, params, order = fdq.filters_from_request(
        [{"field": "score", "op": "gte", "value": 10}], "score", "asc", obj, dialect="sqlite"
    )
    filtered, total = repos.feature_data_repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order,
    )
    assert total == 1
    assert [record.values["name"] for record in filtered] == ["Twelve"]
    ordered, _ = repos.feature_data_repo.list_records(
        agent_id="feat-q",
        object_key="deal",
        user_id=1,
        order_by=fdq.compile_order("score", "asc", obj, dialect="sqlite"),
    )
    assert [record.values["name"] for record in ordered] == ["Two", "Twelve", "Legacy text"]
