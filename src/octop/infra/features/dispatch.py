"""The platform's half of a step's decomposition — the ceiling, and the record.

Design 7.7 splits one step in two: **the model decides** whether to decompose it,
into how many pieces, and how to schedule them; **the platform keeps only** a
ceiling on how many subagents may run at once (``max_parallel``) and the record of
what actually ran (7.8's 分解留痕). Nothing here schedules anything either — the
boundary that enforces and records is
:mod:`octop.infra.agents.middleware.feature_dispatch`, applied to the ``task`` tool
of a step's turn.

So this module holds the parts of that split the platform owns and both sides
read:

* :data:`DEFAULT_MAX_PARALLEL` and :func:`resolve_max_parallel` — where one step's
  ceiling comes from (its own declaration, else the feature's, else the platform's);
* :class:`DispatchEntry` — one dispatch as the boundary observed it, and the shape
  the repository stores;
* :func:`decomposition_dict` — the record as the run and the audit report it;
* :func:`refuse_undispatchable` and :func:`require_role_dispatch` — the two
  refusals that keep "this step decomposes" from being a claim nobody checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from octop.infra.features.steps import (
    DISPATCH_TOOL_NAME,
    FeatureStep,
    StepAgentRoleUnmet,
    StepUnsupported,
    dispatch_required,
)

if TYPE_CHECKING:
    from octop.infra.db.repos.feature_runs import FeatureStepDispatchRow
    from octop.infra.features.capability import ResolvedCapability

DEFAULT_MAX_PARALLEL = 4
"""The ceiling a step that declares none inherits (7.7's "可配、可继承默认").

Every dispatch level is explicit, so nothing is guessed: a step's own
``max_parallel`` wins, then the feature definition's ``agent.max_parallel``, then
this number. It is the ceiling the platform enforces on its own — the model is
always free to schedule fewer. Four keeps the failure 7.7 names ("模型可能一次开 50
个子 agent") bounded while still letting the common "split the batch in three and
merge" pattern run in parallel.
"""

MAX_DISPATCH_RESULT_CHARS = 4000
"""How much of one subagent's answer the record keeps.

Longer answers are cut and marked (:class:`DispatchEntry.truncated`) rather than
silently shortened: the record exists so "这个子 agent 产出什么" is answerable, and a
record that quietly dropped half of it would answer a different question.
"""


@dataclass(frozen=True)
class DispatchEntry:
    """One subagent dispatch, as the boundary observed it.

    The fields are the ones the ``task`` tool call itself carries — the subagent
    it named and the task text it was given — plus what the platform measured
    around it: when it ran, how wide it ran, and how long it queued for a slot.
    """

    role: str
    """The ``subagent_type`` the model passed, verbatim."""

    task: str
    """The ``description`` the model passed — what that subagent was asked to do."""

    status: str
    """``succeeded`` or ``failed`` (the call's own outcome, never the step's)."""

    error: str | None
    result: str | None
    truncated: bool
    """The answer was longer than :data:`MAX_DISPATCH_RESULT_CHARS` and was cut."""

    waited: bool
    """No slot was free when this dispatch was made — the ceiling was in force."""

    waited_ms: int
    slots: int
    """How many dispatches were running once this one started (its own included)."""

    started_at: int
    """Unix seconds (``now_ts``) — the same clock as every other run timestamp."""

    ended_at: int | None


def resolve_max_parallel(step: FeatureStep, declared_default: int | None) -> int:
    """The ceiling *step* runs under: its own, else the feature's, else the platform's.

    *declared_default* is the feature definition's ``agent.max_parallel`` as the
    run resolved it (``None`` when the definition declares none). A step that
    declares a ceiling of its own always wins: it is the narrower statement.
    """
    if step.max_parallel is not None:
        return step.max_parallel
    if declared_default is not None:
        return declared_default
    return DEFAULT_MAX_PARALLEL


def decomposition_dict(
    step: FeatureStep,
    *,
    declared_default: int | None,
    rows: Sequence[FeatureStepDispatchRow],
) -> dict[str, Any] | None:
    """One step's decomposition record, or ``None`` when there is none to report.

    ``None`` is the honest answer for a step that neither decomposes nor ran a
    subagent: reporting an empty record would say "this step dispatched nothing"
    about a step that was never meant to dispatch anything. A step that declares
    ``orchestrate``/``agent_role``, or that ran at least one subagent, always gets
    the object — with an empty ``dispatches`` list when the model chose not to
    decompose after all.

    ``peak`` and ``waited`` are measured, not declared: they are the ceiling's own
    evidence (7.7's "模型想开很多时被上限截住" reads as ``waited > 0``).
    """
    if not rows and not dispatch_required(step):
        return None
    return {
        "mode": step.mode,
        "role": step.agent_role,
        "declared": step.max_parallel,
        "ceiling": resolve_max_parallel(step, declared_default),
        "peak": max((row.slots for row in rows), default=0),
        "waited": sum(1 for row in rows if row.waited),
        "dispatches": [
            {
                "ordinal": ordinal,
                "role": row.role,
                "task": row.task,
                "status": row.status,
                "error": row.error,
                "result": row.result,
                "truncated": row.truncated,
                "waited": row.waited,
                "waited_ms": row.waited_ms,
                "slots": row.slots,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
            }
            for ordinal, row in enumerate(rows)
        ],
    }


def refuse_undispatchable(
    feature_id: str,
    plan: Sequence[FeatureStep],
    capability: ResolvedCapability | None,
) -> None:
    """Refuse a run whose decomposing steps could dispatch nothing at all.

    A step declaring ``orchestrate`` (or an ``agent_role``) is a promise that its
    work is handed to subagents. When the run's own capability layer makes that
    impossible — the dispatch tool is switched off, or the subagent allow-list
    resolves to none — running it anyway would hand back a single agent's answer
    for a step the author believes was decomposed. That is exactly the silent
    degradation this build does not do, so the run is refused before it writes
    anything.
    """
    needs = [step for step in plan if dispatch_required(step)]
    if not needs or capability is None:
        return
    reasons: list[str] = []
    if DISPATCH_TOOL_NAME in capability.tools_disabled:
        reasons.append(f"the feature switches the {DISPATCH_TOOL_NAME!r} tool off")
    if capability.subagents is not None and not capability.subagents:
        withheld = "; ".join(capability.withheld)
        reasons.append(f"the run may dispatch no subagent{': ' + withheld if withheld else ''}")
    if not reasons:
        return
    names = ", ".join(step.id for step in needs)
    raise StepUnsupported(
        f"feature {feature_id!r} step(s) {names} must dispatch subagents, but "
        + " and ".join(reasons)
    )


def require_role_dispatch(step: FeatureStep, entries: Sequence[DispatchEntry]) -> None:
    """A step that names the subagent it runs as must actually have run as it.

    Checked against the dispatches *this turn* made. The step's declared
    ``agent_role`` is part of its skeleton, so a turn that never dispatched it did
    not do what the definition said — which is a failure to report, not an answer
    to accept. Whether that dispatch succeeded is the model's business: the check
    is that the role ran, and a failed subagent is a result its caller can react to.
    """
    if step.agent_role is None:
        return
    if any(entry.role == step.agent_role for entry in entries):
        return
    ran = ", ".join(sorted({entry.role for entry in entries})) or "no subagent"
    raise StepAgentRoleUnmet(
        f"step {step.id!r} declares agent_role {step.agent_role!r} and must run as that "
        f"subagent, but its turn dispatched {ran}"
    )


__all__ = [
    "DEFAULT_MAX_PARALLEL",
    "MAX_DISPATCH_RESULT_CHARS",
    "DispatchEntry",
    "decomposition_dict",
    "refuse_undispatchable",
    "require_role_dispatch",
    "resolve_max_parallel",
]
