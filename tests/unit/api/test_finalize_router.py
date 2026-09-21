"""Unit tests for the M4 capture endpoints: finalize, promote, case list."""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

import pytest

from octop.api.routers import features as features_router
from octop.infra.db.repos.feature_cases import FeatureCaseRow
from octop.infra.db.repos.feature_tasks import FINALIZED_STATUS, FeatureTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import Feature
from octop.infra.features.diff import diff_segments
from octop.infra.users.identity import Role, User

USER_ID = 7
OTHER_USER_ID = 8
FINISHED_AT = 1_700_000_000


def _user(user_id: int = USER_ID, *, role: Role = Role.USER) -> User:
    return User(
        id=user_id,
        username="u",
        role=role,
        display_name=None,
        permissions=["features"],
        denied_permissions=[],
    )


def _feature(feature_id: str) -> Feature:
    return Feature(
        id=feature_id,
        version=1,
        label={"zh": "报价单", "en": "Quote draft"},
        description={"zh": "描述", "en": "description"},
        icon_name="receipt",
        color="#e5484d",
        unit="sales",
        input_schema={"type": "object", "properties": {"topic": {"type": "string"}}},
        ui_schema={"order": ["topic"]},
        user_template="Draft about {{inputs}}",
        system_prompt="Be concise.",
        output_kind="markdown",
        permissions={"allow_units": ["*"]},
    )


def _task(
    task_id: str = "task-1",
    *,
    user_id: int = USER_ID,
    draft: str | None = "甲\n乙",
    final: str | None = None,
    diff_json: str | None = None,
    status: str = "succeeded",
    finalized_at: int | None = None,
) -> FeatureTaskRow:
    return FeatureTaskRow(
        id=task_id,
        feature_id="quote-draft",
        user_id=user_id,
        inputs='{"topic": "增长"}',
        draft=draft,
        final=final,
        status=status,
        error=None,
        created_at=1,
        diff_json=diff_json,
        finalized_at=finalized_at,
        agent_id="agent-1",
        injected_rule_ids="[]",
    )


def _case(task_id: str = "task-1", *, note: str | None = None) -> FeatureCaseRow:
    return FeatureCaseRow(
        task_id=task_id,
        feature_id="quote-draft",
        promoted_by=USER_ID,
        promoted_at=FINISHED_AT,
        note=note,
        inputs='{"topic": "增长"}',
        final="甲\n丙",
        diff_json='[{"op": "replace", "draft": "乙", "final": "丙"}]',
    )


class _FakeTaskRepo:
    """Stands in for ``FeatureTaskRepo`` and records every write it is asked for."""

    def __init__(self, task: FeatureTaskRow | None) -> None:
        self.task = task
        self.finalized: list[dict[str, Any]] = []
        # ``None`` models the conditional UPDATE matching no row (already finalized).
        self.finalize_result: FeatureTaskRow | None = None

    def get(self, task_id: str) -> FeatureTaskRow | None:
        return self.task if self.task is not None and self.task.id == task_id else None

    def finalize(self, task_id: str, *, final: str, diff_json: str) -> FeatureTaskRow | None:
        self.finalized.append({"task_id": task_id, "final": final, "diff_json": diff_json})
        return self.finalize_result


class _FakeCaseRepo:
    """Stands in for ``FeatureCaseRepo`` and records promotions."""

    def __init__(self, cases: list[FeatureCaseRow] | None = None) -> None:
        self.cases = list(cases or [])
        self.promoted: list[dict[str, Any]] = []
        self.promote_result: FeatureCaseRow | None = None

    def promote(
        self,
        task_id: str,
        *,
        feature_id: str,
        promoted_by: int,
        note: str | None = None,
    ) -> FeatureCaseRow | None:
        self.promoted.append(
            {
                "task_id": task_id,
                "feature_id": feature_id,
                "promoted_by": promoted_by,
                "note": note,
            }
        )
        return self.promote_result

    def list_for_feature(self, feature_id: str, limit: int = 50) -> list[FeatureCaseRow]:
        return [case for case in self.cases if case.feature_id == feature_id][:limit]


def _server(
    *,
    task: FeatureTaskRow | None = None,
    cases: list[FeatureCaseRow] | None = None,
) -> tuple[Any, _FakeTaskRepo, _FakeCaseRepo]:
    task_repo = _FakeTaskRepo(task)
    case_repo = _FakeCaseRepo(cases)
    server = SimpleNamespace(
        feature_catalog=SimpleNamespace(
            get=lambda feature_id: _feature(feature_id) if feature_id == "quote-draft" else None
        ),
        services=SimpleNamespace(
            repos=SimpleNamespace(feature_tasks_repo=task_repo, feature_cases_repo=case_repo)
        ),
    )
    return server, task_repo, case_repo


async def test_finalize_stores_final_computed_diff_and_timestamp() -> None:
    server, repo, _ = _server(task=_task())
    repo.finalize_result = _task(
        final="甲\n丙",
        diff_json='[{"op": "keep", "text": "甲"}, {"op": "replace", "draft": "乙", "final": "丙"}]',
        status=FINALIZED_STATUS,
        finalized_at=FINISHED_AT,
    )

    payload = await features_router.finalize_feature_task(
        "task-1", features_router.FeatureFinalizeBody(final="甲\n丙"), _user(), server
    )

    assert repo.finalized == [
        {
            "task_id": "task-1",
            "final": "甲\n丙",
            "diff_json": json.dumps(diff_segments("甲\n乙", "甲\n丙"), ensure_ascii=False),
        }
    ]
    assert payload == {
        "task_id": "task-1",
        "feature_id": "quote-draft",
        "status": FINALIZED_STATUS,
        "final": "甲\n丙",
        "diff": [
            {"op": "keep", "text": "甲"},
            {"op": "replace", "draft": "乙", "final": "丙"},
        ],
        "finalized_at": FINISHED_AT,
    }


async def test_unedited_finalize_stores_an_empty_diff() -> None:
    """Approving the draft untouched is the strongest positive sample."""
    server, repo, _ = _server(task=_task())
    repo.finalize_result = _task(
        final="甲\n乙", diff_json="[]", status=FINALIZED_STATUS, finalized_at=FINISHED_AT
    )

    payload = await features_router.finalize_feature_task(
        "task-1", features_router.FeatureFinalizeBody(final="甲\n乙"), _user(), server
    )

    assert repo.finalized[0]["diff_json"] == "[]"
    assert payload["diff"] == []


async def test_finalize_of_an_unknown_task_is_not_found() -> None:
    server, repo, _ = _server(task=None)

    with pytest.raises(OctopError) as excinfo:
        await features_router.finalize_feature_task(
            "missing", features_router.FeatureFinalizeBody(final="x"), _user(), server
        )

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert repo.finalized == []


async def test_finalize_by_another_user_is_forbidden() -> None:
    server, repo, _ = _server(task=_task())

    with pytest.raises(OctopError) as excinfo:
        await features_router.finalize_feature_task(
            "task-1",
            features_router.FeatureFinalizeBody(final="甲\n丙"),
            _user(OTHER_USER_ID),
            server,
        )

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    assert repo.finalized == []


async def test_admin_may_finalize_a_task_owned_by_someone_else() -> None:
    server, repo, _ = _server(task=_task())
    repo.finalize_result = _task(
        final="甲\n丙", diff_json="[]", status=FINALIZED_STATUS, finalized_at=FINISHED_AT
    )

    await features_router.finalize_feature_task(
        "task-1",
        features_router.FeatureFinalizeBody(final="甲\n丙"),
        _user(OTHER_USER_ID, role=Role.ADMIN),
        server,
    )

    assert repo.finalized[0]["final"] == "甲\n丙"


async def test_second_finalize_is_a_conflict() -> None:
    """The conditional UPDATE matching no row means it was finalized already."""
    server, repo, _ = _server(task=_task(final="甲\n丙", finalized_at=FINISHED_AT))

    with pytest.raises(OctopError) as excinfo:
        await features_router.finalize_feature_task(
            "task-1", features_router.FeatureFinalizeBody(final="甲\n丁"), _user(), server
        )

    assert excinfo.value.code is ErrorCode.FEATURE_TASK_FINALIZED
    assert excinfo.value.status == 409
    assert repo.finalized[0]["final"] == "甲\n丁"


async def test_finalize_diff_is_computed_from_the_stored_draft() -> None:
    server, repo, _ = _server(task=_task(draft="旧草稿"))
    repo.finalize_result = _task(
        draft="旧草稿",
        final="新定稿",
        diff_json="[]",
        status=FINALIZED_STATUS,
        finalized_at=FINISHED_AT,
    )

    await features_router.finalize_feature_task(
        "task-1", features_router.FeatureFinalizeBody(final="新定稿"), _user(), server
    )

    assert json.loads(repo.finalized[0]["diff_json"]) == [
        {"op": "replace", "draft": "旧草稿", "final": "新定稿"}
    ]


async def test_promote_records_the_case_reference_and_note() -> None:
    server, _, cases = _server(task=_task(final="甲\n丙", finalized_at=FINISHED_AT))
    cases.promote_result = _case(note="全称范例")

    payload = await features_router.promote_feature_task(
        "task-1", features_router.FeaturePromoteBody(note="全称范例"), _user(), server
    )

    assert cases.promoted == [
        {
            "task_id": "task-1",
            "feature_id": "quote-draft",
            "promoted_by": USER_ID,
            "note": "全称范例",
        }
    ]
    assert payload["case"]["task_id"] == "task-1"
    assert payload["case"]["final"] == "甲\n丙"


async def test_promote_without_a_body_stores_no_note() -> None:
    server, _, cases = _server(task=_task(final="甲\n丙", finalized_at=FINISHED_AT))
    cases.promote_result = _case()

    await features_router.promote_feature_task("task-1", None, _user(), server)

    assert cases.promoted[0]["note"] is None


async def test_promote_before_finalize_is_a_conflict() -> None:
    """A case is few-shot material: an unfinalized run has nothing to teach."""
    server, _, cases = _server(task=_task(final=None, finalized_at=None))

    with pytest.raises(OctopError) as excinfo:
        await features_router.promote_feature_task("task-1", None, _user(), server)

    assert excinfo.value.code is ErrorCode.FEATURE_TASK_NOT_FINALIZED
    assert excinfo.value.status == 409
    assert cases.promoted == []


async def test_promote_of_an_unknown_task_is_not_found() -> None:
    server, _, cases = _server(task=None)

    with pytest.raises(OctopError) as excinfo:
        await features_router.promote_feature_task("missing", None, _user(), server)

    assert excinfo.value.code is ErrorCode.NOT_FOUND
    assert cases.promoted == []


async def test_promote_by_another_user_is_forbidden() -> None:
    server, _, cases = _server(task=_task(final="甲\n丙", finalized_at=FINISHED_AT))

    with pytest.raises(OctopError) as excinfo:
        await features_router.promote_feature_task("task-1", None, _user(OTHER_USER_ID), server)

    assert excinfo.value.code is ErrorCode.FORBIDDEN
    assert cases.promoted == []


async def test_case_list_returns_this_features_cases() -> None:
    server, _, _ = _server(cases=[_case("task-1", note="范例"), _case("task-2")])

    payload = await features_router.list_feature_cases("quote-draft", _user(), server)

    assert payload["feature_id"] == "quote-draft"
    assert [case["task_id"] for case in payload["cases"]] == ["task-1", "task-2"]
    assert payload["cases"][0]["note"] == "范例"
    assert payload["cases"][0]["inputs"] == {"topic": "增长"}
    assert payload["cases"][0]["diff"] == [{"op": "replace", "draft": "乙", "final": "丙"}]


async def test_case_list_without_promotions_is_empty() -> None:
    server, _, _ = _server()

    payload = await features_router.list_feature_cases("quote-draft", _user(), server)

    assert payload == {"feature_id": "quote-draft", "cases": []}


async def test_case_list_of_an_unknown_feature_is_not_found() -> None:
    server, _, _ = _server()

    with pytest.raises(OctopError) as excinfo:
        await features_router.list_feature_cases("nope", _user(), server)

    assert excinfo.value.code is ErrorCode.NOT_FOUND
