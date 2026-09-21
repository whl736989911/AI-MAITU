"""Enterprise feature directory — cards, definitions, and one-shot runs.

Feature definitions live on disk (:mod:`octop.infra.features`); every run is
logged to the ``feature_tasks`` table so M4 self-improvement has the raw
inputs/drafts, including the failed runs.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from octop.api.deps import get_server, require_permission
from octop.api.routers.chat.turn import resolve_thread_id
from octop.infra.agents.middleware.feature_prompt import stamp_feature_system_prompt
from octop.infra.db.repos.feature_cases import FeatureCaseRow
from octop.infra.db.repos.feature_rules import FeatureRuleRow
from octop.infra.db.repos.feature_tasks import FeatureTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import Feature, build_user_prompt
from octop.infra.features.diff import diff_segments
from octop.infra.features.rules import (
    RuleAlreadyReviewed,
    RuleExtractionFailed,
    RuleNoSamples,
    RuleNotFound,
    extract_rules,
    injectable_rule_rows,
    review_rule,
)
from octop.infra.gateway.process import build_harness_request
from octop.infra.utils.llm_text import strip_thinking

logger = logging.getLogger(__name__)

router = APIRouter()

# Chunk types that carry visible assistant text (same set cron delivery consumes).
_TEXT_CHUNK_TYPES = ("token", "delta")


def _is_agent_running(server: Any, agent_id: str) -> bool:
    """Whether the harness already holds a live agent for ``agent_id``.

    Asks the live registry rather than the ``agents.last_state`` column: the row
    can say ``running`` while the in-memory handle is gone (restart, failed
    start), and ``stream`` needs the handle, not the row.
    """
    try:
        server.app_runtime.agent_registry.get_agent(agent_id)
    except OctopError:
        return False
    return True


class FeatureRunBody(BaseModel):
    """Form values collected by the schema-driven UI."""

    inputs: dict[str, Any] = Field(default_factory=dict)


class _FeatureRunFailed(RuntimeError):
    """A run that finished without usable output (HITL or empty text)."""


def _summary_dict(feature: Feature) -> dict[str, Any]:
    """Card-level projection — no schemas, no prompt plumbing."""
    return {
        "id": feature.id,
        "version": feature.version,
        "label": dict(feature.label),
        "description": dict(feature.description),
        "icon_name": feature.icon_name,
        "color": feature.color,
        "unit": feature.unit,
        "output_kind": feature.output_kind,
        "permissions": dict(feature.permissions),
    }


def _feature_dict(feature: Feature) -> dict[str, Any]:
    """Full definition, including everything a schema-driven form needs."""
    data = _summary_dict(feature)
    data.update(
        {
            "input_schema": feature.input_schema,
            "ui_schema": feature.ui_schema,
            "user_template": feature.user_template,
            "system_prompt": feature.system_prompt,
        }
    )
    return data


def _unit_counts(features: list[Feature]) -> list[dict[str, Any]]:
    """``[{"key", "count"}]`` in catalog order — the UI's grouping order."""
    counts: dict[str, int] = {}
    for feature in features:
        counts[feature.unit] = counts.get(feature.unit, 0) + 1
    return [{"key": unit, "count": count} for unit, count in counts.items()]


def _catalog_features(server: Any) -> list[Feature]:
    catalog = server.feature_catalog
    return [] if catalog is None else catalog.list()


def _require_feature(server: Any, feature_id: str) -> Feature:
    catalog = server.feature_catalog
    feature = None if catalog is None else catalog.get(feature_id)
    if feature is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature {feature_id!r} not found")
    return feature


def _run_agent_id(server: Any, user_id: int) -> str:
    """The user's own primary (oldest enabled) agent; runs never create one."""
    rows = server.app_runtime.agent_registry.list_agents(user_id)
    if not rows:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"no runnable agent for user {user_id}")
    return str(rows[0].agent_id)


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, _FeatureRunFailed):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


async def _run_agent_turn(
    server: Any,
    *,
    agent_id: str,
    user_id: int,
    text: str,
    system_prompt: str | None = None,
) -> str:
    """Run one non-interactive agent turn and return its visible text.

    *system_prompt* is the feature's own ``prompt.system_file`` text. It rides on
    this request only (see :mod:`octop.infra.agents.middleware.feature_prompt`):
    the agent's persisted ``system_prompt`` — the user's own configuration — is
    never touched, and neither is the thread, because the text is not part of the
    messages the checkpointer stores. ``None`` (the default) leaves the request
    exactly as it was before feature system prompts existed.
    """
    gateway = server.app_runtime.gateway
    _thread_id, session_key = await resolve_thread_id(
        agent_id=agent_id,
        user_id=user_id,
        thread_registry=gateway.thread_registry,
        thread_id=None,
        session_key=None,
    )
    session = gateway.require_session(agent_id, session_key)
    # A feature run is the entry point for someone who may never have opened the
    # chat, so nothing has started this agent yet. ``stream`` only reaches agents
    # already in the harness registry, so boot it here the same way the agents
    # router does (``agents.py`` calling ``agent_registry.start``).
    if not _is_agent_running(server, agent_id):
        await server.app_runtime.agent_registry.start(agent_id)
    request = build_harness_request(
        thread_id=session.thread_id,
        user_id=session.user_id,
        agent_id=agent_id,
        session_key=session_key,
        source=session.channel_type,
        text=text,
        model=None,
        message_kwargs=None,
    )
    stamp_feature_system_prompt(request, system_prompt)
    output: str | None = None

    async def _stream() -> None:
        nonlocal output
        parts: list[str] = []
        interaction_required = False
        async for chunk in server.app_runtime.agent_registry.stream(agent_id, request):
            if chunk.get("type") in _TEXT_CHUNK_TYPES:
                parts.append(str(chunk.get("content") or chunk.get("text") or ""))
            elif chunk.get("type") == "hitl_required":
                interaction_required = True
        if interaction_required:
            raise _FeatureRunFailed("feature run requires user interaction")
        outbound = strip_thinking("".join(parts)).strip()
        if not outbound:
            raise _FeatureRunFailed("feature run produced no visible output")
        output = outbound

    await gateway.run_in_session(agent_id, session_key, _stream)
    assert output is not None
    return output


@router.get("", summary="List enterprise features")
async def list_features(
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """List feature cards plus the organization-unit grouping order."""
    features = _catalog_features(server)
    return {
        "features": [_summary_dict(feature) for feature in features],
        "units": _unit_counts(features),
    }


@router.get("/{feature_id}", summary="Get one feature definition")
async def get_feature(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return the full definition backing the schema-driven run form."""
    return _feature_dict(_require_feature(server, feature_id))


@router.post("/{feature_id}/run", summary="Run a feature once")
async def run_feature(
    feature_id: str,
    body: FeatureRunBody,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Run the feature with the user's own agent and log the outcome.

    Both outcomes land in ``feature_tasks`` — a failed run is the training
    signal M4 needs, so the row is written before the error is raised. The row
    carries the run's snapshot too (which agent ran it, which approved rules
    went into the prompt): a failed run needs that diagnosis as much as a
    successful one, and nothing else records it.
    """
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    agent_id = _run_agent_id(server, user.id)
    repo = server.services.repos.feature_tasks_repo
    inputs = json.dumps(body.inputs, ensure_ascii=False)
    rules = injectable_rule_rows(server.services.repos.feature_rules_repo, feature.id)
    rule_ids = json.dumps([rule.id for rule in rules], ensure_ascii=False)
    try:
        output = await _run_agent_turn(
            server,
            agent_id=agent_id,
            user_id=user.id,
            text=build_user_prompt(
                feature,
                body.inputs,
                rules=[rule.rule_text for rule in rules],
            ),
            system_prompt=feature.system_prompt,
        )
    except Exception as exc:
        reason = _failure_reason(exc)
        repo.create(
            feature_id=feature.id,
            user_id=user.id,
            inputs=inputs,
            status="failed",
            error=reason,
            agent_id=agent_id,
            injected_rule_ids=rule_ids,
        )
        logger.warning("feature %s run failed for user %s: %s", feature.id, user.id, reason)
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"feature {feature.id!r} run failed: {reason}",
        ) from exc
    row = repo.create(
        feature_id=feature.id,
        user_id=user.id,
        inputs=inputs,
        status="succeeded",
        draft=output,
        agent_id=agent_id,
        injected_rule_ids=rule_ids,
    )
    return {"task_id": row.id, "output": output, "output_kind": feature.output_kind}


def _rule_dict(row: FeatureRuleRow) -> dict[str, Any]:
    """Rule payload for the review UI — provenance included."""
    return {
        "id": row.id,
        "feature_id": row.feature_id,
        "rule_text": row.rule_text,
        "status": row.status,
        "source_task_ids": row.source_task_id_list(),
        "proposed_by": row.proposed_by,
        "approved_by": row.approved_by,
        "created_at": row.created_at,
        "reviewed_at": row.reviewed_at,
    }


def _review_one(server: Any, rule_id: str, *, approve: bool, reviewer_id: int) -> dict[str, Any]:
    """Shared body of approve/reject — the reviewer is always the caller."""
    assert server.services is not None
    try:
        row = review_rule(
            server.services.repos.feature_rules_repo,
            rule_id,
            approve=approve,
            reviewer_id=reviewer_id,
        )
    except RuleNotFound as exc:
        raise OctopError(ErrorCode.NOT_FOUND, str(exc)) from exc
    except RuleAlreadyReviewed as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_REVIEWED, str(exc)) from exc
    return _rule_dict(row)


@router.post("/{feature_id}/rules/extract", summary="Induce draft rules from finalized tasks")
async def extract_feature_rules(
    feature_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Run the extractor once over this feature's finalized corrections.

    Output is always ``draft``: the extractor proposes, a human disposes. A run
    that cannot be parsed writes nothing at all.
    """
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    agent_id = _run_agent_id(server, user.id)

    async def runner(prompt: str) -> str:
        # ``extract_rules`` calls the runner with the prompt positionally, while
        # ``_run_agent_turn`` takes it as keyword-only — so adapt here instead of
        # binding it into the partial (that made the prompt a second positional
        # argument and raised TypeError on every real extraction).
        return await _run_agent_turn(server, agent_id=agent_id, user_id=user.id, text=prompt)

    try:
        rows = await extract_rules(
            repo=server.services.repos.feature_rules_repo,
            tasks_repo=server.services.repos.feature_tasks_repo,
            feature=feature,
            runner=runner,
        )
    except OctopError:
        raise
    except RuleNoSamples as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_NO_SAMPLES, str(exc)) from exc
    except RuleExtractionFailed as exc:
        logger.warning("rule extraction for feature %s produced nothing: %s", feature.id, exc)
        raise OctopError(ErrorCode.FEATURE_RULE_EXTRACTION_FAILED, str(exc)) from exc
    except Exception as exc:
        reason = _failure_reason(exc)
        logger.warning("rule extraction failed for feature %s: %s", feature.id, reason)
        raise OctopError(
            ErrorCode.FEATURE_RULE_EXTRACTION_FAILED,
            f"rule extraction for feature {feature.id!r} failed: {reason}",
        ) from exc
    return {"feature_id": feature.id, "rules": [_rule_dict(row) for row in rows]}


@router.get("/{feature_id}/rules", summary="List rules of one feature")
async def list_feature_rules(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Every rule of this feature, newest first — unreviewed drafts included."""
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    rows = server.services.repos.feature_rules_repo.list_for_feature(feature.id)
    return {"feature_id": feature.id, "rules": [_rule_dict(row) for row in rows]}


@router.post("/rules/{rule_id}/approve", summary="Approve a draft rule")
async def approve_feature_rule(
    rule_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Let an approved rule into future prompts of its feature."""
    return _review_one(server, rule_id, approve=True, reviewer_id=user.id)


@router.post("/rules/{rule_id}/reject", summary="Reject a draft rule")
async def reject_feature_rule(
    rule_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Retire a rule for good — rejected rules are never injected."""
    return _review_one(server, rule_id, approve=False, reviewer_id=user.id)


class FeatureFinalizeBody(BaseModel):
    """The initiator's approved text for one run."""

    final: str


class FeaturePromoteBody(BaseModel):
    """Provenance note stored alongside a promoted case."""

    note: str | None = None


def _diff_payload(raw: str | None) -> list[dict[str, str]]:
    """Stored ``diff_json`` as the structure M4 specifies (``[]`` before finalize)."""
    return [] if raw is None else json.loads(raw)


def _finalized_dict(row: FeatureTaskRow) -> dict[str, Any]:
    """The captured signal: approved text plus the computed draft→final diff."""
    return {
        "task_id": row.id,
        "feature_id": row.feature_id,
        "status": row.status,
        "final": row.final,
        "diff": _diff_payload(row.diff_json),
        "finalized_at": row.finalized_at,
    }


def _case_dict(case: FeatureCaseRow) -> dict[str, Any]:
    """One promoted case plus the task content it references (never a copy)."""
    return {
        "task_id": case.task_id,
        "feature_id": case.feature_id,
        "promoted_by": case.promoted_by,
        "promoted_at": case.promoted_at,
        "note": case.note,
        "inputs": json.loads(case.inputs),
        "final": case.final,
        "diff": _diff_payload(case.diff_json),
    }


def _require_task_actor(row: FeatureTaskRow, user: Any) -> None:
    """Finalizing/promoting a run is the initiator's own call; admins may step in."""
    if row.user_id != int(user.id) and not user.is_admin:
        raise OctopError(ErrorCode.FORBIDDEN, f"feature task {row.id!r} belongs to another user")


@router.post("/tasks/{task_id}/finalize", summary="Finalize a feature run")
async def finalize_feature_task(
    task_id: str,
    body: FeatureFinalizeBody,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Store the human-approved text and the draft→final diff it replaced.

    The diff is computed once, here, and stored: rule induction then scans
    stored diffs instead of recomputing history. Finalizing is one-way (a
    finalized run is the anchor of the learning signal), and only the initiator
    — or an admin — may do it.
    """
    assert server.services is not None
    repo = server.services.repos.feature_tasks_repo
    row = repo.get(task_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature task {task_id!r} not found")
    _require_task_actor(row, user)
    diff = diff_segments(row.draft or "", body.final)
    finalized = repo.finalize(
        task_id,
        final=body.final,
        diff_json=json.dumps(diff, ensure_ascii=False),
    )
    if finalized is None:
        # The conditional UPDATE matched no row: someone finalized it first.
        raise OctopError(
            ErrorCode.FEATURE_TASK_FINALIZED, f"feature task {task_id!r} is already finalized"
        )
    return _finalized_dict(finalized)


@router.post("/tasks/{task_id}/promote", summary="Promote a run into the case library")
async def promote_feature_task(
    task_id: str,
    body: FeaturePromoteBody | None = None,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Promote one finalized run into this feature's case library by hand.

    Nothing is promoted automatically: cases are fed back into prompts as
    few-shot examples, so a mediocre sample nobody reviewed is worse than a
    missing one. Only the task's content is referenced — never duplicated.
    """
    assert server.services is not None
    repo = server.services.repos.feature_tasks_repo
    row = repo.get(task_id)
    if row is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature task {task_id!r} not found")
    _require_task_actor(row, user)
    if row.finalized_at is None:
        raise OctopError(
            ErrorCode.FEATURE_TASK_NOT_FINALIZED,
            f"feature task {task_id!r} has not been finalized",
        )
    case = server.services.repos.feature_cases_repo.promote(
        task_id,
        feature_id=row.feature_id,
        promoted_by=user.id,
        note=body.note if body is not None else None,
    )
    if case is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature task {task_id!r} not found")
    return {"case": _case_dict(case)}


@router.get("/{feature_id}/cases", summary="List promoted cases of one feature")
async def list_feature_cases(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """The feature's case library, newest promotion first."""
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    cases = server.services.repos.feature_cases_repo.list_for_feature(feature.id)
    return {"feature_id": feature.id, "cases": [_case_dict(case) for case in cases]}
