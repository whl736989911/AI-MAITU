"""Driving a feature run's linear steps — gates, artifacts, and the rerun paths.

The engine is deliberately small and has no opinion about HTTP: it walks the plan
the definition froze at start, hands each step's prompt to a *runner* (the router
supplies the one that runs a real agent turn through the existing
:func:`octop.infra.api.routers.features._run_agent_turn` machinery), and writes
every transition to :class:`~octop.infra.db.repos.feature_runs.FeatureRunRepo`.

That the state lives in the database rather than in this object is the whole point
of design 7.4's third decision ("批准后继续同一个运行"): when a gate stops the run,
this object goes away with the request, and approving it later rebuilds the state —
which step it stopped at, and which artifacts the earlier steps produced — and
walks on. Nothing is recomputed, and no step is run twice to get back to where the
run already was.

Four rules the engine never bends:

* a step produces exactly its declared artifact, or it fails (7.2's 类型化产物);
* a ``confirm`` gate stops the run; a ``validate`` gate that read ``passed: false``
  cannot be approved past — the remedy is a corrected rerun (校验门不过不许交付);
* ``on_failure: escalate`` stops for a human instead of deciding (中止上报);
* every human write — an edit at a gate, an edit on a rewind, and the void a
  rewind performs — is recorded with its value before and after, before it is
  applied (7.9's audit requirement).
"""

from __future__ import annotations

import json
import logging
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.db.repos.feature_runs import (
    FeatureRunRepo,
    FeatureRunRow,
    FeatureStepEditRow,
    FeatureStepRunRow,
)
from octop.infra.db.repos.feature_tasks import FeatureTaskRepo
from octop.infra.features.steps import (
    GATE_CONFIRM,
    GATE_VALIDATE,
    MAX_STEP_ATTEMPTS,
    ON_FAILURE_ESCALATE,
    ON_FAILURE_RETRY,
    STEP_ESCALATED,
    STEP_FAILED,
    STEP_SUCCEEDED,
    Artifact,
    FeatureStep,
    StepError,
    StepGateFailed,
    StepUnknown,
    StepUnsupported,
    parse_artifact,
    parse_steps,
    render_value,
    resolve_step,
    unsupported_reasons,
    validate_artifact,
    verdict_of,
)

logger = logging.getLogger(__name__)


def refuse_unsupported(feature_id: str, plan: Sequence[FeatureStep]) -> None:
    """Raise when *plan* declares something this build does not run.

    Called before a run writes anything — the router uses it to keep an
    unimplemented mode from leaving a half-run behind, and the engine calls it
    again as its own guard on the resume paths.
    """
    reasons = unsupported_reasons(plan)
    if reasons:
        raise StepUnsupported(
            f"feature {feature_id!r} declares steps this build cannot run: " + "; ".join(reasons)
        )


RUN_RUNNING = "running"
RUN_AWAITING_GATE = "awaiting_gate"
RUN_SUCCEEDED = "succeeded"
RUN_FAILED = "failed"
RUN_ESCALATED = "escalated"
"""``feature_runs.status`` — the states the API reports a run in."""

GATE_ESCALATED = "escalate"
"""The ``pending_gate.gate`` value of a run waiting for a human after a failure.

Not a declared gate: it is the ``on_failure: escalate`` path stopping for a human,
and the reason to keep it distinct is that the run is *not* waiting for the
approval a ``confirm`` gate asks for — it is waiting for a decision.
"""

EDIT_KIND_EDIT = "edit"
EDIT_KIND_VOID = "void"
EDIT_SOURCE_APPROVE = "approve"
EDIT_SOURCE_REWIND = "rewind"

_GATE_RUN_STATUS: dict[str, str] = {
    GATE_CONFIRM: RUN_AWAITING_GATE,
    GATE_VALIDATE: RUN_AWAITING_GATE,
    GATE_ESCALATED: RUN_ESCALATED,
}


class RunError(RuntimeError):
    """A request against a run that cannot be honoured."""


class RunNotAtGate(RunError):
    """The run is not waiting for the action asked of it."""


class RunEditInvalid(RunError):
    """A human edit that would put a value where the definition says it cannot go."""


class RunTaken(RunError):
    """Another action moved this run on first — reload it and decide again."""


@dataclass(frozen=True)
class _PlannedEdit:
    """One human correction that passed every check, ready to be written."""

    seq: int
    artifact: Artifact
    before: Any


StepTurn = Callable[[FeatureStep, Mapping[str, Artifact]], Awaitable[str]]
"""Runs one step's turn: the step, the artifacts it consumes, the answer.

The prompt is rendered by the caller's turn — it holds the feature (the step's
prompt text and the run form's values) and is the one that actually sends the
turn. The engine's half of the contract is the artifacts: the turn is given the
typed values, not a description of them.
"""


@dataclass(frozen=True)
class RunState:
    """One run as the engine works with it: the row, its frozen plan, its steps."""

    row: FeatureRunRow
    plan: tuple[FeatureStep, ...]
    steps: tuple[FeatureStepRunRow, ...]
    edits: tuple[FeatureStepEditRow, ...]

    @property
    def task_id(self) -> str:
        return self.row.task_id

    def row_of(self, step_id: str) -> FeatureStepRunRow:
        for row in self.steps:
            if row.step_id == step_id:
                return row
        raise StepUnknown(f"step {step_id!r} is not part of run {self.task_id!r}")

    def seq_of(self, step_id: str) -> int:
        for seq, step in enumerate(self.plan):
            if step.id == step_id:
                return seq
        raise StepUnknown(f"step {step_id!r} is not part of run {self.task_id!r}")

    def step(self, step_id: str) -> FeatureStep:
        for step in self.plan:
            if step.id == step_id:
                return step
        raise StepUnknown(f"step {step_id!r} is not part of run {self.task_id!r}")

    def artifacts(self) -> dict[str, Artifact]:
        """Every artifact the run currently holds, by name."""
        out: dict[str, Artifact] = {}
        for row in self.steps:
            artifact = row.artifact()
            if artifact is not None:
                out[artifact.name] = artifact
        return out

    def final_artifact(self) -> tuple[FeatureStep, Artifact] | None:
        """The last step's artifact, when the run produced one — this run's output."""
        if not self.plan:
            return None
        row = self.row_of(self.plan[-1].id)
        artifact = row.artifact()
        return None if artifact is None else (self.plan[-1], artifact)


def plan_from_row(row: FeatureRunRow) -> tuple[FeatureStep, ...]:
    """The plan frozen when the run started, re-read from the run's own row.

    The catalog is never consulted here: a definition edited after a run started
    must not change what that run was, and the audit has to describe the pipeline
    that actually ran.
    """
    steps, errors = parse_steps(json.loads(row.plan))
    if errors:
        raise StepError(
            f"run {row.task_id!r} carries a step plan this build cannot read: {'; '.join(errors)}"
        )
    return tuple(steps)


class FeatureRunEngine:
    """Walks one stepped feature run and persists every transition it makes."""

    def __init__(self, runs: FeatureRunRepo, tasks: FeatureTaskRepo) -> None:
        self._runs = runs
        self._tasks = tasks

    # --- reading ---------------------------------------------------------

    def state(self, task_id: str) -> RunState | None:
        """The run's current state, or ``None`` when there is no such run."""
        row = self._runs.get(task_id)
        if row is None:
            return None
        return RunState(
            row=row,
            plan=plan_from_row(row),
            steps=tuple(self._runs.steps(task_id)),
            edits=tuple(self._runs.edits(task_id)),
        )

    # --- the three entry points ------------------------------------------

    async def start(
        self,
        *,
        task_id: str,
        feature_id: str,
        user_id: int,
        plan: Sequence[FeatureStep],
        snapshot: Mapping[str, Any],
        run_turn: StepTurn,
    ) -> RunState:
        """Begin a run: validate it is runnable, then walk from the first step.

        A plan this build cannot run is refused *before* any row is written: an
        unimplemented mode must leave no half-run behind that later reads as if it
        had executed.
        """
        refuse_unsupported(feature_id, plan)
        self._runs.create(
            task_id=task_id,
            feature_id=feature_id,
            user_id=user_id,
            status=RUN_RUNNING,
            plan=[step.as_dict() for step in plan],
            snapshot=snapshot,
            step_ids=[step.id for step in plan],
        )
        await self._walk(task_id, tuple(plan), start_seq=0, run_turn=run_turn)
        state = self.state(task_id)
        assert state is not None
        return state

    async def approve(
        self,
        state: RunState,
        *,
        edits: Mapping[str, Any] | None,
        by_user_id: int,
        run_turn: StepTurn,
    ) -> RunState:
        """Answer the gate the run waits on, then walk on from the next step.

        Approving continues **this** run: the steps before the gate keep their
        artifacts, and only the steps after it are ever asked of the model again.
        """
        gate = self._open_gate(state)
        step_id = str(gate["step_id"])
        step = state.step(step_id)
        seq = state.seq_of(step_id)
        if gate["gate"] == GATE_VALIDATE:
            raise RunNotAtGate(
                f"run {state.task_id!r} stopped at a check gate that did not pass; approval "
                "cannot override it — rewind the run with the corrected artifact"
            )
        planned = self._plan_edits(state, edits=edits, step=step, seq=seq)
        self._claim(state)
        self._write_edits(state, planned, by_user_id=by_user_id, source=EDIT_SOURCE_APPROVE)
        await self._walk(state.task_id, state.plan, start_seq=seq + 1, run_turn=run_turn)
        resumed = self.state(state.task_id)
        assert resumed is not None
        return resumed

    async def rewind(
        self,
        state: RunState,
        *,
        to_step: str | int,
        edits: Mapping[str, Any] | None,
        by_user_id: int,
        run_turn: StepTurn,
    ) -> RunState:
        """Go back to a step, void what came after it, and walk again from there.

        Without ``edits`` this is 回退重跑. With them it is 带修正重跑: the corrected
        values are injected as the artifacts the target step consumes, which is why
        the edits are audited first — "这一步人工把切削速度从 120 改成 100" has to be
        answerable after the fact.
        """
        target = resolve_step(state.plan, to_step)
        target_seq = state.seq_of(target.id)
        planned = self._plan_edits(state, edits=edits, step=None, seq=target_seq)
        self._claim(state)
        self._void_from(state, target_seq, by_user_id=by_user_id)
        self._write_edits(state, planned, by_user_id=by_user_id, source=EDIT_SOURCE_REWIND)
        await self._walk(state.task_id, state.plan, start_seq=target_seq, run_turn=run_turn)
        resumed = self.state(state.task_id)
        assert resumed is not None
        return resumed

    # --- walking the plan -------------------------------------------------

    async def _walk(
        self,
        task_id: str,
        plan: Sequence[FeatureStep],
        *,
        start_seq: int,
        run_turn: StepTurn,
    ) -> None:
        """Run the plan from *start_seq* until a gate, a failure, or the end."""
        artifacts = self._artifacts(task_id)
        self._runs.update(
            task_id,
            status=RUN_RUNNING,
            current_step=None,
            current_seq=start_seq,
            pending_gate=None,
        )
        for seq, step in enumerate(plan):
            if seq < start_seq:
                continue
            self._runs.update(task_id, status=RUN_RUNNING, current_step=step.id, current_seq=seq)
            artifact, error = await self._attempt(task_id, seq, step, artifacts, run_turn)
            if error is None and artifact is not None:
                artifacts[artifact.name] = artifact
                if step.gate == GATE_CONFIRM:
                    self._hold_at_gate(task_id, step, seq, GATE_CONFIRM)
                    return
                continue
            if step.on_failure == ON_FAILURE_ESCALATE:
                self._hold_at_gate(task_id, step, seq, GATE_ESCALATED)
                return
            self._fail(task_id, step, seq, error or f"step {step.id!r} produced no artifact")
            return
        self._finish(task_id, plan)

    async def _attempt(
        self,
        task_id: str,
        seq: int,
        step: FeatureStep,
        artifacts: Mapping[str, Artifact],
        run_turn: StepTurn,
    ) -> tuple[Artifact | None, str | None]:
        """One step's turns — retried when it declares ``retry``, never silently.

        ``on_failure: retry`` buys :data:`~octop.infra.features.steps.MAX_STEP_ATTEMPTS`
        attempts and then fails: a retry is a second chance, and the step's own
        error is what the run reports if that chance is spent.

        How the failure is recorded follows the step's own declaration: a step that
        escalates ends as ``escalated`` (this run is waiting for a human decision
        about *it*), every other failure ends as ``failed``.
        """
        attempts = MAX_STEP_ATTEMPTS if step.on_failure == ON_FAILURE_RETRY else 1
        failure_status = STEP_ESCALATED if step.on_failure == ON_FAILURE_ESCALATE else STEP_FAILED
        error: str | None = None
        for _attempt in range(attempts):
            self._runs.start_step(task_id, seq)
            try:
                answer = await run_turn(step, artifacts)
                value = parse_artifact(step, answer)
                if step.gate == GATE_VALIDATE and not verdict_of(step.output, value):
                    raise StepGateFailed(
                        f"step {step.id!r} did not pass its check gate: "
                        f"{json.dumps(value, ensure_ascii=False)[:500]}"
                    )
                artifact = Artifact(
                    name=step.output.name,
                    schema=step.output.schema,
                    value=value,
                    step_id=step.id,
                )
            except Exception as exc:  # noqa: BLE001 - every failure is this step's
                error = reason_of(exc)
                self._runs.finish_step(
                    task_id, seq, status=failure_status, artifact=None, error=error
                )
                logger.warning("feature run %s step %s failed: %s", task_id, step.id, error)
                continue
            self._runs.finish_step(
                task_id, seq, status=STEP_SUCCEEDED, artifact=artifact, error=None
            )
            return artifact, None
        return None, error

    # --- transitions ------------------------------------------------------

    def _hold_at_gate(self, task_id: str, step: FeatureStep, seq: int, gate: str) -> None:
        """Stop the run at *step*'s gate and record what a human is being asked."""
        self._runs.update(
            task_id,
            status=_GATE_RUN_STATUS[gate],
            current_step=step.id,
            current_seq=seq,
            pending_gate={
                "step_id": step.id,
                "name": step.name,
                "gate": gate,
                "allow_edit": step.allow_edit,
            },
        )

    def _fail(self, task_id: str, step: FeatureStep, seq: int, error: str) -> None:
        """End the run as failed — and log that outcome on the task row too."""
        self._runs.update(
            task_id,
            status=RUN_FAILED,
            current_step=step.id,
            current_seq=seq,
            pending_gate=None,
            error=error,
        )
        self._tasks.finish(task_id, status=RUN_FAILED, draft=None, error=error)

    def _finish(self, task_id: str, plan: Sequence[FeatureStep]) -> None:
        """The last step produced its artifact: the run succeeded and has output."""
        self._runs.update(
            task_id, status=RUN_SUCCEEDED, current_step=None, pending_gate=None, error=None
        )
        output = ""
        if plan:
            state = self.state(task_id)
            final = state.final_artifact() if state is not None else None
            if final is not None:
                step, artifact = final
                output = render_value(step.output, artifact.value)
        self._tasks.finish(task_id, status=RUN_SUCCEEDED, draft=output, error=None)

    # --- human writes -----------------------------------------------------

    def _claim(self, state: RunState) -> None:
        """Take the run out of the state this action was decided on.

        A gate is where two people (or one impatient double-click) can dispatch the
        same continuation; the run going back to ``running`` is what makes the
        second one lose. Nothing is written for the action before this succeeds.
        """
        if not self._runs.claim(
            state.task_id, expected_status=state.row.status, new_status=RUN_RUNNING
        ):
            raise RunTaken(
                f"run {state.task_id!r} was moved on by another action while this one was "
                "being prepared; reload the run and decide again"
            )

    def _open_gate(self, state: RunState) -> Mapping[str, Any]:
        gate = state.row.pending_gate_payload()
        if gate is None or state.row.status not in (RUN_AWAITING_GATE, RUN_ESCALATED):
            raise RunNotAtGate(
                f"run {state.task_id!r} is {state.row.status!r}: it is not waiting at a gate"
            )
        return gate

    def _void_from(self, state: RunState, seq: int, *, by_user_id: int) -> None:
        """Log what the rewind throws away, then throw it away."""
        for row in state.steps:
            if row.seq < seq:
                continue
            artifact = row.artifact()
            if artifact is None:
                continue
            self._runs.record_edit(
                state.task_id,
                step_id=row.step_id,
                artifact=artifact.name,
                before=artifact.value,
                after=None,
                by_user_id=by_user_id,
                kind=EDIT_KIND_VOID,
                source=EDIT_SOURCE_REWIND,
            )
        self._runs.void_steps_from(state.task_id, seq)

    def _plan_edits(
        self,
        state: RunState,
        *,
        edits: Mapping[str, Any] | None,
        step: FeatureStep | None,
        seq: int,
    ) -> list[_PlannedEdit]:
        """Check every human correction without writing anything.

        Two callers, two rules. At a gate, the only artifact a human may correct is
        the one that gate's step produced — *step* is that step. On a rewind, they
        correct an *input* of the step the run goes back to, so the edit must name
        an artifact of a step the rewind did not void.

        Checking and writing are separate on purpose: a refused edit must leave the
        run exactly where it was, so nothing is written until the action has claimed
        the run.
        """
        artifacts = state.artifacts()
        planned: list[_PlannedEdit] = []
        for name, value in (edits or {}).items():
            producer, producer_seq = self._producer(state, name)
            if step is not None:
                if producer is not step:
                    raise RunEditInvalid(
                        f"artifact {name!r} belongs to step {producer.id!r}, but this gate "
                        f"opened on step {step.id!r}"
                    )
                if not step.allow_edit:
                    raise RunEditInvalid(
                        f"step {step.id!r} does not allow edits: it was declared without allow_edit"
                    )
            elif producer_seq >= seq:
                raise RunEditInvalid(
                    f"artifact {name!r} is produced by step {producer.id!r}, which this rewind "
                    "voids; correct an artifact of an earlier step or rewind further back"
                )
            elif not producer.allow_edit:
                raise RunEditInvalid(
                    f"artifact {name!r} belongs to step {producer.id!r}, which does not allow "
                    "edits: correct it only if that step declares allow_edit"
                )
            try:
                checked = validate_artifact(producer.output, value)
            except StepError as exc:
                raise RunEditInvalid(f"artifact {name!r} was not accepted: {exc}") from exc
            planned.append(
                _PlannedEdit(
                    seq=producer_seq,
                    artifact=Artifact(
                        name=producer.output.name,
                        schema=producer.output.schema,
                        value=checked,
                        step_id=producer.id,
                    ),
                    before=artifacts[name].value if name in artifacts else None,
                )
            )
        return planned

    def _write_edits(
        self,
        state: RunState,
        planned: Sequence[_PlannedEdit],
        *,
        by_user_id: int,
        source: str,
    ) -> None:
        """Record each correction and apply it — the log entry first, then the value."""
        for edit in planned:
            self._runs.record_edit(
                state.task_id,
                step_id=edit.artifact.step_id,
                artifact=edit.artifact.name,
                before=edit.before,
                after=edit.artifact.value,
                by_user_id=by_user_id,
                kind=EDIT_KIND_EDIT,
                source=source,
            )
            self._runs.set_artifact(state.task_id, edit.seq, edit.artifact, status=STEP_SUCCEEDED)

    def _producer(self, state: RunState, name: str) -> tuple[FeatureStep, int]:
        """The step that produces artifact *name* — an unknown name is refused."""
        for seq, step in enumerate(state.plan):
            if step.output.name == name:
                return step, seq
        raise RunEditInvalid(
            f"artifact {name!r} is not produced by any step of run {state.task_id!r}"
        )

    def _artifacts(self, task_id: str) -> dict[str, Artifact]:
        """The artifacts a run currently holds, rebuilt from its rows."""
        out: dict[str, Artifact] = {}
        for row in self._runs.steps(task_id):
            artifact = row.artifact()
            if artifact is not None:
                out[artifact.name] = artifact
        return out


def reason_of(exc: BaseException) -> str:
    """One failure as the text the run records for it."""
    if isinstance(exc, StepError):
        return str(exc)
    return f"{type(exc).__name__}: {exc}"


__all__ = [
    "EDIT_KIND_EDIT",
    "EDIT_KIND_VOID",
    "EDIT_SOURCE_APPROVE",
    "EDIT_SOURCE_REWIND",
    "GATE_ESCALATED",
    "RUN_AWAITING_GATE",
    "RUN_ESCALATED",
    "RUN_FAILED",
    "RUN_RUNNING",
    "RUN_SUCCEEDED",
    "FeatureRunEngine",
    "RunEditInvalid",
    "RunError",
    "RunNotAtGate",
    "RunState",
    "RunTaken",
    "StepTurn",
    "plan_from_row",
    "reason_of",
    "refuse_unsupported",
]
