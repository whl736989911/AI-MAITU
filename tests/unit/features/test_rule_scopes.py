"""Tests for the three layers a feature rule can live at (schema v23).

A rule is personal (its owner's alone), unit (its department's), or global
(everyone's). What a run injects, who may approve what, and where a submission
lands are all decided by that layer, so these tests pin the layer's behaviour
rather than the row's plumbing.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.api.routers import features as features_router
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.audit import AuditRepo
from octop.infra.db.repos.feature_rules import (
    APPROVED,
    DRAFT,
    SCOPE_GLOBAL,
    SCOPE_PERSONAL,
    SCOPE_UNIT,
    FeatureRuleRepo,
)
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo, FeatureTaskRow
from octop.infra.db.repos.org_units import OrgUnitRepo
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import Feature, ScopedRule, build_user_prompt
from octop.infra.features.rules import (
    RuleScopeForbidden,
    RuleScopeInvalid,
    extract_rules,
    injectable_rule_rows,
    may_review_rule,
    submit_rule,
)
from octop.infra.users.identity import Role

OWNER_ID = 7
COLLEAGUE_ID = 8
UNIT_ADMIN_ID = 9
ADMIN_ID = 10

SALES = "sales"
SUPPORT = "support"


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def rules(db: SqlitePool) -> FeatureRuleRepo:
    return FeatureRuleRepo(db)


@pytest.fixture(autouse=True)
def users(db: SqlitePool) -> dict[str, int]:
    """The people and departments these rules belong to.

    Real ``users`` rows, because a personal rule references its owner through a
    foreign key and the review guards read the review*er*'s unit from one. The two
    departments carry labels, so a unit rule's payload resolves to a name rather
    than falling back to its key.
    """
    rows = [
        (OWNER_ID, "owner", Role.USER, SALES),
        (COLLEAGUE_ID, "colleague", Role.USER, SALES),
        (UNIT_ADMIN_ID, "sales-admin", Role.UNIT_ADMIN, SALES),
        (ADMIN_ID, "admin", Role.ADMIN, None),
    ]
    with db.connect() as conn:
        for key, label in ((SALES, "销售部"), (SUPPORT, "客服部")):
            conn.execute(
                "INSERT INTO org_units(key, label_zh, label_en, sort_order, created_at) "
                "VALUES (?, ?, ?, 0, 0)",
                (key, label, key.title()),
            )
        for user_id, username, role, org_unit in rows:
            conn.execute(
                "INSERT INTO users(id, username, password_hash, role, org_unit, created_at) "
                "VALUES (?, ?, ?, ?, ?, 0)",
                (user_id, username, "h", str(role), org_unit),
            )
    return {username: user_id for _, username, _, _ in rows}


def _feature(feature_id: str = "quote-draft") -> Feature:
    return Feature(
        id=feature_id,
        version=1,
        label={"zh": "报价单草稿", "en": "Quote draft"},
        description={"zh": "根据客户信息起草报价单", "en": "Draft a quotation"},
        icon_name="receipt",
        color="#e5484d",
        unit="sales",
        input_schema={
            "type": "object",
            "properties": {"topic": {"type": "string", "title": {"zh": "主题", "en": "Topic"}}},
            "required": ["topic"],
        },
        ui_schema={"order": ["topic"]},
        user_template="整理以下输入：\n{{inputs}}",
        system_prompt=None,
        output_kind="markdown",
        permissions={"allow_units": ["*"]},
    )


def _approve(
    rules: FeatureRuleRepo,
    *,
    text: str,
    scope: str = SCOPE_GLOBAL,
    owner_user_id: int | None = None,
    unit_key: str | None = None,
    reviewed_at: int = 1_000,
    feature_id: str = "quote-draft",
) -> str:
    """One approved rule at *scope*, as the review queue would have left it."""
    row = rules.create_many(
        feature_id=feature_id,
        rule_texts=[text],
        source_task_ids=["task-1"],
        proposed_by="ai",
        scope=scope,
        owner_user_id=owner_user_id,
        unit_key=unit_key,
    )[0]
    rules.mark_reviewed(row.id, status=APPROVED, approved_by=OWNER_ID, reviewed_at=reviewed_at)
    return row.id


def _user(user_id: int, role: Role, org_unit: str | None) -> SimpleNamespace:
    """API user double: id, role, unit, and the name the audit log records."""
    return SimpleNamespace(
        id=user_id,
        username=f"user-{user_id}",
        role=role,
        org_unit=org_unit,
        is_admin=role is Role.ADMIN,
    )


def _server(db: SqlitePool, rules: FeatureRuleRepo, feature: Feature) -> SimpleNamespace:
    return SimpleNamespace(
        feature_catalog=SimpleNamespace(list=lambda: [feature], get={feature.id: feature}.get),
        services=SimpleNamespace(
            repos=SimpleNamespace(
                feature_rules_repo=rules,
                feature_tasks_repo=FeatureTaskRepo(db),
                org_unit_repo=OrgUnitRepo(db),
            ),
            audit_repo=AuditRepo(db),
        ),
    )


# ---------------------------------------------------------------------------
# Injection: what one caller's prompt gets (contract §parse)
# ---------------------------------------------------------------------------


def test_own_rules_take_the_prompt_slots_before_global_ones(rules: FeatureRuleRepo) -> None:
    for index in range(8):
        _approve(
            rules,
            text=f"个人规则 {index}",
            scope=SCOPE_PERSONAL,
            owner_user_id=OWNER_ID,
            reviewed_at=2_000 + index,
        )
    for index in range(5):
        _approve(rules, text=f"全局规则 {index}", scope=SCOPE_GLOBAL, reviewed_at=1_000 + index)

    injected = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID)

    assert len(injected) == 10
    assert [row.scope for row in injected] == [SCOPE_PERSONAL] * 8 + [SCOPE_GLOBAL] * 2
    # Within a layer the most recently reviewed comes first, so the global layer
    # is cut from the oldest end — the personal rules claimed their slots first.
    assert [row.rule_text for row in injected] == [
        *(f"个人规则 {index}" for index in range(7, -1, -1)),
        "全局规则 4",
        "全局规则 3",
    ]


def test_another_users_personal_rule_is_never_injected(rules: FeatureRuleRepo) -> None:
    _approve(rules, text="别人的个人偏好", scope=SCOPE_PERSONAL, owner_user_id=COLLEAGUE_ID)

    assert injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID) == []
    assert injectable_rule_rows(rules, "quote-draft") == []


def test_unit_rules_reach_their_own_department_only(rules: FeatureRuleRepo) -> None:
    _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES)
    _approve(rules, text="客服部要求", scope=SCOPE_UNIT, unit_key=SUPPORT)
    _approve(rules, text="公司要求", scope=SCOPE_GLOBAL)

    own_unit = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID, unit_key=SALES)
    other_unit = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID, unit_key=SUPPORT)
    no_unit = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID)

    assert [row.rule_text for row in own_unit] == ["销售部要求", "公司要求"]
    assert [row.rule_text for row in other_unit] == ["客服部要求", "公司要求"]
    assert [row.rule_text for row in no_unit] == ["公司要求"]


def test_layers_are_injected_narrowest_first(rules: FeatureRuleRepo) -> None:
    _approve(rules, text="公司要求", scope=SCOPE_GLOBAL, reviewed_at=3_000)
    _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES, reviewed_at=2_000)
    _approve(
        rules,
        text="我的要求",
        scope=SCOPE_PERSONAL,
        owner_user_id=OWNER_ID,
        reviewed_at=1_000,
    )

    injected = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID, unit_key=SALES)

    assert [row.rule_text for row in injected] == ["我的要求", "销售部要求", "公司要求"]


# ---------------------------------------------------------------------------
# Extraction: which layer the drafts land in, and whose runs they read
# ---------------------------------------------------------------------------


class _Tasks:
    """``FeatureTaskRepo`` double — extraction only ever reads."""

    def __init__(self, rows: list[FeatureTaskRow]) -> None:
        self.rows = rows

    def list_for_feature(self, feature_id: str, limit: int = 50) -> list[FeatureTaskRow]:
        return [row for row in self.rows if row.feature_id == feature_id][:limit]


class _Runner:
    def __init__(self, reply: str = '["客户名写全称"]') -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


def _task(task_id: str, *, user_id: int, text: str, finalized_at: int = 1_700_000_000) -> Any:
    return FeatureTaskRow(
        id=task_id,
        feature_id="quote-draft",
        user_id=user_id,
        inputs="{}",
        draft="草稿",
        final="定稿",
        status="finalized",
        error=None,
        created_at=finalized_at,
        diff_json=json.dumps([{"op": "replace", "draft": "客户：张总", "final": text}]),
        finalized_at=finalized_at,
        agent_id="agent-1",
        injected_rule_ids="[]",
    )


async def test_personal_extraction_writes_for_its_owner_only(rules: FeatureRuleRepo) -> None:
    tasks = _Tasks(
        [
            _task("mine", user_id=OWNER_ID, text="我的改动"),
            _task("theirs", user_id=COLLEAGUE_ID, text="同事的改动"),
        ]
    )
    runner = _Runner()

    created = await extract_rules(
        repo=rules,
        tasks_repo=tasks,  # type: ignore[arg-type]
        feature=_feature(),
        runner=runner,
        owner_user_id=OWNER_ID,
        user_ids=[OWNER_ID],
    )

    assert [row.scope for row in created] == [SCOPE_PERSONAL]
    assert [row.owner_user_id for row in created] == [OWNER_ID]
    assert [row.unit_key for row in created] == [None]
    assert "我的改动" in runner.prompts[0]
    assert "同事的改动" not in runner.prompts[0]


async def test_unit_extraction_writes_for_the_department(rules: FeatureRuleRepo) -> None:
    tasks = _Tasks(
        [
            _task("mine", user_id=OWNER_ID, text="本部门的改动"),
            _task("outsider", user_id=ADMIN_ID, text="部门外的改动"),
        ]
    )
    runner = _Runner()

    created = await extract_rules(
        repo=rules,
        tasks_repo=tasks,  # type: ignore[arg-type]
        feature=_feature(),
        runner=runner,
        scope=SCOPE_UNIT,
        unit_key=SALES,
        user_ids=[OWNER_ID, COLLEAGUE_ID],
    )

    assert [row.scope for row in created] == [SCOPE_UNIT]
    assert [row.unit_key for row in created] == [SALES]
    assert [row.owner_user_id for row in created] == [None]
    assert "本部门的改动" in runner.prompts[0]
    assert "部门外的改动" not in runner.prompts[0]


async def test_global_extraction_reads_every_users_runs(rules: FeatureRuleRepo) -> None:
    tasks = _Tasks(
        [
            _task("mine", user_id=OWNER_ID, text="我的改动"),
            _task("theirs", user_id=ADMIN_ID, text="同事的改动"),
        ]
    )
    runner = _Runner()

    created = await extract_rules(
        repo=rules,
        tasks_repo=tasks,  # type: ignore[arg-type]
        feature=_feature(),
        runner=runner,
        scope=SCOPE_GLOBAL,
    )

    assert [row.scope for row in created] == [SCOPE_GLOBAL]
    assert [row.owner_user_id for row in created] == [None]
    assert "我的改动" in runner.prompts[0]
    assert "同事的改动" in runner.prompts[0]


@pytest.mark.parametrize(
    ("scope", "owner_user_id", "unit_key"),
    [(SCOPE_PERSONAL, None, None), (SCOPE_UNIT, None, None), ("team", None, None)],
)
async def test_a_layer_that_cannot_hold_the_rule_is_refused(
    rules: FeatureRuleRepo,
    scope: str,
    owner_user_id: int | None,
    unit_key: str | None,
) -> None:
    runner = _Runner()

    with pytest.raises(RuleScopeInvalid):
        await extract_rules(
            repo=rules,
            tasks_repo=_Tasks([_task("mine", user_id=OWNER_ID, text="改动")]),  # type: ignore[arg-type]
            feature=_feature(),
            runner=runner,
            scope=scope,
            owner_user_id=owner_user_id,
            unit_key=unit_key,
        )

    assert runner.prompts == []
    assert rules.list_for_feature("quote-draft") == []


# ---------------------------------------------------------------------------
# Submission: a personal rule is proposed up a layer, never moved
# ---------------------------------------------------------------------------


def test_submit_writes_a_draft_upstream_and_leaves_the_rule_alone(
    rules: FeatureRuleRepo,
) -> None:
    personal_id = _approve(
        rules,
        text="客户名写全称",
        scope=SCOPE_PERSONAL,
        owner_user_id=OWNER_ID,
    )

    created = submit_rule(
        rules,
        personal_id,
        target_scope=SCOPE_UNIT,
        submitter_id=OWNER_ID,
        role=Role.USER,
        unit_key=SALES,
    )

    assert created.id != personal_id
    assert (created.scope, created.unit_key, created.owner_user_id) == (SCOPE_UNIT, SALES, None)
    assert created.status == DRAFT
    assert created.proposed_by == f"user:{OWNER_ID}"
    # The wider layer needs the same provenance the original was induced from.
    assert created.source_task_id_list() == ["task-1"]

    original = rules.get(personal_id)
    assert original is not None
    assert original.scope == SCOPE_PERSONAL
    assert original.unit_key is None
    # Still live for its owner: the wider layer's decision is not a precondition.
    injected = injectable_rule_rows(rules, "quote-draft", user_id=OWNER_ID, unit_key=SALES)
    assert [row.id for row in injected] == [personal_id]


def test_submit_to_a_unit_without_a_unit_holds_the_rule(rules: FeatureRuleRepo) -> None:
    personal_id = _approve(
        rules,
        text="客户名写全称",
        scope=SCOPE_PERSONAL,
        owner_user_id=OWNER_ID,
    )

    with pytest.raises(RuleScopeInvalid):
        submit_rule(
            rules,
            personal_id,
            target_scope=SCOPE_UNIT,
            submitter_id=OWNER_ID,
            role=Role.USER,
            unit_key=None,
        )

    assert [row.id for row in rules.list_for_feature("quote-draft")] == [personal_id]


def test_submit_only_leaves_the_personal_layer(rules: FeatureRuleRepo) -> None:
    unit_id = _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES)

    with pytest.raises(RuleScopeInvalid):
        submit_rule(
            rules,
            unit_id,
            target_scope=SCOPE_GLOBAL,
            submitter_id=OWNER_ID,
            role=Role.ADMIN,
            unit_key=SALES,
        )


def test_submit_of_someone_elses_rule_is_refused(rules: FeatureRuleRepo) -> None:
    personal_id = _approve(
        rules,
        text="别人的偏好",
        scope=SCOPE_PERSONAL,
        owner_user_id=COLLEAGUE_ID,
    )

    with pytest.raises(RuleScopeForbidden):
        submit_rule(
            rules,
            personal_id,
            target_scope=SCOPE_UNIT,
            submitter_id=OWNER_ID,
            role=Role.USER,
            unit_key=SALES,
        )


def test_submit_to_the_global_layer_needs_an_admin(rules: FeatureRuleRepo) -> None:
    unit_admin_rule = _approve(
        rules,
        text="部门管理员的偏好",
        scope=SCOPE_PERSONAL,
        owner_user_id=UNIT_ADMIN_ID,
    )

    with pytest.raises(RuleScopeForbidden):
        submit_rule(
            rules,
            unit_admin_rule,
            target_scope=SCOPE_GLOBAL,
            submitter_id=UNIT_ADMIN_ID,
            role=Role.UNIT_ADMIN,
            unit_key=SALES,
        )

    admin_rule = _approve(
        rules,
        text="管理员的偏好",
        scope=SCOPE_PERSONAL,
        owner_user_id=ADMIN_ID,
    )
    created = submit_rule(
        rules,
        admin_rule,
        target_scope=SCOPE_GLOBAL,
        submitter_id=ADMIN_ID,
        role=Role.ADMIN,
        unit_key=None,
    )
    assert created.scope == SCOPE_GLOBAL
    assert created.owner_user_id is None


# ---------------------------------------------------------------------------
# Review: the layer decides who may decide (contract §review table)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("scope", "unit_key", "owner_user_id", "reviewer", "expected"),
    [
        # personal — its owner's own call, and nobody else's (admin included).
        (SCOPE_PERSONAL, None, OWNER_ID, (OWNER_ID, Role.USER, SALES), True),
        (SCOPE_PERSONAL, None, OWNER_ID, (COLLEAGUE_ID, Role.USER, SALES), False),
        (SCOPE_PERSONAL, None, OWNER_ID, (ADMIN_ID, Role.ADMIN, None), False),
        # unit — that department's unit admin, or an admin.
        (SCOPE_UNIT, SALES, None, (UNIT_ADMIN_ID, Role.UNIT_ADMIN, SALES), True),
        (SCOPE_UNIT, SALES, None, (ADMIN_ID, Role.ADMIN, None), True),
        (SCOPE_UNIT, SALES, None, (UNIT_ADMIN_ID, Role.UNIT_ADMIN, SUPPORT), False),
        (SCOPE_UNIT, SALES, None, (OWNER_ID, Role.USER, SALES), False),
        # global — an admin, and only an admin.
        (SCOPE_GLOBAL, None, None, (ADMIN_ID, Role.ADMIN, None), True),
        (SCOPE_GLOBAL, None, None, (UNIT_ADMIN_ID, Role.UNIT_ADMIN, SALES), False),
        (SCOPE_GLOBAL, None, None, (OWNER_ID, Role.USER, SALES), False),
    ],
)
def test_may_review_follows_the_layer(
    rules: FeatureRuleRepo,
    scope: str,
    unit_key: str | None,
    owner_user_id: int | None,
    reviewer: tuple[int, Role, str | None],
    expected: bool,
) -> None:
    row = rules.get(
        _approve(
            rules,
            text="规则",
            scope=scope,
            unit_key=unit_key,
            owner_user_id=owner_user_id,
        )
    )
    assert row is not None
    user_id, role, reviewer_unit = reviewer

    assert may_review_rule(row, user_id=user_id, role=role, unit_key=reviewer_unit) is expected


async def test_approving_a_global_rule_is_not_a_plain_users_call(
    db: SqlitePool, rules: FeatureRuleRepo
) -> None:
    feature = _feature()
    rule_id = rules.create_many(
        feature_id=feature.id,
        rule_texts=["公司级要求"],
        source_task_ids=["task-1"],
        proposed_by="ai",
        scope=SCOPE_GLOBAL,
    )[0].id
    server = _server(db, rules, feature)

    with pytest.raises(OctopError) as excinfo:
        await features_router.approve_feature_rule(
            rule_id, _user(OWNER_ID, Role.USER, SALES), server
        )

    assert excinfo.value.code == ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN
    assert excinfo.value.status == 403
    stored = rules.get(rule_id)
    assert stored is not None and stored.status == DRAFT


async def test_a_unit_admin_cannot_decide_another_units_rule(
    db: SqlitePool, rules: FeatureRuleRepo
) -> None:
    feature = _feature()
    rule_id = rules.create_many(
        feature_id=feature.id,
        rule_texts=["客服部要求"],
        source_task_ids=["task-1"],
        proposed_by="ai",
        scope=SCOPE_UNIT,
        unit_key=SUPPORT,
    )[0].id
    server = _server(db, rules, feature)

    with pytest.raises(OctopError) as excinfo:
        await features_router.approve_feature_rule(
            rule_id, _user(UNIT_ADMIN_ID, Role.UNIT_ADMIN, SALES), server
        )

    assert excinfo.value.code == ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN
    assert excinfo.value.status == 403

    approved = await features_router.approve_feature_rule(
        rule_id, _user(UNIT_ADMIN_ID, Role.UNIT_ADMIN, SUPPORT), server
    )
    assert approved["status"] == APPROVED
    assert approved["scope"] == SCOPE_UNIT
    # Resolved from org_units, not the raw key the rule stores.
    assert approved["unit_label"] == "客服部"


async def test_the_review_list_hides_other_peoples_personal_rules(
    db: SqlitePool, rules: FeatureRuleRepo
) -> None:
    feature = _feature()
    _approve(rules, text="我的偏好", scope=SCOPE_PERSONAL, owner_user_id=OWNER_ID)
    _approve(rules, text="同事的偏好", scope=SCOPE_PERSONAL, owner_user_id=COLLEAGUE_ID)
    _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES)
    _approve(rules, text="客服部要求", scope=SCOPE_UNIT, unit_key=SUPPORT)
    _approve(rules, text="公司要求", scope=SCOPE_GLOBAL)
    server = _server(db, rules, feature)

    visible = await features_router.list_feature_rules(
        feature.id, _user(OWNER_ID, Role.USER, SALES), server
    )

    assert {rule["rule_text"] for rule in visible["rules"]} == {
        "我的偏好",
        "销售部要求",
        "公司要求",
    }
    assert {rule["scope"] for rule in visible["rules"]} == {SCOPE_PERSONAL, SCOPE_UNIT, SCOPE_GLOBAL}


async def test_an_admin_sees_every_departments_queue_but_no_ones_personal_rules(
    db: SqlitePool,
    rules: FeatureRuleRepo,
) -> None:
    """An admin decides every department's rules, so the queue has to show them.

    Personal rules stay out of everyone else's list — an admin may not decide
    those, and reading them would leak a colleague's own preferences.
    """
    feature = _feature()
    _approve(rules, text="同事的偏好", scope=SCOPE_PERSONAL, owner_user_id=COLLEAGUE_ID)
    _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES)
    _approve(rules, text="客服部要求", scope=SCOPE_UNIT, unit_key=SUPPORT)
    server = _server(db, rules, feature)

    visible = await features_router.list_feature_rules(
        feature.id, _user(ADMIN_ID, Role.ADMIN, None), server
    )

    assert {rule["rule_text"] for rule in visible["rules"]} == {"销售部要求", "客服部要求"}


# ---------------------------------------------------------------------------
# Endpoints: the layer is part of the contract the caller sees
# ---------------------------------------------------------------------------


async def test_submit_endpoint_reports_a_missing_unit_as_a_bad_request(
    db: SqlitePool, rules: FeatureRuleRepo
) -> None:
    feature = _feature()
    rule_id = _approve(rules, text="客户名写全称", scope=SCOPE_PERSONAL, owner_user_id=ADMIN_ID)
    server = _server(db, rules, feature)

    with pytest.raises(OctopError) as excinfo:
        await features_router.submit_feature_rule(
            rule_id,
            features_router.FeatureRuleSubmitBody(target_scope="unit"),
            _user(ADMIN_ID, Role.ADMIN, None),
            server,
        )

    assert excinfo.value.code == ErrorCode.FEATURE_RULE_SUBMIT_INVALID
    assert excinfo.value.status == 400


async def test_submit_endpoint_records_the_reason_and_returns_the_new_rule(
    db: SqlitePool, rules: FeatureRuleRepo
) -> None:
    feature = _feature()
    rule_id = _approve(rules, text="客户名写全称", scope=SCOPE_PERSONAL, owner_user_id=OWNER_ID)
    server = _server(db, rules, feature)

    payload = await features_router.submit_feature_rule(
        rule_id,
        features_router.FeatureRuleSubmitBody(
            target_scope="unit", reason="整个部门都在用这条"
        ),
        _user(OWNER_ID, Role.USER, SALES),
        server,
    )

    rule = payload["rule"]
    assert rule["scope"] == SCOPE_UNIT
    assert rule["unit_key"] == SALES
    assert rule["status"] == DRAFT
    assert rule["source_task_ids"] == ["task-1"]
    audited = AuditRepo(db).query(action="feature_rule.submit", limit=10)
    assert len(audited) == 1
    assert audited[0].target == rule_id
    assert "整个部门都在用这条" in (audited[0].payload or "")


async def test_run_injects_the_callers_layers_with_their_tags(
    db: SqlitePool, rules: FeatureRuleRepo, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def _resolve(**kwargs: Any) -> tuple[str, str]:
        return "thread-1", "dashboard:agent-1:7"

    monkeypatch.setattr(features_router, "resolve_thread_id", _resolve)
    feature = _feature()
    _approve(rules, text="我的偏好", scope=SCOPE_PERSONAL, owner_user_id=OWNER_ID)
    _approve(rules, text="销售部要求", scope=SCOPE_UNIT, unit_key=SALES)
    _approve(rules, text="客服部要求", scope=SCOPE_UNIT, unit_key=SUPPORT)
    _approve(rules, text="公司要求", scope=SCOPE_GLOBAL)
    server = _server(db, rules, feature)
    registry = _RecordingRegistry()
    server.app_runtime = SimpleNamespace(gateway=_Gateway(), agent_registry=registry)

    await features_router.run_feature(
        feature.id,
        features_router.FeatureRunBody(inputs={"topic": "季度采购"}),
        _user(OWNER_ID, Role.USER, SALES),
        server,
    )

    sent = registry.requests[0]["messages"][0]["content"]
    assert "【个人规则】我的偏好" in sent
    assert "【部门规则】销售部要求" in sent
    assert "【全局规则】公司要求" in sent
    assert "客服部要求" not in sent
    assert sent.index("我的偏好") < sent.index("销售部要求") < sent.index("公司要求")


class _Gateway:
    def __init__(self) -> None:
        self.thread_registry = SimpleNamespace()

    def require_session(self, agent_id: str, session_key: str) -> Any:
        return SimpleNamespace(thread_id="thread-1", user_id=OWNER_ID, channel_type="dashboard")

    async def run_in_session(self, agent_id: str, session_key: str, operation: Any) -> None:
        await operation()


class _RecordingRegistry:
    def __init__(self) -> None:
        self.requests: list[dict[str, Any]] = []

    def list_agents(self, user_id: int) -> list[Any]:
        return [SimpleNamespace(agent_id="agent-1")]

    def get_agent(self, agent_id: str) -> Any:
        return object()

    async def start(self, agent_id: str) -> None:
        return None

    async def stream(self, agent_id: str, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        yield {"type": "token", "content": "草稿正文"}


# ---------------------------------------------------------------------------
# Prompt rendering: the tag is the model's only clue about authority
# ---------------------------------------------------------------------------


def test_prompt_tags_each_rule_with_its_layer() -> None:
    feature = _feature()

    prompt = build_user_prompt(
        feature,
        {"topic": "季度采购"},
        rules=[
            ScopedRule("我的偏好", SCOPE_PERSONAL),
            ScopedRule("销售部要求", SCOPE_UNIT),
            ScopedRule("公司要求", SCOPE_GLOBAL),
        ],
    )

    assert "1. 【个人规则】我的偏好" in prompt
    assert "2. 【部门规则】销售部要求" in prompt
    assert "3. 【全局规则】公司要求" in prompt


def test_prompt_leaves_an_unscoped_rule_untagged() -> None:
    feature = _feature()

    prompt = build_user_prompt(feature, {"topic": "x"}, rules=[ScopedRule("没有来源的规则")])

    assert prompt.endswith(
        "以下要求来自历史修正记录归纳（人工审核通过），不是本次输入的一部分，请一并遵守：\n"
        "1. 没有来源的规则"
    )


def test_prompt_without_rules_is_still_the_bare_template() -> None:
    feature = _feature()
    inputs = {"topic": "季度采购"}

    expected = "整理以下输入：\n- 主题：季度采购"

    assert build_user_prompt(feature, inputs) == expected
    assert build_user_prompt(feature, inputs, rules=None) == expected
    assert build_user_prompt(feature, inputs, rules=[]) == expected
    assert build_user_prompt(feature, inputs, rules=[ScopedRule(" "), ScopedRule("")]) == expected
