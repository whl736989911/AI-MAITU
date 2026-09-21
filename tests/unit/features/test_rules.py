"""Tests for rule induction, human review, and prompt injection (M4)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from octop.api.routers import features as features_router
from octop.infra.db.migrate import run_migrations
from octop.infra.db.pool import SqlitePool
from octop.infra.db.repos.feature_rules import (
    APPROVED,
    DRAFT,
    REJECTED,
    FeatureRuleRepo,
)
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo, FeatureTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import catalog as catalog_module
from octop.infra.features.catalog import (
    MAX_INJECTED_RULES,
    Feature,
    ScopedRule,
    build_user_prompt,
)
from octop.infra.features.rules import (
    MAX_EXTRACTED_RULES,
    MAX_SOURCE_TASKS,
    RuleAlreadyReviewed,
    RuleExtractionFailed,
    RuleNoSamples,
    RuleNotFound,
    build_extraction_prompt,
    extract_rules,
    finalized_samples,
    injectable_rule_rows,
    parse_rule_reply,
    review_rule,
)

USER_ID = 7


@pytest.fixture
def db(tmp_path: Path) -> SqlitePool:
    pool = SqlitePool(tmp_path / "octop.db")
    run_migrations(pool)
    return pool


@pytest.fixture
def user_id(db: SqlitePool) -> int:
    """A real ``users`` row — rules record their reviewer, and now their owner,
    through foreign keys. The id is pinned to :data:`USER_ID` so a task row built
    with ``USER_ID`` and a rule owned by ``USER_ID`` refer to the same person.
    """
    with db.connect() as conn:
        conn.execute(
            "INSERT INTO users(id, username, password_hash, role, created_at) "
            "VALUES (?, ?, ?, ?, 0)",
            (USER_ID, "reviewer", "h", "user"),
        )
    return USER_ID


@pytest.fixture
def rules(db: SqlitePool) -> FeatureRuleRepo:
    return FeatureRuleRepo(db)


def _feature(feature_id: str = "quote-draft", **overrides: Any) -> Feature:
    payload: dict[str, Any] = {
        "id": feature_id,
        "version": 1,
        "label": {"zh": "报价单草稿", "en": "Quote draft"},
        "description": {"zh": "根据客户信息起草报价单", "en": "Draft a quotation"},
        "icon_name": "receipt",
        "color": "#e5484d",
        "unit": "sales",
        "input_schema": {
            "type": "object",
            "properties": {
                "topic": {"type": "string", "title": {"zh": "主题", "en": "Topic"}},
                "note": {"type": "string", "title": {"zh": "备注", "en": "Note"}},
            },
            "required": ["topic"],
        },
        "ui_schema": {"order": ["topic"]},
        "user_template": "整理以下输入：\n{{inputs}}",
        "system_prompt": None,
        "output_kind": "markdown",
        "permissions": {"allow_units": ["*"]},
    }
    payload.update(overrides)
    return Feature(**payload)


def _task(
    task_id: str,
    *,
    feature_id: str = "quote-draft",
    finalized_at: int | None = 1_700_000_000,
    diff: Any = None,
    raw_diff: str | None = None,
) -> FeatureTaskRow:
    """One ``feature_tasks`` row; ``finalized_at=None`` means still a draft."""
    if raw_diff is not None:
        diff_json = raw_diff
    elif diff is None:
        diff_json = "[]"
    else:
        diff_json = json.dumps(diff, ensure_ascii=False)
    return FeatureTaskRow(
        id=task_id,
        feature_id=feature_id,
        user_id=USER_ID,
        inputs="{}",
        draft="草稿",
        final=None if finalized_at is None else "定稿",
        status="finalized" if finalized_at is not None else "succeeded",
        error=None,
        created_at=finalized_at or 1,
        diff_json=diff_json,
        finalized_at=finalized_at,
        agent_id="agent-1",
        injected_rule_ids="[]",
    )


REPLACE_DIFF = [
    {"op": "keep", "text": "报价单"},
    {"op": "replace", "draft": "客户：张总", "final": "客户：北京某某科技有限公司"},
]


class _Tasks:
    """``FeatureTaskRepo`` double — extraction only ever reads."""

    def __init__(self, rows: list[FeatureTaskRow]) -> None:
        self.rows = rows
        self.queries: list[tuple[str, int]] = []

    def list_for_feature(self, feature_id: str, limit: int = 50) -> list[FeatureTaskRow]:
        self.queries.append((feature_id, limit))
        return [row for row in self.rows if row.feature_id == feature_id][:limit]


class _Runner:
    """Stand-in for the agent turn: records the prompt, replies with canned text."""

    def __init__(self, reply: str) -> None:
        self.reply = reply
        self.prompts: list[str] = []

    async def __call__(self, prompt: str) -> str:
        self.prompts.append(prompt)
        return self.reply


# ---------------------------------------------------------------------------
# Prompt assembly (contract §1.3, §2.4)
# ---------------------------------------------------------------------------


def _legacy_prompt(feature: Feature, inputs: dict[str, Any]) -> str:
    """The pre-``rules`` rendering: template substitution and nothing else.

    Rebuilt from the same helpers the old body used, so any drift in the
    substitution step shows up as a diff against :func:`build_user_prompt`.
    """
    rendered = {
        "inputs": catalog_module._render_inputs_text(feature, inputs),
        "inputs_json": json.dumps(inputs, ensure_ascii=False, indent=2),
    }
    return catalog_module._PLACEHOLDER_RE.sub(
        lambda match: rendered.get(match.group(1), match.group(0)),
        feature.user_template,
    )


def test_prompt_without_rules_is_byte_identical_to_the_old_renderer() -> None:
    feature = _feature()
    inputs = {"topic": "季度采购", "note": "urgent"}

    expected = "整理以下输入：\n- 主题：季度采购\n- Note: urgent"

    assert build_user_prompt(feature, inputs) == expected
    assert build_user_prompt(feature, inputs, rules=None) == expected
    assert build_user_prompt(feature, inputs, rules=[]) == expected
    assert build_user_prompt(feature, inputs, rules=[ScopedRule(" "), ScopedRule("")]) == expected
    assert build_user_prompt(feature, inputs) == _legacy_prompt(feature, inputs)


def test_prompt_without_rules_matches_the_old_renderer_across_shapes() -> None:
    cases: list[tuple[Feature, dict[str, Any]]] = [
        (_feature(), {}),
        (_feature(), {"topic": "Quarterly purchase"}),
        (_feature(user_template="{{inputs_json}}\n{{unknown}}"), {"topic": "x"}),
        (
            _feature(
                user_template="Summarize {{inputs}}",
                input_schema={
                    "type": "object",
                    "properties": {
                        "rows": {"type": "array", "title": {"zh": "明细", "en": "Rows"}},
                    },
                },
                ui_schema={"order": []},
            ),
            {"rows": [["a", "b"], ["c", "d"]], "extra": 0},
        ),
    ]

    for feature, inputs in cases:
        assert build_user_prompt(feature, inputs) == _legacy_prompt(feature, inputs)


def test_rules_come_last_as_a_labeled_separate_paragraph() -> None:
    feature = _feature()
    inputs = {"topic": "季度采购"}

    prompt = build_user_prompt(
        feature,
        inputs,
        rules=[ScopedRule("客户名写全称"), ScopedRule("金额保留两位小数")],
    )

    assert prompt == (
        "整理以下输入：\n- 主题：季度采购\n\n"
        "以下要求来自历史修正记录归纳（人工审核通过），不是本次输入的一部分，请一并遵守：\n"
        "1. 客户名写全称\n"
        "2. 金额保留两位小数"
    )
    assert "历史修正记录" in prompt
    assert "不是本次输入的一部分" in prompt


def test_rules_heading_follows_the_template_language() -> None:
    feature = _feature(user_template="Summarize {{inputs}}")

    prompt = build_user_prompt(
        feature, {"topic": "Roadmap"}, rules=[ScopedRule("Spell names in full")]
    )

    assert "NOT part of the current input" in prompt
    assert "历史修正记录" not in prompt


def test_at_most_ten_rules_are_injected() -> None:
    feature = _feature()
    inputs = {"topic": "x"}
    eleven = [ScopedRule(f"规则 {index}") for index in range(1, 12)]

    ten_prompt = build_user_prompt(feature, inputs, rules=eleven[:MAX_INJECTED_RULES])
    eleven_prompt = build_user_prompt(feature, inputs, rules=eleven)

    assert MAX_INJECTED_RULES == 10
    assert eleven_prompt == ten_prompt
    assert "10. 规则 10" in eleven_prompt
    assert "规则 11" not in eleven_prompt


# ---------------------------------------------------------------------------
# Rule induction (contract §1.3)
# ---------------------------------------------------------------------------


def test_finalized_samples_keep_only_finalized_diffs_newest_first() -> None:
    rows = [
        _task("old", diff=REPLACE_DIFF, finalized_at=100),
        _task("draft-run", finalized_at=None),
        _task("new", diff=[], finalized_at=300),
        _task("corrupt", raw_diff="{not json", finalized_at=200),
    ]

    samples = finalized_samples(rows)

    assert [sample.task_id for sample in samples] == ["new", "old"]
    assert samples[0].diff == []
    assert samples[1].diff == REPLACE_DIFF


def test_extraction_prompt_carries_the_edits_and_drops_keeps() -> None:
    samples = finalized_samples(
        [
            _task("t1", diff=REPLACE_DIFF, finalized_at=300),
            _task("t2", diff=[], finalized_at=200),
        ]
    )

    prompt = build_extraction_prompt(_feature(), samples)

    assert "报价单草稿" in prompt
    assert "- 改写：「客户：张总」→「客户：北京某某科技有限公司」" in prompt
    assert "- 保留" not in prompt
    assert "人工没有做任何修改：草稿即定稿" in prompt
    assert str(MAX_EXTRACTED_RULES) in prompt


def test_parse_rule_reply_reads_json_and_rejects_junk() -> None:
    assert parse_rule_reply('["a", "b"]') == ["a", "b"]
    assert parse_rule_reply('```json\n["a", "a", "b"]\n```') == ["a", "b"]
    assert parse_rule_reply('好的：\n["a"]\n以上。') == ["a"]
    assert parse_rule_reply('{"rules": ["a"]}') == ["a"]
    assert parse_rule_reply("[]") == []
    assert len(parse_rule_reply(json.dumps([f"r{index}" for index in range(30)]))) == (
        MAX_EXTRACTED_RULES
    )

    for junk in ("抱歉，我没有发现规律。", '[{"rule": "a"}]', "", "null"):
        with pytest.raises(RuleExtractionFailed):
            parse_rule_reply(junk)


async def test_extract_writes_draft_rules_with_their_sources(
    rules: FeatureRuleRepo,
    user_id: int,
) -> None:
    tasks = _Tasks(
        [
            _task("finalized-2", diff=REPLACE_DIFF, finalized_at=300),
            _task("finalized-1", diff=[], finalized_at=200),
            _task(
                "unfinalized",
                diff=[{"op": "add", "text": "还没定稿的内容"}],
                finalized_at=None,
            ),
        ]
    )
    runner = _Runner('["客户名写全称", "金额保留两位小数"]')

    created = await extract_rules(
        repo=rules,
        tasks_repo=tasks,  # type: ignore[arg-type]
        feature=_feature(),
        runner=runner,
        owner_user_id=user_id,
        user_ids=[user_id],
    )

    assert [row.rule_text for row in created] == ["客户名写全称", "金额保留两位小数"]
    assert {row.status for row in created} == {DRAFT}
    assert {row.proposed_by for row in created} == {"ai"}
    assert all(row.approved_by is None and row.reviewed_at is None for row in created)
    assert {tuple(row.source_task_id_list()) for row in created} == {
        ("finalized-2", "finalized-1"),
    }

    stored = rules.list_for_feature("quote-draft")
    assert {row.rule_text for row in stored} == {"客户名写全称", "金额保留两位小数"}
    assert all(row.source_task_id_list() for row in stored)

    assert tasks.queries[0][0] == "quote-draft"
    assert tasks.queries[0][1] >= MAX_SOURCE_TASKS
    prompt = runner.prompts[0]
    assert "北京某某科技有限公司" in prompt
    assert "还没定稿的内容" not in prompt


async def test_extract_without_finalized_tasks_never_calls_the_agent(
    rules: FeatureRuleRepo,
) -> None:
    tasks = _Tasks([_task("draft-run", finalized_at=None)])
    runner = _Runner('["不应该被调用"]')

    with pytest.raises(RuleNoSamples):
        await extract_rules(
            repo=rules,
            tasks_repo=tasks,  # type: ignore[arg-type]
            feature=_feature(),
            runner=runner,
            owner_user_id=USER_ID,
            user_ids=[USER_ID],
        )

    assert runner.prompts == []
    assert rules.list_for_feature("quote-draft") == []


async def test_unparsable_reply_writes_no_half_baked_rules(rules: FeatureRuleRepo) -> None:
    tasks = _Tasks([_task("finalized-1", diff=REPLACE_DIFF)])

    with pytest.raises(RuleExtractionFailed):
        await extract_rules(
            repo=rules,
            tasks_repo=tasks,  # type: ignore[arg-type]
            feature=_feature(),
            runner=_Runner("我认为不需要总结规则。"),
            owner_user_id=USER_ID,
            user_ids=[USER_ID],
        )

    assert rules.list_for_feature("quote-draft") == []


async def test_extract_can_legitimately_find_no_rules(rules: FeatureRuleRepo) -> None:
    tasks = _Tasks([_task("finalized-1", diff=[])])

    created = await extract_rules(
        repo=rules,
        tasks_repo=tasks,  # type: ignore[arg-type]
        feature=_feature(),
        runner=_Runner("[]"),
        owner_user_id=USER_ID,
        user_ids=[USER_ID],
    )

    assert created == []
    assert rules.list_for_feature("quote-draft") == []


# ---------------------------------------------------------------------------
# Review (contract §2.3)
# ---------------------------------------------------------------------------


def _draft(rules: FeatureRuleRepo, text: str = "客户名写全称", task_id: str = "task-1") -> str:
    return rules.create_many(
        feature_id="quote-draft",
        rule_texts=[text],
        source_task_ids=[task_id],
        proposed_by="ai",
    )[0].id


def test_approve_records_the_reviewer_and_time(rules: FeatureRuleRepo, user_id: int) -> None:
    rule_id = _draft(rules)

    row = review_rule(rules, rule_id, approve=True, reviewer_id=user_id)

    assert row.status == APPROVED
    assert row.approved_by == user_id
    assert row.reviewed_at is not None
    assert row.source_task_id_list() == ["task-1"]


def test_reject_retires_the_rule(rules: FeatureRuleRepo, user_id: int) -> None:
    rule_id = _draft(rules)

    row = review_rule(rules, rule_id, approve=False, reviewer_id=user_id)

    assert row.status == REJECTED
    assert row.approved_by == user_id
    assert row.reviewed_at is not None


@pytest.mark.parametrize("first", [True, False])
def test_reviewed_rules_keep_their_decision(
    rules: FeatureRuleRepo,
    user_id: int,
    first: bool,
) -> None:
    rule_id = _draft(rules)
    decided = review_rule(rules, rule_id, approve=first, reviewer_id=user_id)

    with pytest.raises(RuleAlreadyReviewed):
        review_rule(rules, rule_id, approve=not first, reviewer_id=user_id + 1)
    with pytest.raises(RuleAlreadyReviewed):
        review_rule(rules, rule_id, approve=first, reviewer_id=user_id + 1)

    after = rules.get(rule_id)
    assert after is not None
    assert after.status == decided.status
    assert after.approved_by == user_id
    assert after.reviewed_at == decided.reviewed_at


def test_review_unknown_rule_is_not_found(rules: FeatureRuleRepo) -> None:
    with pytest.raises(RuleNotFound):
        review_rule(rules, "missing", approve=True, reviewer_id=USER_ID)


# ---------------------------------------------------------------------------
# Injection assembly (contract §1.3, §2.4)
# ---------------------------------------------------------------------------


def test_injectable_rule_rows_are_approved_newest_first_and_capped(
    rules: FeatureRuleRepo,
    user_id: int,
) -> None:
    approved_ids: list[str] = []
    for index in range(12):
        rule_id = _draft(rules, text=f"规则 {index}", task_id=f"task-{index}")
        rules.mark_reviewed(
            rule_id,
            status=APPROVED,
            approved_by=user_id,
            reviewed_at=1_000 + index,
        )
        approved_ids.append(rule_id)
    pending = _draft(rules, text="待审规则")
    rejected = _draft(rules, text="被否规则")
    rules.mark_reviewed(rejected, status=REJECTED, approved_by=user_id, reviewed_at=1_100)
    rules.create_many(
        feature_id="meeting-notes",
        rule_texts=["别的功能的规则"],
        source_task_ids=["task-x"],
        proposed_by="ai",
    )
    rules.mark_reviewed(
        rules.list_for_feature("meeting-notes")[0].id,
        status=APPROVED,
        approved_by=user_id,
        reviewed_at=1_200,
    )

    injectable = injectable_rule_rows(rules, "quote-draft")

    assert len(injectable) == MAX_INJECTED_RULES
    assert [row.rule_text for row in injectable] == [f"规则 {index}" for index in range(11, 1, -1)]
    # Rows, not just texts: the run records these ids as what it injected, so the
    # order and the cap have to be the same thing the prompt saw.
    assert [row.id for row in injectable] == approved_ids[11:1:-1]
    texts = [row.rule_text for row in injectable]
    assert "待审规则" not in texts
    assert "被否规则" not in texts
    assert "别的功能的规则" not in texts
    pending_row = rules.get(pending)
    assert pending_row is not None and pending_row.status == DRAFT


# ---------------------------------------------------------------------------
# Wiring: the run endpoint feeds approved rules into the prompt
# ---------------------------------------------------------------------------


class _FakeGateway:
    def __init__(self) -> None:
        self.thread_registry = SimpleNamespace()

    def require_session(self, agent_id: str, session_key: str) -> Any:
        return SimpleNamespace(thread_id="thread-1", user_id=USER_ID, channel_type="dashboard")

    async def run_in_session(self, agent_id: str, session_key: str, operation: Any) -> None:
        await operation()


class _FakeAgentRegistry:
    def __init__(self, *, running: bool = True) -> None:
        self.requests: list[dict[str, Any]] = []
        self._running = running
        self.started: list[str] = []

    def list_agents(self, user_id: int) -> list[Any]:
        return [SimpleNamespace(agent_id="agent-1")]

    def get_agent(self, agent_id: str) -> Any:
        """Live-registry lookup; mirrors the real registry's not-running error."""
        if not self._running:
            raise OctopError(ErrorCode.AGENT_NOT_RUNNING, f"agent {agent_id!r} is not running")
        return object()

    async def start(self, agent_id: str) -> None:
        self.started.append(agent_id)
        self._running = True

    async def stream(self, agent_id: str, request: dict[str, Any]) -> Any:
        self.requests.append(request)
        yield {"type": "token", "content": "草稿正文"}


def _run_body_server(
    *,
    feature: Feature,
    rules: FeatureRuleRepo,
    db: SqlitePool,
) -> tuple[Any, _FakeAgentRegistry]:
    registry = _FakeAgentRegistry()
    server = SimpleNamespace(
        feature_catalog=SimpleNamespace(list=lambda: [feature], get={feature.id: feature}.get),
        app_runtime=SimpleNamespace(gateway=_FakeGateway(), agent_registry=registry),
        services=SimpleNamespace(
            repos=SimpleNamespace(
                feature_tasks_repo=FeatureTaskRepo(db),
                feature_rules_repo=rules,
            ),
        ),
    )
    return server, registry


async def test_run_feature_injects_approved_rules_only(
    db: SqlitePool,
    rules: FeatureRuleRepo,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _resolve(**kwargs: Any) -> tuple[str, str]:
        return "thread-1", "dashboard:agent-1:7"

    monkeypatch.setattr(features_router, "resolve_thread_id", _resolve)
    feature = _feature()
    approved = _draft(rules, text="客户名写全称")
    rules.mark_reviewed(approved, status=APPROVED, approved_by=user_id, reviewed_at=1_000)
    _draft(rules, text="待审规则")
    server, registry = _run_body_server(feature=feature, rules=rules, db=db)

    payload = await features_router.run_feature(
        feature.id,
        features_router.FeatureRunBody(inputs={"topic": "季度采购"}),
        SimpleNamespace(id=user_id),
        server,
    )

    sent = registry.requests[0]["messages"][0]["content"]
    assert "客户名写全称" in sent
    assert "历史修正记录" in sent
    assert "待审规则" not in sent
    assert payload["output"] == "草稿正文"


async def test_run_feature_prompt_is_unchanged_when_nothing_is_approved(
    db: SqlitePool,
    rules: FeatureRuleRepo,
    user_id: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _resolve(**kwargs: Any) -> tuple[str, str]:
        return "thread-1", "dashboard:agent-1:7"

    monkeypatch.setattr(features_router, "resolve_thread_id", _resolve)
    feature = _feature()
    _draft(rules, text="待审规则")
    server, registry = _run_body_server(feature=feature, rules=rules, db=db)

    await features_router.run_feature(
        feature.id,
        features_router.FeatureRunBody(inputs={"topic": "季度采购"}),
        SimpleNamespace(id=user_id),
        server,
    )

    sent = registry.requests[0]["messages"][0]["content"]
    assert sent == build_user_prompt(feature, {"topic": "季度采购"})
    assert sent == "整理以下输入：\n- 主题：季度采购"
