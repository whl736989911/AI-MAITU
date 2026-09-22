"""Enterprise feature directory — cards, definitions, and one-shot runs.

Feature definitions live on disk (:mod:`octop.infra.features`); every run is
logged to the ``feature_tasks`` table so M4 self-improvement has the raw
inputs/drafts, including the failed runs.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import get_server, require_admin, require_permission
from octop.api.routers.chat.turn import resolve_thread_id
from octop.infra.agents.middleware.feature_dispatch import (
    close_ledger,
    open_ledger,
    stamp_dispatch,
)
from octop.infra.agents.middleware.feature_prompt import stamp_feature_system_prompt
from octop.infra.agents.middleware.feature_scope import FeatureRunScope, stamp_feature_scope
from octop.infra.agents.providers.store import enabled_model_refs
from octop.infra.agents.tool_catalog import BUILTIN_TOOL_CATALOG, CRITICAL_TOOLS
from octop.infra.db.repos.feature_cases import FeatureCaseRow
from octop.infra.db.repos.feature_rules import (
    SCOPE_PERSONAL,
    SCOPE_UNIT,
    FeatureRuleRow,
)
from octop.infra.db.repos.feature_tasks import FeatureTaskRow
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.features import (
    CapabilityUnavailable,
    Feature,
    FeatureAlreadyExists,
    FeatureCatalog,
    FeatureDefinitionInvalid,
    FeatureNotFound,
    FeatureReadOnly,
    FeatureStore,
    ResolvedCapability,
    ScopedRule,
    build_user_prompt,
    capability_from_audit,
    render_step_prompt,
    resolve_capability,
    stamp_capability,
)
from octop.infra.features.diff import diff_segments
from octop.infra.features.dispatch import (
    DEFAULT_MAX_PARALLEL,
    MAX_DISPATCH_RESULT_CHARS,
    DispatchEntry,
    decomposition_dict,
    refuse_undispatchable,
    require_role_dispatch,
    resolve_max_parallel,
)
from octop.infra.features.personalization import (
    ensure_feature_agent,
    materialized_feature_agent_id,
)
from octop.infra.features.rules import (
    RuleAlreadyReviewed,
    RuleExtractionFailed,
    RuleNoSamples,
    RuleNotFound,
    RuleScopeForbidden,
    RuleScopeInvalid,
    extract_rules,
    injectable_rule_rows,
    may_review_rule,
    reported_scope,
    review_rule,
    submit_rule,
)
from octop.infra.features.runs import (
    RUN_RUNNING,
    RUN_SUCCEEDED,
    FeatureRunEngine,
    RunEditInvalid,
    RunNotAtGate,
    RunState,
    RunTaken,
    StepTurn,
    refuse_unsupported,
)
from octop.infra.features.schema import ALLOWED_ICONS, ALLOWED_OUTPUT_KINDS
from octop.infra.features.steps import (
    STEP_VOIDED,
    Artifact,
    FeatureStep,
    StepUnknown,
    StepUnsupported,
    output_kind_of,
    render_value,
)
from octop.infra.gateway.process import build_harness_request
from octop.infra.knowledge.default_open import stamp_turn_knowledge_config
from octop.infra.users.identity import Role
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


class FeatureDefinitionBody(BaseModel):
    """One feature definition as authored in the settings UI (create and update).

    Field types stay loose on purpose: ``validate_manifest`` is the single judge
    of whether a definition is usable, so this model only shapes the request for
    the docs — everything it can carry reaches the store, which refuses a bad
    definition with ``FEATURE_INVALID`` and every reason it found, instead of a
    pydantic 422 that would swallow the specifics.

    ``prompt.system_prompt`` is the ``PROMPT.md`` text; the file name is not part
    of the API, because the store always writes ``PROMPT.md`` and normalises the
    manifest to match.
    """

    model_config = ConfigDict(extra="allow")

    id: str = ""
    label: dict[str, Any] = Field(default_factory=dict)
    description: dict[str, Any] = Field(default_factory=dict)
    icon_name: str = ""
    color: str | None = None
    unit: str = ""
    input_schema: dict[str, Any] = Field(default_factory=dict)
    ui_schema: dict[str, Any] | None = None
    prompt: dict[str, Any] = Field(default_factory=dict)
    output: dict[str, Any] = Field(default_factory=dict)
    permissions: dict[str, Any] | None = None
    agent: dict[str, Any] | None = None
    steps: list[dict[str, Any]] | None = None
    version: int | None = None


class _FeatureRunFailed(RuntimeError):
    """A run that finished without usable output (HITL or empty text)."""


def _require_store(server: Any) -> FeatureStore:
    """The server's feature store — absent only while the server is not started."""
    store: FeatureStore | None = server.feature_store
    if store is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "feature store not available")
    return store


@contextmanager
def _store_errors() -> Iterator[None]:
    """Map feature-store refusals onto the stable API error codes."""
    try:
        yield
    except FeatureDefinitionInvalid as exc:
        raise OctopError(
            ErrorCode.FEATURE_INVALID,
            f"invalid feature definition: {exc}",
            details={"errors": "; ".join(exc.errors)},
        ) from exc
    except FeatureAlreadyExists as exc:
        raise OctopError(ErrorCode.FEATURE_ALREADY_EXISTS, str(exc)) from exc
    except FeatureNotFound as exc:
        raise OctopError(ErrorCode.NOT_FOUND, str(exc)) from exc
    except FeatureReadOnly as exc:
        raise OctopError(ErrorCode.FORBIDDEN, str(exc)) from exc
    except OSError as exc:
        raise OctopError(
            ErrorCode.INTERNAL_ERROR,
            f"could not write the feature definition: {exc}",
        ) from exc


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
            # ``None`` (not ``{}``) for a feature that declares no capability
            # layer: the editor must be able to tell "nothing declared" from
            # "declared, and everything in it is empty".
            "agent": feature.agent.as_dict() if feature.agent is not None else None,
            # Verbatim, as authored: the editor reads a step back to edit it, and
            # an empty list is the same fact as "this feature has no steps".
            "steps": [step.as_dict() for step in feature.steps],
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


def _unit_keys(server: Any) -> list[str]:
    """Unit keys the editor offers: organisation units plus units already in use."""
    keys = {feature.unit for feature in _catalog_features(server)}
    if server.services is not None:
        keys.update(row.key for row in server.services.repos.org_unit_repo.list_all())
    return sorted(keys)


def _model_choices(server: Any) -> list[dict[str, str]]:
    """Chat-eligible model refs this instance can actually run."""
    store = server.app_runtime.agent_registry.providers
    refs: set[str] = set()
    for row in store.iter_usable_rows():
        refs |= enabled_model_refs(row.name, row.get_models(), provider_api_key=row.api_key)
    return [{"ref": ref, "label": ref.split("/", 1)[-1]} for ref in sorted(refs)]


def _tool_choices() -> list[dict[str, str]]:
    """Built-in tools a feature may switch off — never the always-on ones.

    The always-on names are left out rather than offered and refused: the format
    rejects them outright, so the editor must not be able to pick one.
    """
    return [
        {"name": entry.name, "category": entry.category}
        for entry in BUILTIN_TOOL_CATALOG
        if entry.name not in CRITICAL_TOOLS
    ]


def _connector_service(server: Any) -> Any:
    from octop.infra.connectors.service import ConnectorService  # noqa: PLC0415

    return ConnectorService(
        repo=server.services.repos.connector_repo,
        secret_repo=server.services.secret_repo,
        settings_repo=server.services.settings_repo,
        config=server.services.config,
    )


def _connector_choices(server: Any, user: Any) -> list[dict[str, str]]:
    """Connector names the caller may mount, labelled with their display names."""
    svc = _connector_service(server)
    labels: dict[str, str] = {}
    for instance in svc.list_instances_for_api(int(user.id)):
        name = str(instance.get("mcp_server_name") or "").strip()
        if name and name not in labels:
            labels[name] = str(instance.get("display_name") or name).strip() or name
    return [
        {"name": name, "label": labels.get(name, name)}
        for name in svc.list_active_mcp_server_names(int(user.id))
    ]


def _knowledge_base_choices(server: Any, user: Any) -> list[dict[str, str]]:
    """Knowledge bases the caller may read (the same scope the runs resolve against)."""
    bases = server.services.repos.knowledge_repo.list_visible(int(user.id))
    return [{"id": str(base.id), "name": str(base.name)} for base in bases]


async def _capability_agent_id(server: Any, user: Any) -> str:
    """The caller's own agent, started — the agent its capability lists come from.

    Skills and subagents are read off a *live* harness handle
    (``list_skill_summaries`` starts with ``get_agent``), and a run boots the
    caller's agent the same way, so booting it here is what makes the editor
    describe exactly the agent a run would use. Nothing is swallowed: an agent
    that cannot start is reported, because an empty skills list would read as
    "this agent has no skills" and quietly offer the wrong choices.
    """
    agent_id = _run_agent_id(server, int(user.id))
    if not _is_agent_running(server, agent_id):
        await server.app_runtime.agent_registry.start(agent_id)
    return agent_id


async def _agent_scoped_choices(server: Any, agent_id: str, key: str) -> list[str]:
    """Skills / subagents of *agent_id* — what a run of it could ever use."""
    registry = server.app_runtime.agent_registry
    if key == "skills":
        summaries = await registry.list_skill_summaries(agent_id)
        return sorted(
            {str(summary.get("name") or "") for summary in summaries if summary.get("enabled")}
            - {""}
        )
    summaries = await registry.list_subagent_summaries(agent_id)
    return sorted({str(row.get("name") or "") for row in summaries} - {""})


async def _capability_choices(server: Any, user: Any) -> dict[str, Any]:
    """Everything the capability layer of a definition may name, for *user*.

    Every list is resolved through the caller's own visibility (their connectors,
    their readable knowledge bases, their agent's skills and subagents), so the
    editor offers exactly what a run started by this caller could really use — the
    same rule the run path applies (design 5.2).

    This is the one editor call that needs an agent, which is why it is not part
    of ``_meta``: opening the editor must not depend on one agent starting.
    """
    agent_id = await _capability_agent_id(server, user)
    return {
        "models": _model_choices(server),
        "tools": _tool_choices(),
        "skills": await _agent_scoped_choices(server, agent_id, "skills"),
        "subagents": await _agent_scoped_choices(server, agent_id, "subagents"),
        "mcp_servers": _connector_choices(server, user),
        "knowledge_bases": _knowledge_base_choices(server, user),
    }


def _require_feature(server: Any, feature_id: str) -> Feature:
    catalog: FeatureCatalog | None = server.feature_catalog
    feature = None if catalog is None else catalog.get(feature_id)
    if feature is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature {feature_id!r} not found")
    return feature


def _require_personalizable(server: Any, feature: Feature) -> None:
    """Refuse personalization for a definition this instance must not write.

    A bundled definition is read-only, so nothing about it can be edited — and its
    personalization would be a live agent reachable only through a definition whose
    own editor refuses every write (design 7.1, where the user's ruling is pending
    and "not supported" is the recommendation). Refusing here keeps the answer
    single: fork the definition into one of your own, then personalize that.
    """
    store = _require_store(server)
    if not store.is_writable(feature.id):
        raise OctopError(
            ErrorCode.FORBIDDEN,
            f"feature {feature.id!r} is bundled with the application and cannot be "
            "personalized: author a definition of your own for the same job instead",
            details={"feature_id": feature.id, "reason": "bundled"},
        )


def _run_agent_id(server: Any, user_id: int) -> str:
    """The user's own primary (oldest enabled) agent; runs never create one."""
    rows = server.app_runtime.agent_registry.list_agents(user_id)
    if not rows:
        raise OctopError(ErrorCode.AGENT_NOT_FOUND, f"no runnable agent for user {user_id}")
    return str(rows[0].agent_id)


def _feature_run_agent_id(server: Any, feature: Feature, user_id: int) -> str:
    """The agent one run of *feature* uses: the feature's own once it has one.

    Design 5.1: a personalized definition runs on ``feat-<feature_id>`` — the agent
    its author configured — and the definition's declared capability layer is then
    intersected with *that* agent's skills and subagents ("inherit" means the
    feature's agent decides, which is what 5.1 is for). A definition nobody has
    personalized has no such agent and takes the other branch verbatim: the caller's
    own agent, exactly as every run did before features could have agents at all.

    Who pays stays the caller either way — connectors, knowledge bases, turn-scoped
    tools and the session are all resolved per caller (:func:`_stamp_capability`,
    :func:`_run_agent_turn`) — so this switch changes *configuration*, never data.
    """
    return materialized_feature_agent_id(server, feature) or _run_agent_id(server, user_id)


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, (_FeatureRunFailed, CapabilityUnavailable)):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


async def _stamp_capability(
    server: Any,
    request: dict[str, Any],
    capability: ResolvedCapability,
    *,
    agent_id: str,
    user_id: int,
    is_admin: bool,
    locale: str,
    scope: FeatureRunScope | None = None,
) -> None:
    """Put a resolved capability onto the run's harness request.

    The two lookups that make design 5.2 real happen here: the connectors are
    loaded *with the caller's credentials* (``prepare_chat_mcp`` taking
    ``connector_user_id`` — on an agent that is not theirs the tools ride the
    per-turn registry instead of the agent), and the knowledge bases are stamped
    from the caller's visible set, which is also what builds the catalog the
    ``search_knowledge`` tool description is written from.

    *scope* narrows the tool / subagent part for one request (a step's ``tools``
    allow-list); the rest of the capability — model, knobs, skills, connectors,
    knowledge — is the feature's own and stays the same on every step.
    """
    stamp_capability(request, capability, scope)
    if capability.mcp_servers:
        failed = await server.app_runtime.agent_registry.prepare_chat_mcp(
            agent_id,
            list(capability.mcp_servers),
            connector_user_id=user_id,
        )
        if failed:
            raise _FeatureRunFailed(
                f"feature connectors could not be loaded for this caller: {', '.join(failed)}"
            )
        request["mcp_servers"] = list(capability.mcp_servers)
    if capability.knowledge_base_ids is not None:
        # The scope was already intersected with what this caller may read; the
        # list is re-read here because the catalog the tool is described from is
        # built from the same visible set.
        stamp_turn_knowledge_config(
            request,
            visible_bases=server.services.repos.knowledge_repo.list_visible(user_id),
            explicit_ids=list(capability.knowledge_base_ids),
            owner_user_id=user_id,
            is_admin=is_admin,
            locale=locale,
        )


async def _run_agent_turn(
    server: Any,
    *,
    agent_id: str,
    user_id: int,
    text: str,
    system_prompt: str | None = None,
    capability: ResolvedCapability | None = None,
    scope: FeatureRunScope | None = None,
    dispatch_token: str | None = None,
    is_admin: bool = False,
    locale: str = "zh",
) -> str:
    """Run one non-interactive agent turn and return its visible text.

    *system_prompt* is the feature's own ``prompt.system_file`` text. It rides on
    this request only (see :mod:`octop.infra.agents.middleware.feature_prompt`):
    the agent's persisted ``system_prompt`` — the user's own configuration — is
    never touched, and neither is the thread, because the text is not part of the
    messages the checkpointer stores. ``None`` (the default) leaves the request
    exactly as it was before feature system prompts existed.

    *capability* is the feature's declared capability layer already resolved for
    this caller (:func:`~octop.infra.features.capability.resolve_capability`). It
    rides the same request: model, runtime knobs, skills, tools and subagents on
    ``configurable``, the caller's own connector tools through
    ``prepare_chat_mcp``, and the knowledge-base scope through
    ``stamp_turn_knowledge_config`` — every one of them scoped to this run, none of
    them written to the agent.

    *dispatch_token* is the step-turn boundary (design 7.7): the ledger that caps
    how many subagents this turn may run at once and records the ones it did. The
    single-shot run passes none — it has no ceiling to enforce and no step to
    report one for.
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
    model = capability.model if capability is not None else None
    request = build_harness_request(
        thread_id=session.thread_id,
        user_id=session.user_id,
        agent_id=agent_id,
        session_key=session_key,
        source=session.channel_type,
        text=text,
        model=model,
        message_kwargs=None,
    )
    stamp_feature_system_prompt(request, system_prompt)
    if capability is not None:
        await _stamp_capability(
            server,
            request,
            capability,
            agent_id=agent_id,
            user_id=user_id,
            is_admin=is_admin,
            locale=locale,
            scope=scope,
        )
    elif scope is not None:
        # A step's tool allow-list is a scope of its own: even a feature that
        # declares no capability layer has to honour "this step reads the PDF and
        # nothing else", and dropping it here would hand the step every tool.
        stamp_feature_scope(request, scope)
    if dispatch_token is not None:
        # The dispatch boundary for this one step turn: the ceiling and the record
        # (7.7). A plain turn stamps nothing and stays byte-identical.
        stamp_dispatch(request, dispatch_token)
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


# --- stepped runs (design 7.x) ---------------------------------------------


def _run_engine(server: Any) -> FeatureRunEngine:
    """The engine over this server's run tables."""
    assert server.services is not None
    return FeatureRunEngine(
        server.services.repos.feature_runs_repo,
        server.services.repos.feature_tasks_repo,
    )


@contextmanager
def _run_errors() -> Iterator[None]:
    """Map the run engine's refusals onto the stable API error codes."""
    try:
        yield
    except StepUnsupported as exc:
        raise OctopError(
            ErrorCode.FEATURE_STEP_UNSUPPORTED, str(exc), details={"reason": str(exc)}
        ) from exc
    except (RunNotAtGate, RunTaken) as exc:
        raise OctopError(ErrorCode.FEATURE_RUN_NOT_AT_GATE, str(exc)) from exc
    except (RunEditInvalid, StepUnknown) as exc:
        raise OctopError(
            ErrorCode.FEATURE_RUN_REQUEST_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc


def _artifact_dict(artifact: Artifact) -> dict[str, Any]:
    """One artifact as the API reports it: name, declared type, and the value."""
    return artifact.as_dict()


def _step_dict(
    state: RunState,
    step: FeatureStep,
    seq: int,
    *,
    dispatch_default: int | None = None,
) -> dict[str, Any]:
    """One step's row of a run — status, artifact, error, timing, attempts.

    ``decomposition`` is 7.8's 留痕: which subagents this step dispatched, what each
    was told, what each answered, and what the ceiling did about it. ``None`` for a
    step that neither decomposes nor dispatched anything.
    """
    row = state.row_of(step.id)
    artifact = row.artifact()
    return {
        "id": step.id,
        "name": step.name,
        "seq": seq,
        "status": row.status,
        "gate": step.gate,
        "mode": step.mode,
        "on_failure": step.on_failure,
        "attempts": row.attempts,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "error": row.error,
        "voided": row.status == STEP_VOIDED,
        "artifacts": [] if artifact is None else [_artifact_dict(artifact)],
        "decomposition": decomposition_dict(
            step,
            declared_default=dispatch_default,
            rows=state.dispatches_of(step.id),
        ),
    }


def _dispatch_default(state: RunState) -> int | None:
    """The feature-level ceiling this run recorded, or ``None`` when it declared none.

    Read from the run's own snapshot, exactly like every other capability the run
    resolved: a definition edited after the run started must not change what that
    run's ceiling was.
    """
    capability = state.row.snapshot_payload().get("capability")
    value = capability.get("max_parallel") if isinstance(capability, Mapping) else None
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _run_state_dict(state: RunState) -> dict[str, Any]:
    """One run as the API reports it: where it is, and what it produced so far.

    ``output``/``output_kind`` describe the run's deliverable — the last step's
    artifact, once the run got there — and follow that artifact's own declared
    type rather than the definition's ``output.kind``, because on a stepped run the
    deliverable *is* the final artifact.
    """
    pending = state.row.pending_gate_payload()
    output: str | None = None
    output_kind: str | None = None
    if state.row.status == RUN_SUCCEEDED:
        final = state.final_artifact()
        if final is not None:
            step, artifact = final
            output = render_value(step.output, artifact.value)
            output_kind = output_kind_of(step.output)
    pending_gate: dict[str, Any] | None = None
    if pending is not None:
        gate_step = state.step(str(pending["step_id"]))
        gated = state.row_of(gate_step.id).artifact()
        pending_gate = {
            "step_id": gate_step.id,
            "name": gate_step.name,
            "gate": pending["gate"],
            "allow_edit": bool(pending.get("allow_edit")),
            "artifacts": [] if gated is None else [_artifact_dict(gated)],
        }
    dispatch_default = _dispatch_default(state)
    return {
        "task_id": state.task_id,
        "feature_id": state.row.feature_id,
        "status": state.row.status,
        "current_step": state.row.current_step,
        "steps": [
            _step_dict(state, step, seq, dispatch_default=dispatch_default)
            for seq, step in enumerate(state.plan)
        ],
        "pending_gate": pending_gate,
        "output": output,
        "output_kind": output_kind,
    }


def _audit_dict(state: RunState) -> dict[str, Any]:
    """What each step consumed and produced, and every human write on it.

    An input the run no longer holds reads as ``value: null`` rather than being
    dropped: "this step's input is not in the run any more" is what a rewind looks
    like afterwards, and an audit that hid it would describe a run that never
    happened.
    """
    artifacts = state.artifacts()
    dispatch_default = _dispatch_default(state)
    steps: list[dict[str, Any]] = []
    for seq, step in enumerate(state.plan):
        entries = [
            {
                "artifact": edit.artifact,
                "kind": edit.kind,
                "before": edit.before(),
                "after": edit.after(),
                "by_user_id": edit.by_user_id,
                "source": edit.source,
                "at": edit.created_at,
            }
            for edit in state.edits
            if edit.step_id == step.id
        ]
        steps.append(
            {
                **_step_dict(state, step, seq, dispatch_default=dispatch_default),
                "inputs": [
                    artifacts[name].as_dict()
                    if name in artifacts
                    else {"name": name, "schema": None, "value": None}
                    for name in step.inputs
                ],
                "human_edits": entries,
            }
        )
    return {
        "task_id": state.task_id,
        "feature_id": state.row.feature_id,
        "status": state.row.status,
        "snapshot": state.row.snapshot_payload(),
        "steps": steps,
    }


def _require_run(server: Any, feature: Feature, task_id: str, user: Any) -> RunState:
    """The run of *feature* this caller may act on, or the refusal that says why.

    A run belongs to whoever started it — the same rule the task log uses for
    finalizing — and it is addressed under its feature: a task id from another
    feature is not found here rather than acted on.
    """
    state = _run_engine(server).state(task_id)
    if state is None or state.row.feature_id != feature.id:
        raise OctopError(ErrorCode.FEATURE_RUN_NOT_FOUND, f"feature run {task_id!r} does not exist")
    if state.row.user_id != int(user.id) and not user.is_admin:
        raise OctopError(ErrorCode.FORBIDDEN, f"feature run {task_id!r} belongs to another user")
    return state


def _step_scope(step: FeatureStep, capability: ResolvedCapability | None) -> FeatureRunScope | None:
    """The tool surface of one step's turn: the run's scope, narrowed by the step.

    ``tools`` absent inherits whatever the feature's capability layer set, and
    ``[]`` allows no tool at all — the same None-versus-empty convention the
    capability layer uses for skills and subagents.
    """
    if step.tools is None:
        return None if capability is None else capability.run_scope()
    base = capability.run_scope() if capability is not None else FeatureRunScope()
    return FeatureRunScope(
        tools_disabled=base.tools_disabled,
        subagents=base.subagents,
        tools_allowed=step.tools,
    )


def _record_dispatches(
    server: Any,
    task_id: str,
    step: FeatureStep,
    entries: Sequence[DispatchEntry],
) -> None:
    """Write what one step turn dispatched — 7.8's 留痕, straight after the turn.

    Written whether the turn succeeded or failed: a step that fell over mid-way is
    exactly when "which subagents ran, and what did each of them say" has to be
    answerable. The step's row is where the position comes from — a resumed run's
    plan lives in its own row, not in today's definition.
    """
    if not entries:
        return
    assert server.services is not None
    repo = server.services.repos.feature_runs_repo
    row = next((item for item in repo.steps(task_id) if item.step_id == step.id), None)
    if row is None:
        raise StepUnknown(f"step {step.id!r} is not part of run {task_id!r}")
    repo.record_dispatches(task_id, row.seq, step_id=step.id, entries=entries)


async def _run_step_turn(
    server: Any,
    feature: Feature,
    *,
    task_id: str,
    step: FeatureStep,
    artifacts: Mapping[str, Artifact],
    agent_id: str,
    user: Any,
    inputs: dict[str, Any],
    capability: ResolvedCapability | None,
) -> str:
    """Run one step's agent turn inside its dispatch boundary (design 7.7).

    The ceiling is opened before the turn and the record closed after it, so the
    three facts 7.7 keeps stay facts: the model still decides whether and how to
    decompose the step (nothing here schedules anything), the platform caps how many
    subagents run at once, and every one of them is written down.

    A step that declares ``agent_role`` must have run as that subagent; a turn that
    dispatched something else fails the step loudly here, because an answer produced
    by the wrong agent is not the answer the definition asked for.
    """
    locale = str(getattr(user, "locale", None) or "zh")
    token, ledger = open_ledger(
        ceiling=resolve_max_parallel(step, capability.max_parallel if capability else None)
    )
    try:
        answer = await _run_agent_turn(
            server,
            agent_id=agent_id,
            user_id=int(user.id),
            text=render_step_prompt(
                step,
                feature,
                inputs,
                artifacts,
                locale=locale,
                max_parallel_default=capability.max_parallel if capability else None,
            ),
            system_prompt=feature.system_prompt,
            capability=capability,
            scope=_step_scope(step, capability),
            dispatch_token=token,
            is_admin=bool(getattr(user, "is_admin", False)),
            locale=locale,
        )
    finally:
        close_ledger(token)
        _record_dispatches(server, task_id, step, ledger.entries())
    require_role_dispatch(step, ledger.entries())
    return answer


def _step_turn(
    server: Any,
    feature: Feature,
    *,
    task_id: str,
    agent_id: str,
    user: Any,
    inputs: dict[str, Any],
    capability: ResolvedCapability | None,
) -> StepTurn:
    """One step's turn: render its prompt, run it, hand the answer back.

    This reuses the single-shot run's own turn (``_run_agent_turn``, which the
    harness ``stream`` already backs) instead of a second executor: a step *is* an
    agent turn, with a narrower tool surface and a prompt built from the artifacts
    it declared. What a step does *inside* that turn — decompose itself into
    subagents, or do the work in one pass — is the model's call; the platform only
    bounds and records it (:func:`_run_step_turn`).
    """

    async def turn(step: FeatureStep, artifacts: Mapping[str, Artifact]) -> str:
        return await _run_step_turn(
            server,
            feature,
            task_id=task_id,
            step=step,
            artifacts=artifacts,
            agent_id=agent_id,
            user=user,
            inputs=inputs,
            capability=capability,
        )

    return turn


def _run_snapshot(
    feature: Feature,
    *,
    agent_id: str,
    capability: ResolvedCapability | None,
    rules: Sequence[FeatureRuleRow],
    inputs: dict[str, Any],
    locale: str,
) -> dict[str, Any]:
    """What produced this run — design §4's snapshot, for a stepped run.

    The definition version, the agent and model, the capability the caller's run
    actually got, the rules that rode its prompts, the form values its steps read,
    and the plan as frozen. Enough to answer "which configuration produced this"
    without re-reading a definition that may since have changed — and it is what a
    resumed run is rebuilt from.
    """
    return {
        "feature_id": feature.id,
        "feature_version": feature.version,
        "agent_id": agent_id,
        "model": capability.model if capability is not None else None,
        "capability": capability.audit() if capability is not None else None,
        "rules": [{"id": rule.id, "scope": rule.scope} for rule in rules],
        "inputs": inputs,
        "locale": locale,
        "steps": [step.snapshot() for step in feature.steps],
    }


def _resume_turn(server: Any, feature: Feature, state: RunState, user: Any) -> StepTurn:
    """The turn a resumed run uses — the configuration the run started with.

    The agent, the form inputs and the capability layer come from the run's own
    log (the task row and the snapshot), not from today's definition: a run that
    stopped at a gate continues under the configuration it was approved under, and
    an edit to the definition lands on the next run. The feature's ``PROMPT.md`` is
    the exception — it is the feature's standing instruction set (design 7.4's 60KB
    rules document is one), so a correction to it reaches a run still in flight
    rather than being ignored until the next one.
    """
    assert server.services is not None
    task = server.services.repos.feature_tasks_repo.get(state.task_id)
    if task is None or not task.agent_id:
        raise OctopError(
            ErrorCode.FEATURE_RUN_NOT_FOUND,
            f"feature run {state.task_id!r} has no agent recorded",
        )
    payload = state.row.snapshot_payload()
    declared = payload.get("capability")
    capability = capability_from_audit(declared) if isinstance(declared, Mapping) else None
    inputs = json.loads(task.inputs)
    return _step_turn(
        server,
        feature,
        task_id=state.task_id,
        agent_id=str(task.agent_id),
        user=user,
        inputs=inputs if isinstance(inputs, dict) else {},
        capability=capability,
    )


async def _run_steps(
    server: Any,
    feature: Feature,
    inputs: dict[str, Any],
    user: Any,
) -> dict[str, Any]:
    """Execute a stepped definition through the linear engine.

    The run is logged *before* it runs and updated as it goes: a run that stopped
    at a human gate is a run still in progress, so its ``feature_tasks`` row says
    ``running`` until the run actually ends. A plan this build cannot run — or a
    plan whose decomposing steps this *run* could not dispatch — is refused before
    anything is written: an unimplemented or unsatisfiable mode must leave no
    half-run that later reads as if it had executed.
    """
    assert server.services is not None
    agent_id = _feature_run_agent_id(server, feature, user.id)
    repo = server.services.repos.feature_tasks_repo
    rules = injectable_rule_rows(
        server.services.repos.feature_rules_repo,
        feature.id,
        user_id=int(user.id),
        unit_key=getattr(user, "org_unit", None),
    )
    locale = str(getattr(user, "locale", None) or "zh")
    with _run_errors():
        refuse_unsupported(feature.id, feature.steps)
        capability = await resolve_capability(server, feature.agent, agent_id=agent_id, user=user)
        refuse_undispatchable(feature.id, feature.steps, capability)
        logger.info(
            "feature %s stepped run scope user=%s agent=%s %s",
            feature.id,
            user.id,
            agent_id,
            json.dumps(
                capability.audit() if capability is not None else {"declared": False},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        row = repo.create(
            feature_id=feature.id,
            user_id=user.id,
            inputs=json.dumps(inputs, ensure_ascii=False),
            status=RUN_RUNNING,
            agent_id=agent_id,
            injected_rule_ids=json.dumps([rule.id for rule in rules], ensure_ascii=False),
        )
        state = await _run_engine(server).start(
            task_id=row.id,
            feature_id=feature.id,
            user_id=int(user.id),
            plan=feature.steps,
            snapshot=_run_snapshot(
                feature,
                agent_id=agent_id,
                capability=capability,
                rules=rules,
                inputs=inputs,
                locale=locale,
            ),
            run_turn=_step_turn(
                server,
                feature,
                task_id=row.id,
                agent_id=agent_id,
                user=user,
                inputs=inputs,
                capability=capability,
            ),
        )
    return _run_state_dict(state)


class FeatureRunApproveBody(BaseModel):
    """A human's answer at a gate; ``edits`` corrects the gated step's artifact."""

    edits: dict[str, Any] | None = None


class FeatureRunRewindBody(BaseModel):
    """Where to go back to — and, for 带修正重跑, what the human corrected.

    ``to_step`` is a step id; step ids are lowercase identifiers, so an integer is
    never an id and is read as the step's position in the plan instead.
    """

    to_step: str | int
    edits: dict[str, Any] | None = None


@router.get("/{feature_id}/runs/{task_id}", summary="Get one run's step states")
async def get_feature_run(
    feature_id: str,
    task_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Where a stepped run is, what each step produced, and the gate it waits on."""
    feature = _require_feature(server, feature_id)
    return _run_state_dict(_require_run(server, feature, task_id, user))


@router.post("/{feature_id}/runs/{task_id}/approve", summary="Approve a run's gate")
async def approve_feature_run(
    feature_id: str,
    task_id: str,
    body: FeatureRunApproveBody | None = None,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Answer the gate a run waits on and continue **the same run**.

    Approving does not start a second run: the steps before the gate keep the
    artifacts they produced, and only the steps after it are asked of the model
    again. ``edits`` corrects the gated step's own artifact (and only that one, and
    only when the step declares ``allow_edit``); a check gate that did not pass
    cannot be approved past at all — the remedy is a rewind with the corrected
    artifact.
    """
    feature = _require_feature(server, feature_id)
    state = _require_run(server, feature, task_id, user)
    with _run_errors():
        resumed = await _run_engine(server).approve(
            state,
            edits=body.edits if body is not None else None,
            by_user_id=int(user.id),
            run_turn=_resume_turn(server, feature, state, user),
        )
    return _run_state_dict(resumed)


@router.post("/{feature_id}/runs/{task_id}/rewind", summary="Rewind a run to a step")
async def rewind_feature_run(
    feature_id: str,
    task_id: str,
    body: FeatureRunRewindBody,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Go back to a step, void what came after it, and walk on from there.

    Without ``edits`` this is 回退重跑: every step from the target on loses its
    artifact and runs again. With them it is 带修正重跑: the corrected values are
    injected as the artifacts the target step consumes, and both the correction and
    the void it replaced stay in the audit — the discarded value is kept in the
    void record, so "who changed what" is answerable after the rerun.
    """
    feature = _require_feature(server, feature_id)
    state = _require_run(server, feature, task_id, user)
    with _run_errors():
        resumed = await _run_engine(server).rewind(
            state,
            to_step=body.to_step,
            edits=body.edits,
            by_user_id=int(user.id),
            run_turn=_resume_turn(server, feature, state, user),
        )
    return _run_state_dict(resumed)


@router.get("/{feature_id}/runs/{task_id}/audit", summary="Audit one run")
async def audit_feature_run(
    feature_id: str,
    task_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Every step's inputs and artifacts, and every human write, before and after."""
    feature = _require_feature(server, feature_id)
    return _audit_dict(_require_run(server, feature, task_id, user))


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


@router.get("/_meta", summary="Choices the feature editor offers")
async def feature_meta(
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Metadata for authoring a definition: units, icons, output kinds, ownership.

    ``bundled_ids`` is what tells the editor which definitions it must not offer
    to edit — it comes from the store's own writability test, so a greyed-out
    button and a refused write can never disagree.

    Nothing here touches an agent: this is the call the editor makes on open, and
    a definition's metadata has to load whether or not any agent can start. The
    choices that *are* read off the caller's agent live in ``_capabilities``.
    """
    store = _require_store(server)
    return {
        "units": _unit_keys(server),
        "icons": list(ALLOWED_ICONS),
        "output_kinds": list(ALLOWED_OUTPUT_KINDS),
        "bundled_ids": store.read_only_ids(),
        # The ceiling a step that declares none inherits (design 7.7). Reported so
        # the editor can show what "inherit" actually resolves to instead of
        # leaving the author to guess a number this build already fixed.
        "max_parallel_default": DEFAULT_MAX_PARALLEL,
        # The cap on one recorded subagent answer — the editor says so where it
        # shows a dispatch's result, so a cut answer is never mistaken for a short one.
        "dispatch_result_max_chars": MAX_DISPATCH_RESULT_CHARS,
    }


@router.get("/_capabilities", summary="Choices that need the caller's agent")
async def feature_capabilities(
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """What the capability layer of a definition may name — skills and subagents
    included, which only the caller's own agent can answer for.

    Split from ``_meta`` on purpose. Listing skills means starting the caller's
    agent (the registry reads them off the live harness handle), and that must not
    be a precondition for opening the editor: the settings drawer loads ``_meta``
    on open and calls this only when the capability block is expanded. An agent
    that cannot start is reported here rather than answered with empty lists —
    "could not load" and "you have none" are different facts, and the editor shows
    the first as an error.
    """
    return await _capability_choices(server, user)


@router.get("/{feature_id}", summary="Get one feature definition")
async def get_feature(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Return the full definition backing the schema-driven run form."""
    return _feature_dict(_require_feature(server, feature_id))


@router.post("", status_code=201, summary="Create a feature")
async def create_feature(
    body: FeatureDefinitionBody,
    _user: Any = Depends(require_permission("features")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Save a new feature definition into ``~/.octop/features/<id>/``.

    Admin-only: a feature is instance-wide configuration (which model, tools and
    prompt everybody gets), unlike the runs and rules any member may contribute.
    """
    store = _require_store(server)
    with _store_errors():
        feature_id = store.create(body.model_dump())
    return {"feature_id": feature_id}


@router.put("/{feature_id}", summary="Update a feature")
async def update_feature(
    feature_id: str,
    body: FeatureDefinitionBody,
    _user: Any = Depends(require_permission("features")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Overwrite one user feature definition.

    A bundled definition is refused outright rather than copied into the user
    directory: a silent copy would fork the shipped definition, and later
    versions of the application would never reach that installation again.
    """
    store = _require_store(server)
    with _store_errors():
        updated = store.update(feature_id, body.model_dump())
    return {"feature_id": updated}


@router.delete("/{feature_id}", status_code=204, summary="Delete a feature")
async def delete_feature(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> None:
    """Remove one user feature definition; a bundled one cannot be deleted."""
    store = _require_store(server)
    with _store_errors():
        store.delete(feature_id)


@router.post("/{feature_id}/agent", summary="Give one feature its own agent")
async def personalize_feature(
    feature_id: str,
    _user: Any = Depends(require_permission("features")),
    _admin: Any = Depends(require_admin()),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Personalization's entry point: this feature's own agent, created on the first call.

    Design 5.1 — every feature may have an agent of its own for the panels that only
    work against a live agent. This is the *only* way one is born: not at definition
    creation, not on a run (``_run_agent_id`` still never creates anything). It is
    idempotent because it is the editor's "open the personalization surface" call, so
    the second call answers ``created: false`` for the same agent instead of a
    conflict. Creating the row and leaving the harness with a live handle are one
    answer: a panel reads skills, subagents and workspace files off that handle.

    Admin-gated exactly like the definition write endpoints — the feature's agent is
    instance configuration, not the caller's property: it is app-owned
    (``user_id IS NULL``), so ``assert_agent_owner`` lets administrators edit it and
    refuses everyone else through the agent endpoints, while runs stay governed by
    ``require_permission("features")`` alone. A bundled definition is refused, the
    same line as editing it.
    """
    feature = _require_feature(server, feature_id)
    _require_personalizable(server, feature)
    row, created = await ensure_feature_agent(server, feature)
    return {"feature_id": feature.id, "agent_id": row.agent_id, "created": created}


@router.post("/{feature_id}/run", summary="Run a feature once")
async def run_feature(
    feature_id: str,
    body: FeatureRunBody,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Run the feature with its own agent — or the caller's, when it has none.

    Which agent runs it is the *only* thing personalization changes here: a
    definition whose author configured one runs on ``feat-<feature_id>``, and every
    other definition keeps running on the caller's own agent exactly as before
    (:func:`_feature_run_agent_id`).

    Both outcomes land in ``feature_tasks`` — a failed run is the training
    signal M4 needs, so the row is written before the error is raised. The row
    carries the run's snapshot too (which agent ran it, which approved rules
    went into the prompt): a failed run needs that diagnosis as much as a
    successful one, and nothing else records it.

    The feature's capability layer is resolved against *this* caller first
    (design 5.2): their connectors, their readable knowledge bases, and the run
    agent's skills and subagents — the feature's own agent once it has one, the
    caller's otherwise. What the caller cannot reach is logged rather than
    silently dropped, and a model the feature declares that this instance cannot
    run fails the run outright — a feature never quietly runs on another model.
    """
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    if feature.steps:
        # A stepped definition runs through the step engine — the same endpoint,
        # because it is the same act from the caller's side. Its own gates decide
        # where it stops, and the row it logs says so.
        return await _run_steps(server, feature, body.inputs, user)
    agent_id = _feature_run_agent_id(server, feature, user.id)
    repo = server.services.repos.feature_tasks_repo
    inputs = json.dumps(body.inputs, ensure_ascii=False)
    rules = injectable_rule_rows(
        server.services.repos.feature_rules_repo,
        feature.id,
        user_id=int(user.id),
        unit_key=getattr(user, "org_unit", None),
    )
    rule_ids = json.dumps([rule.id for rule in rules], ensure_ascii=False)
    try:
        capability = await resolve_capability(
            server,
            feature.agent,
            agent_id=agent_id,
            user=user,
        )
        logger.info(
            "feature %s run scope user=%s agent=%s %s",
            feature.id,
            user.id,
            agent_id,
            json.dumps(
                capability.audit() if capability is not None else {"declared": False},
                ensure_ascii=False,
                sort_keys=True,
            ),
        )
        output = await _run_agent_turn(
            server,
            agent_id=agent_id,
            user_id=user.id,
            text=build_user_prompt(
                feature,
                body.inputs,
                # The prompt says where each rule came from: a personal rule is the
                # caller's own habit, a global one is an org-wide requirement.
                rules=[ScopedRule(text=rule.rule_text, scope=rule.scope) for rule in rules],
            ),
            system_prompt=feature.system_prompt,
            capability=capability,
            is_admin=bool(getattr(user, "is_admin", False)),
            locale=str(getattr(user, "locale", None) or "zh"),
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


def _unit_labels(server: Any) -> dict[str, str]:
    """``{unit_key: label_zh}`` — resolved once per response, not once per rule."""
    assert server.services is not None
    return {
        row.key: (row.label_zh or row.key) for row in server.services.repos.org_unit_repo.list_all()
    }


def _rule_dict(row: FeatureRuleRow, unit_labels: Mapping[str, str]) -> dict[str, Any]:
    """Rule payload for the review UI — provenance, scope, and layer included.

    ``scope`` is always present and never ``null``: an unnameable layer reports
    ``"unknown"`` rather than vanishing, and a layer this build does not know is
    passed through under its own name (:func:`reported_scope`), never folded into
    one of the three. The panel renders every rule it is handed and groups the
    ones it cannot place under its unfiled heading, so a stored layer nobody
    recognises stays visible instead of dropping the rule off the screen.
    """
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
        "scope": reported_scope(row),
        "owner_user_id": row.owner_user_id,
        "unit_key": row.unit_key,
        # The department name, so a unit rule reads as one without the UI having
        # to resolve keys itself; a deleted unit falls back to its key.
        "unit_label": None if row.unit_key is None else unit_labels.get(row.unit_key, row.unit_key),
    }


def _review_one(server: Any, rule_id: str, *, approve: bool, user: Any) -> dict[str, Any]:
    """Shared body of approve/reject — the reviewer is the caller, by scope."""
    assert server.services is not None
    repo = server.services.repos.feature_rules_repo
    existing = repo.get(rule_id)
    if existing is None:
        raise OctopError(ErrorCode.NOT_FOUND, f"rule {rule_id!r} not found")
    if not may_review_rule(
        existing,
        user_id=int(user.id),
        role=user.role,
        unit_key=getattr(user, "org_unit", None),
    ):
        raise OctopError(
            ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN,
            f"rule {rule_id!r} is {existing.scope}-scoped: it is not yours to review",
            details={
                "scope": existing.scope,
                "unit_key": existing.unit_key,
                "owner_user_id": existing.owner_user_id,
            },
        )
    try:
        row = review_rule(repo, rule_id, approve=approve, reviewer_id=int(user.id))
    except RuleNotFound as exc:
        raise OctopError(ErrorCode.NOT_FOUND, str(exc)) from exc
    except RuleAlreadyReviewed as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_REVIEWED, str(exc)) from exc
    return _rule_dict(row, _unit_labels(server))


def _extraction_scope(
    server: Any, user: Any, scope: str
) -> tuple[int | None, str | None, list[int] | None]:
    """Resolve one extraction request into ``(owner, unit, evidence users)``.

    Personal reads the caller's own runs, unit reads the caller's own department
    (which needs the role to reach it and a unit to read), and global reads
    everyone. Refusing here rather than filtering later is the point: a scope the
    caller cannot reach must not run the extractor at all, and the drafts it
    writes are stamped with the layer the caller asked for.
    """
    assert server.services is not None
    unit_key = getattr(user, "org_unit", None)
    if scope == SCOPE_PERSONAL:
        return int(user.id), None, [int(user.id)]
    if scope == SCOPE_UNIT:
        if not user.is_admin and user.role != Role.UNIT_ADMIN:
            raise OctopError(
                ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN,
                "extracting department rules needs the unit_admin role or an admin",
                details={"scope": scope, "unit_key": unit_key},
            )
        if not unit_key:
            raise OctopError(
                ErrorCode.FEATURE_RULE_SUBMIT_INVALID,
                "you are not in an org unit: there is no department to extract rules for",
                details={"scope": scope},
            )
        return None, unit_key, _unit_member_ids(server, unit_key)
    if not user.is_admin:
        raise OctopError(
            ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN,
            "extracting global rules is an admin decision",
            details={"scope": scope},
        )
    return None, None, None


def _unit_member_ids(server: Any, unit_key: str) -> list[int]:
    """User ids of one department — the runs a department extraction may read."""
    assert server.services is not None
    return [
        int(row.id) for row in server.services.repos.user_repo.list() if row.org_unit == unit_key
    ]


@router.post("/{feature_id}/rules/extract", summary="Induce draft rules from finalized tasks")
async def extract_feature_rules(
    feature_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
    scope: Literal["personal", "unit", "global"] = Query(
        "personal",
        description=(
            "personal: learn from your own runs (default); "
            "unit: from your department's runs (unit_admin or admin); "
            "global: from every run (admin only)"
        ),
    ),
) -> dict[str, Any]:
    """Run the extractor once over the corrections of one scope layer.

    Output is always ``draft``: the extractor proposes, a human disposes — and a
    draft of a wider layer waits for that layer's reviewer. A run that cannot be
    parsed writes nothing at all.
    """
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    agent_id = _run_agent_id(server, user.id)
    owner_user_id, unit_key, user_ids = _extraction_scope(server, user, scope)

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
            scope=scope,
            owner_user_id=owner_user_id,
            unit_key=unit_key,
            user_ids=user_ids,
        )
    except OctopError:
        raise
    except RuleNoSamples as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_NO_SAMPLES, str(exc)) from exc
    except RuleScopeInvalid as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_SUBMIT_INVALID, str(exc)) from exc
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
    labels = _unit_labels(server)
    return {"feature_id": feature.id, "rules": [_rule_dict(row, labels) for row in rows]}


@router.get("/{feature_id}/rules", summary="List rules of one feature")
async def list_feature_rules(
    feature_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Rules of this feature the caller may see, newest first.

    That is the global layer, the caller's own department, and the caller's own
    personal rules — under any status, since the review queue needs the drafts.
    Other people's personal rules stay private to them (nobody else may decide
    them anyway), so the list and the review guard answer the same question.
    """
    assert server.services is not None
    feature = _require_feature(server, feature_id)
    rows = server.services.repos.feature_rules_repo.list_visible(
        feature.id,
        user_id=int(user.id),
        unit_key=getattr(user, "org_unit", None),
        is_admin=bool(user.is_admin),
    )
    labels = _unit_labels(server)
    return {"feature_id": feature.id, "rules": [_rule_dict(row, labels) for row in rows]}


class FeatureRuleSubmitBody(BaseModel):
    """Which wider layer a personal rule is proposed to, and why."""

    target_scope: Literal["unit", "global"]
    reason: str | None = None


@router.post("/rules/{rule_id}/submit", summary="Submit a personal rule to a wider scope")
async def submit_feature_rule(
    rule_id: str,
    body: FeatureRuleSubmitBody,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Propose one personal rule to the caller's department, or to everyone.

    The submission is a *new draft* in the target layer — the personal rule is
    left exactly as it was and keeps working for its owner, whatever the wider
    layer decides. The reason is recorded in the audit log; the rule carries the
    provenance of the corrections it came from.
    """
    assert server.services is not None
    try:
        row = submit_rule(
            server.services.repos.feature_rules_repo,
            rule_id,
            target_scope=body.target_scope,
            submitter_id=int(user.id),
            role=user.role,
            unit_key=getattr(user, "org_unit", None),
        )
    except RuleNotFound as exc:
        raise OctopError(ErrorCode.NOT_FOUND, str(exc)) from exc
    except RuleScopeInvalid as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_SUBMIT_INVALID, str(exc)) from exc
    except RuleScopeForbidden as exc:
        raise OctopError(ErrorCode.FEATURE_RULE_SCOPE_FORBIDDEN, str(exc)) from exc
    server.services.audit_repo.write(
        actor=user.username,
        action="feature_rule.submit",
        target=rule_id,
        payload=json.dumps(
            {
                "rule_id": row.id,
                "feature_id": row.feature_id,
                "target_scope": row.scope,
                "unit_key": row.unit_key,
                "reason": body.reason,
            },
            ensure_ascii=False,
        ),
    )
    return {"rule": _rule_dict(row, _unit_labels(server))}


@router.post("/rules/{rule_id}/approve", summary="Approve a draft rule")
async def approve_feature_rule(
    rule_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Let an approved rule into the prompts of the layer it belongs to."""
    return _review_one(server, rule_id, approve=True, user=user)


@router.post("/rules/{rule_id}/reject", summary="Reject a draft rule")
async def reject_feature_rule(
    rule_id: str,
    user: Any = Depends(require_permission("features")),
    server: Any = Depends(get_server),
) -> dict[str, Any]:
    """Retire a rule for good — rejected rules are never injected."""
    return _review_one(server, rule_id, approve=False, user=user)


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
