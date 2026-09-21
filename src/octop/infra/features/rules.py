"""Rule extraction, human review, and prompt injection (M4 self-improvement).

The learning loop this serves: run a feature → a human edits the draft → the edit
is finalized as a segment diff → an agent induces candidate rules from those
diffs → a human approves or rejects each rule → approved rules ride along on
later prompts for that feature only.

Nothing past the induction step is automatic: a draft rule is inert until a
person approves it, and rules never cross feature boundaries.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.feature_rules import (
    APPROVED,
    DRAFT,
    REJECTED,
    FeatureRuleRepo,
    FeatureRuleRow,
)
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo, FeatureTaskRow
from octop.infra.features.catalog import MAX_INJECTED_RULES, Feature

logger = logging.getLogger(__name__)

MAX_SOURCE_TASKS = 20
"""Finalized tasks one extraction reads — the most recent corrections."""

TASK_SCAN_LIMIT = 200
"""How many recent feature tasks extraction pulls before keeping the finalized ones.

``FeatureTaskRepo.list_for_feature`` applies its limit before anything can filter
on ``finalized_at``, so the window is deliberately wider than
:data:`MAX_SOURCE_TASKS` rather than exactly equal to it.
"""

MAX_EXTRACTED_RULES = 8
"""Rules one extraction may propose.

The prompt asks for at most this many and the reply is truncated to it, so a
model that repeats itself cannot flood the review queue. It stays below
:data:`~octop.infra.features.catalog.MAX_INJECTED_RULES` so approving a whole
extraction still fits one prompt.
"""

PROPOSED_BY_AI = "ai"
"""``proposed_by`` value for rules the extractor wrote (humans use ``user:<id>``)."""

ExtractionRunner = Callable[[str], Awaitable[str]]
"""Runs one non-interactive agent turn and returns its visible text."""

_FENCE_RE = re.compile(r"^```[a-zA-Z0-9_-]*\s*|\s*```$")
_DIFF_OP_LABELS = {"add": "新增", "remove": "删除", "replace": "改写"}


class RuleError(Exception):
    """Base class for rule domain failures."""


class RuleNotFound(RuleError):
    """No rule with that id."""


class RuleAlreadyReviewed(RuleError):
    """The rule already has a review decision; decisions are final."""


class RuleNoSamples(RuleError):
    """The feature has no finalized task diffs to induce rules from."""


class RuleExtractionFailed(RuleError):
    """The extractor reply could not be turned into rules."""


@dataclass(frozen=True)
class RuleSample:
    """One finalized task's correction, ready to render into a prompt."""

    task_id: str
    diff: list[dict[str, str]]


def finalized_samples(
    tasks: Sequence[FeatureTaskRow],
    *,
    limit: int = MAX_SOURCE_TASKS,
) -> list[RuleSample]:
    """The evidence an extraction reads: finalized diffs, newest finalized first.

    A row whose ``diff_json`` is unreadable is skipped with a warning — one
    corrupt row must not block learning from every other correction, and it is
    never silently counted as a "no edits" positive sample.
    """
    finalized = [task for task in tasks if task.finalized_at is not None]
    finalized.sort(key=lambda task: (task.finalized_at or 0, task.id), reverse=True)

    samples: list[RuleSample] = []
    for task in finalized[:limit]:
        diff = _parse_diff(task)
        if diff is None:
            logger.warning("feature task %s finalized but diff_json is unusable", task.id)
            continue
        samples.append(RuleSample(task_id=task.id, diff=diff))
    return samples


def build_extraction_prompt(feature: Feature, samples: Sequence[RuleSample]) -> str:
    """The one-shot extraction prompt: feature context plus correction evidence.

    ``keep`` segments stay out of the prompt: they are what the human accepted
    unchanged, which costs length without naming a rule. A task with an empty
    diff is the opposite — a draft the human accepted as-is is the strongest
    positive sample there is, so it is called out explicitly.
    """
    heading = feature.label.get("zh") or feature.label.get("en") or feature.id
    description = feature.description.get("zh") or feature.description.get("en") or ""
    lines = [f"你在为企业功能「{heading}」归纳写作要求。"]
    if description:
        lines += ["", f"功能说明：{description}"]
    lines += [
        "",
        "以下是人工审核过的历史修正记录，每条记录里的“草稿 → 定稿”差异就是人真正改动的地方：",
        "",
    ]
    for index, sample in enumerate(samples, start=1):
        lines += _render_sample(index, sample)
        lines.append("")
    lines += [
        "请归纳出下次生成时应遵守的写作要求：",
        "- 每条一句话，能直接执行",
        "- 只写可推广到同类输入的规律，不要复述某一次的具体内容",
        "- 不要空话（例如“注意检查”），不要评论改了几处",
        f"- 最多 {MAX_EXTRACTED_RULES} 条；没有可归纳的规律就输出 []",
        "",
        "输出：只输出 JSON 数组，不要解释、不要代码块标记，例如：",
        '["客户名写全称", "金额一律保留两位小数"]',
    ]
    return "\n".join(lines)


def parse_rule_reply(reply: str) -> list[str]:
    """Rule texts from an extractor reply, in reply order and de-duplicated.

    The reply must carry a JSON array of strings; anything else raises
    :class:`RuleExtractionFailed` so the caller can bail out before writing. An
    empty array is a valid answer — the prompt asks for ``[]`` when the diffs
    teach nothing generalizable — and yields no rules rather than an error.
    """
    payload = _json_array(reply)
    if payload is None:
        raise RuleExtractionFailed("extractor reply is not a JSON array")
    rules: list[str] = []
    for item in payload:
        text = item.strip() if isinstance(item, str) else ""
        if text and text not in rules:
            rules.append(text)
    if not rules and payload:
        raise RuleExtractionFailed("extractor reply array held no rule text")
    if len(rules) > MAX_EXTRACTED_RULES:
        logger.warning("extractor proposed %d rules; keeping %d", len(rules), MAX_EXTRACTED_RULES)
        rules = rules[:MAX_EXTRACTED_RULES]
    return rules


async def extract_rules(
    *,
    repo: FeatureRuleRepo,
    tasks_repo: FeatureTaskRepo,
    feature: Feature,
    runner: ExtractionRunner,
) -> list[FeatureRuleRow]:
    """Induce draft rules for *feature* from its finalized task diffs.

    The agent runs and the reply is parsed **before** the first insert, so a
    failed or unparsable extraction writes nothing. Every written rule carries
    the ids of the diffs it was induced from (contract §1.3: no rule without
    provenance) and lands in ``draft`` — a human decides whether it is used.
    """
    samples = finalized_samples(
        tasks_repo.list_for_feature(feature.id, limit=TASK_SCAN_LIMIT),
    )
    if not samples:
        raise RuleNoSamples(f"feature {feature.id!r} has no finalized tasks to learn from")
    reply = await runner(build_extraction_prompt(feature, samples))
    rules = parse_rule_reply(reply)
    return repo.create_many(
        feature_id=feature.id,
        rule_texts=rules,
        source_task_ids=[sample.task_id for sample in samples],
        proposed_by=PROPOSED_BY_AI,
    )


def review_rule(
    repo: FeatureRuleRepo,
    rule_id: str,
    *,
    approve: bool,
    reviewer_id: int,
) -> FeatureRuleRow:
    """Approve or reject one draft rule and return the stored row.

    A rule that already has a decision keeps it: re-reviewing would rewrite the
    audit trail that tells us which rules a prompt was built from. ``approved_by``
    records whoever made the call, for rejections included.
    """
    existing = repo.get(rule_id)
    if existing is None:
        raise RuleNotFound(f"rule {rule_id!r} not found")
    if existing.status != DRAFT:
        raise RuleAlreadyReviewed(
            f"rule {rule_id!r} was already reviewed ({existing.status})",
        )
    updated = repo.mark_reviewed(
        rule_id,
        status=APPROVED if approve else REJECTED,
        approved_by=reviewer_id,
    )
    if updated is None:
        raise RuleAlreadyReviewed(f"rule {rule_id!r} was already reviewed")
    return updated


def injectable_rule_rows(repo: FeatureRuleRepo, feature_id: str) -> list[FeatureRuleRow]:
    """Approved rules for one feature — the only rules a prompt may carry.

    Newest-reviewed first, capped at
    :data:`~octop.infra.features.catalog.MAX_INJECTED_RULES` so the prompt block
    cannot grow without bound as the rule set ages. Rows rather than texts: the
    caller records *which* rules a run injected (``feature_tasks.injected_rule_ids``),
    and a prompt that drops blank text would otherwise disagree with the record.
    """
    rows = repo.list_approved(feature_id, limit=MAX_INJECTED_RULES)
    return [row for row in rows if str(row.rule_text).strip()]


def _parse_diff(task: FeatureTaskRow) -> list[dict[str, str]] | None:
    """Decode one stored segment diff; ``None`` when the column is unusable."""
    raw = task.diff_json
    if raw is None:
        return None
    try:
        parsed = json.loads(raw)
    except ValueError:
        return None
    if not isinstance(parsed, list):
        return None
    segments: list[dict[str, str]] = []
    for segment in parsed:
        if not isinstance(segment, Mapping):
            return None
        segments.append({str(key): str(value) for key, value in segment.items()})
    return segments


def _render_sample(index: int, sample: RuleSample) -> list[str]:
    """One evidence block: the human's edits, ``keep`` segments dropped."""
    lines = [f"## 记录 {index}"]
    if not sample.diff:
        lines.append("（人工没有做任何修改：草稿即定稿）")
        return lines
    for segment in sample.diff:
        op = segment.get("op", "")
        if op in ("add", "remove"):
            lines.append(f"- {_DIFF_OP_LABELS[op]}：{_collapse(segment.get('text'))}")
        elif op == "replace":
            lines.append(
                f"- 改写：「{_collapse(segment.get('draft'))}」→「{_collapse(segment.get('final'))}」",
            )
    return lines


def _collapse(text: Any) -> str:
    """Flatten a segment to one line so the prompt stays readable."""
    return " ".join(str(text or "").split())


def _json_array(text: str) -> list[Any] | None:
    """The JSON array inside *text*, tolerating prose around it or a code fence."""
    body = _FENCE_RE.sub("", text.strip()).strip()
    start, end = body.find("["), body.rfind("]")
    candidates = [body]
    if start >= 0 and end > start:
        candidates.append(body[start : end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, list):
            return parsed
    return None
