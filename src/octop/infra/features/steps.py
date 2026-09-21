"""Task steps — the linear skeleton a feature's run walks (design 7.3 / 7.10).

A feature either runs in one shot (``prompt.user_template``, the M1 behaviour) or
declares ``steps``: an ordered list of turns, each producing **one typed
artifact**, with gates between them. This module is the format's own judge and
nothing else — it parses and validates step declarations, and it is the single
place that turns a step's answer into its declared artifact type. The run state
machine lives in :mod:`octop.infra.features.runs`, the prompt rendering in
:mod:`octop.infra.features.catalog`.

Two rules this module enforces on purpose:

* **No defaults.** ``mode``, ``gate``, ``on_failure``, ``prompt`` and ``output``
  are required; a step that omits one is refused with the field named. A gate read
  as "auto" because the author forgot to say would be a workflow nobody approved.
* **What the platform cannot do is refused, not degraded.** ``mode:
  "orchestrate"`` and ``agent_role`` now run as declared: the model owns the
  decomposition and dispatches its subagents through the harness's own ``task``
  tool, while the platform only bounds how many run at once (``max_parallel``) and
  records what was dispatched (7.8's 分解留痕) — it never schedules, and it never
  quietly runs as a single agent a step the author declared as several.
  :func:`unsupported_reasons` names the one step this build still cannot run: a
  step that must dispatch while its own ``tools`` allow-list hides
  :data:`DISPATCH_TOOL_NAME`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.agents.tool_catalog import BUILTIN_TOOL_CATALOG, CRITICAL_TOOLS

GATE_AUTO = "auto"
"""The run walks on by itself once the step succeeded."""

GATE_CONFIRM = "confirm"
"""The run stops and waits for a human to approve (design 7.2's 人工门)."""

GATE_VALIDATE = "validate"
"""The run reads a boolean verdict and refuses to deliver without it (7.2's 校验门)."""

GATES: tuple[str, ...] = (GATE_AUTO, GATE_CONFIRM, GATE_VALIDATE)

MODE_AGENT = "agent"
"""One agent does one thing: one turn, no subagents of its own."""

MODE_ORCHESTRATE = "orchestrate"
"""The model decomposes the step and dispatches its subagents (7.6); the platform
only caps how many run at once and records them (7.8)."""

MODES: tuple[str, ...] = (MODE_AGENT, MODE_ORCHESTRATE)

DISPATCH_TOOL_NAME = "task"
"""The harness tool a step dispatches a subagent with (deepagents' ``SubAgentMiddleware``).

A step that decomposes (``orchestrate``) or that names an ``agent_role`` can only
do what it declares through this tool, so it is the one name
:func:`unsupported_reasons` refuses to see missing from a step's allow-list.
``octop.infra.agents.middleware.feature_scope.TASK_TOOL_NAME`` names the same tool
for the model's surface; the two values describe one tool and must stay equal.
"""

ON_FAILURE_ABORT = "abort"
"""A failed step fails the run."""

ON_FAILURE_ESCALATE = "escalate"
"""A failed step stops the run for a human — the platform never decides alone."""

ON_FAILURE_RETRY = "retry"
"""A failed step gets a second attempt; then it fails like ``abort``."""

ON_FAILURES: tuple[str, ...] = (ON_FAILURE_ABORT, ON_FAILURE_ESCALATE, ON_FAILURE_RETRY)

STEP_PENDING = "pending"
STEP_RUNNING = "running"
STEP_SUCCEEDED = "succeeded"
STEP_FAILED = "failed"
STEP_ESCALATED = "escalated"
STEP_VOIDED = "voided"
"""Step states in the run log. ``voided`` is a step a rewind threw away."""

SCHEMA_TEXT = "text"
SCHEMA_LIST = "list"
SCHEMA_OBJECT = "object"
SCHEMA_TABLE = "table"
VERDICT_FIELD = "passed"
"""Boolean a ``validate`` gate reads out of its step's artifact."""

MAX_STEP_ATTEMPTS = 2
"""Turns one step gets when it declares ``on_failure: retry`` (one extra attempt).

A retry is a second chance, not a loop: without a bound, a step that cannot
succeed would spend the instance's tokens on its own. The bound is the engine's,
not the definition's — 7.10 has no retry budget — and is reported in the run's
own error when the attempts are used up.
"""

SCHEMA_FORMS = "text, list, object, table, table:<N>cols"
"""Artifact schemas this engine can read back. Anything else is refused."""

_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_TABLE_RE = re.compile(r"^table(?::(\d+)cols)?$")
_FENCED_RE = re.compile(r"```(?:json)?[ \t]*\r?\n(.*?)```", re.DOTALL)
_JSON_START_RE = re.compile(r"[\[{]")

_STEP_KEYS = frozenset(
    {
        "id",
        "name",
        "mode",
        "inputs",
        "tools",
        "max_parallel",
        "output",
        "prompt",
        "gate",
        "allow_edit",
        "on_failure",
        "agent_role",
    }
)
_OUTPUT_KEYS = frozenset({"name", "schema"})
_KNOWN_TOOLS = frozenset({entry.name for entry in BUILTIN_TOOL_CATALOG}) | CRITICAL_TOOLS


class StepError(RuntimeError):
    """A step that cannot produce the artifact it declared."""


class StepInputMissing(StepError):
    """A step's declared input artifact is not in the run."""


class StepOutputInvalid(StepError):
    """A step's answer does not carry the declared artifact type."""


class StepGateFailed(StepError):
    """A ``validate`` gate read ``passed: false`` — the run must not deliver."""


class StepUnsupported(StepError):
    """A step declares something this build does not run (never silently skipped)."""


class StepAgentRoleUnmet(StepError):
    """A step declaring ``agent_role`` ran without ever dispatching that subagent."""


class StepUnknown(StepError):
    """A step an action names is not part of the run (rewind target, edit target)."""


@dataclass(frozen=True)
class StepOutput:
    """The artifact one step produces: its name in the run, and its declared type."""

    name: str
    schema: str

    @property
    def is_text(self) -> bool:
        return self.schema == SCHEMA_TEXT

    def as_dict(self) -> dict[str, str]:
        return {"name": self.name, "schema": self.schema}


@dataclass(frozen=True)
class Artifact:
    """One artifact as it exists in a run: the value plus where it came from."""

    name: str
    schema: str
    value: Any
    step_id: str

    def as_dict(self) -> dict[str, Any]:
        """Named by the type it was declared with — the form the engine reports."""
        return {"name": self.name, "schema": self.schema, "value": self.value}


@dataclass(frozen=True)
class FeatureStep:
    """One declared step, parsed (see :func:`parse_steps`).

    ``as_dict`` returns the declaration **as authored**: the settings UI reads a
    step back to edit it, so a field the author never wrote must stay absent
    rather than come back as a value the platform chose for them.
    """

    id: str
    name: str
    mode: str
    inputs: tuple[str, ...]
    tools: tuple[str, ...] | None
    max_parallel: int | None
    output: StepOutput
    prompt: str
    gate: str
    allow_edit: bool
    on_failure: str
    agent_role: str | None
    declared: Mapping[str, Any]

    def as_dict(self) -> dict[str, Any]:
        return dict(self.declared)

    def snapshot(self) -> dict[str, Any]:
        """The step as the run's snapshot records it — every effective value."""
        return {
            "id": self.id,
            "name": self.name,
            "mode": self.mode,
            "inputs": list(self.inputs),
            "tools": None if self.tools is None else list(self.tools),
            "max_parallel": self.max_parallel,
            "output": self.output.as_dict(),
            "gate": self.gate,
            "allow_edit": self.allow_edit,
            "on_failure": self.on_failure,
            "agent_role": self.agent_role,
        }


def parse_steps(raw: Any) -> tuple[list[FeatureStep], list[str]]:
    """Parse a definition's ``steps`` node; ``(steps, errors)``.

    Every problem found is reported (not just the first), because the settings UI
    shows the author the whole list and a definition that fails validation is
    never written. An empty/absent node is ``[]`` — a feature with no steps is the
    single-shot feature it was before this format existed.
    """
    if raw is None:
        return [], []
    if not isinstance(raw, list):
        return [], ["steps must be an array"]

    steps: list[FeatureStep] = []
    errors: list[str] = []
    produced: dict[str, str] = {}
    for index, node in enumerate(raw):
        where = f"steps[{index}]"
        before = len(errors)
        step = _step_from_node(node, where, produced, errors)
        if step is None or len(errors) > before:
            # A node with any problem is not half a step: it is no step, and the
            # plan it belongs to is refused as a whole.
            continue
        steps.append(step)
        produced[step.output.name] = step.id
    return steps, errors


def _step_from_node(
    node: Any,
    where: str,
    produced: Mapping[str, str],
    errors: list[str],
) -> FeatureStep | None:
    """One step, or ``None`` with every problem it has appended to *errors*."""
    if not isinstance(node, dict):
        errors.append(f"{where} must be an object")
        return None
    unknown = sorted(set(node) - _STEP_KEYS)
    if unknown:
        errors.append(f"{where} uses unsupported keys: {_quoted(unknown)}")

    step_id = _identifier(node.get("id"), f"{where}.id", errors)
    name = _text(node.get("name"), f"{where}.name", errors)
    prompt = _text(node.get("prompt"), f"{where}.prompt", errors)
    mode = _enum(node.get("mode"), f"{where}.mode", MODES, errors)
    gate = _enum(node.get("gate"), f"{where}.gate", GATES, errors)
    on_failure = _enum(node.get("on_failure"), f"{where}.on_failure", ON_FAILURES, errors)
    output = _output(node.get("output"), f"{where}.output", errors)
    inputs = _names(node.get("inputs"), f"{where}.inputs", errors)
    tools = _tools(node, where, errors)
    max_parallel = _max_parallel(node.get("max_parallel"), f"{where}.max_parallel", errors)
    agent_role = node.get("agent_role")
    if agent_role is not None and not isinstance(agent_role, str):
        errors.append(f"{where}.agent_role must be a string or null")
        agent_role = None

    allow_edit = _allow_edit(node, where, errors)
    if output is not None and gate == GATE_VALIDATE and output.schema != SCHEMA_OBJECT:
        errors.append(
            f"{where}.output.schema must be {SCHEMA_OBJECT!r} for a {GATE_VALIDATE!r} gate: "
            f"the engine reads a boolean {VERDICT_FIELD!r} out of it"
        )
    if step_id is not None and output is not None and output.name in produced:
        errors.append(
            f"{where}.output.name {output.name!r} was already produced by step "
            f"{produced[output.name]!r}"
        )
    for input_name in inputs:
        if input_name not in produced:
            errors.append(f"{where}.inputs names {input_name!r}, which no earlier step produces")
    if (
        step_id is None
        or name is None
        or prompt is None
        or mode is None
        or gate is None
        or on_failure is None
        or output is None
    ):
        return None
    return FeatureStep(
        id=step_id,
        name=name,
        mode=mode,
        inputs=inputs,
        tools=tools,
        max_parallel=max_parallel,
        output=output,
        prompt=prompt,
        gate=gate,
        allow_edit=allow_edit,
        on_failure=on_failure,
        agent_role=agent_role,
        declared=dict(node),
    )


def dispatch_required(step: FeatureStep) -> bool:
    """Whether *step* has to be able to call :data:`DISPATCH_TOOL_NAME`.

    True for a step that declares ``mode: "orchestrate"`` (it decomposes itself,
    7.6) or an ``agent_role`` (it runs as that named subagent): both reach their
    subagents through the harness's ``task`` tool and have nothing else to reach
    them with.
    """
    return step.mode == MODE_ORCHESTRATE or step.agent_role is not None


def unsupported_reasons(steps: Sequence[FeatureStep]) -> list[str]:
    """What this build cannot run about these steps, in the author's own terms.

    Empty means the plan is runnable. ``orchestrate`` and ``agent_role`` are
    runnable: the model decomposes the step and dispatches its own subagents,
    while the platform only caps how many run at once and records what ran. The
    one thing refused here is a step that must dispatch while its declared
    ``tools`` allow-list hides :data:`DISPATCH_TOOL_NAME` — it would run to the
    end and hand back a result the author believes was decomposed, which is the
    silent degradation this format exists to prevent. ``tools`` is a scope the run
    resolves (absent inherits the run's surface, ``[]`` allows none), so the check
    belongs to the run that knows what the step actually got.
    """
    reasons: list[str] = []
    for step in steps:
        if (
            dispatch_required(step)
            and step.tools is not None
            and DISPATCH_TOOL_NAME not in step.tools
        ):
            reasons.append(
                f"step {step.id!r} cannot dispatch: its tool allow-list leaves out the "
                f"dispatch tool {DISPATCH_TOOL_NAME!r}, so it cannot decompose the step or "
                "run as the subagent it declares"
            )
    return reasons


def resolve_step(steps: Sequence[FeatureStep], target: str | int) -> FeatureStep:
    """The step *target* names — its id, or its position when given a number.

    Step ids are lowercase identifiers, so a number is never an id: the two forms
    cannot be confused, and a wrong one is reported instead of rounded to a
    neighbour.
    """
    if isinstance(target, bool) or not isinstance(target, (str, int)):
        raise StepUnknown(f"unknown step {target!r}")
    if isinstance(target, int):
        if 0 <= target < len(steps):
            return steps[target]
        raise StepUnknown(f"step index {target} is outside the run's {len(steps)} steps")
    for step in steps:
        if step.id == target:
            return step
    raise StepUnknown(f"step {target!r} is not part of this run")


def validate_artifact(output: StepOutput, value: Any) -> Any:
    """Check *value* against a declared artifact type; return it unchanged.

    Used for a step's own answer and for a human's ``edits`` — a correction that
    does not satisfy the declared type would otherwise travel on into the next
    step as a value the model was promised could not happen.
    """
    if output.is_text:
        if not isinstance(value, str):
            raise StepOutputInvalid(
                f"artifact {output.name!r} is declared {SCHEMA_TEXT!r} but the value is "
                f"{_type_name(value)}"
            )
        return value
    if output.schema == SCHEMA_LIST:
        if not isinstance(value, list):
            raise StepOutputInvalid(
                f"artifact {output.name!r} is declared {SCHEMA_LIST!r} but the value is "
                f"{_type_name(value)}"
            )
        return value
    if output.schema == SCHEMA_OBJECT:
        if not isinstance(value, dict):
            raise StepOutputInvalid(
                f"artifact {output.name!r} is declared {SCHEMA_OBJECT!r} but the value is "
                f"{_type_name(value)}"
            )
        return value
    return _validate_table(output, value)


def _validate_table(output: StepOutput, value: Any) -> Any:
    """A table is rows of equal width; ``table:<N>cols`` pins that width."""
    if not isinstance(value, list) or not value:
        raise StepOutputInvalid(
            f"artifact {output.name!r} is declared {output.schema!r} but the value is "
            f"{_type_name(value)}"
        )
    declared = _TABLE_RE.match(output.schema)
    width = int(declared.group(1)) if declared and declared.group(1) else None
    first = _row_width(value[0])
    if first is None:
        raise StepOutputInvalid(
            f"artifact {output.name!r} is declared {output.schema!r} but row 0 is "
            f"{_type_name(value[0])}, not a list of cells or an object"
        )
    if width is not None and first != width:
        raise StepOutputInvalid(
            f"artifact {output.name!r} is declared {output.schema!r} but row 0 has {first} cells"
        )
    for index, row in enumerate(value[1:], start=1):
        row_width = _row_width(row)
        if row_width is None:
            raise StepOutputInvalid(
                f"artifact {output.name!r} is declared {output.schema!r} but row {index} is "
                f"{_type_name(row)}, not a list of cells or an object"
            )
        if row_width != first:
            raise StepOutputInvalid(
                f"artifact {output.name!r} is declared {output.schema!r} but row {index} has "
                f"{row_width} cells where row 0 has {first}"
            )
    return value


def _row_width(row: Any) -> int | None:
    if isinstance(row, list):
        return len(row)
    if isinstance(row, dict):
        return len(row)
    return None


def parse_artifact(step: FeatureStep, text: str) -> Any:
    """A step's answer as its declared artifact, or a loud failure.

    A ``text`` artifact is the answer verbatim. Everything else must carry JSON —
    a fenced block when the model wrote one, otherwise the first JSON value in the
    answer — and must have the declared shape. An answer that is prose, or JSON of
    the wrong shape, fails the step: storing it would pass a string on to the next
    step where the definition promised a table (7.2's whole point).
    """
    if step.output.is_text:
        if not text.strip():
            raise StepOutputInvalid(f"step {step.id!r} produced no text")
        return text
    return validate_artifact(step.output, extract_json(text, output=step.output))


def extract_json(text: str, *, output: StepOutput | None = None) -> Any:
    """The first JSON value in *text* (fenced blocks first).

    *output* only names what the failure is about: an answer the engine cannot
    read at all has to say which artifact it was supposed to be, or the run's
    error reads as if the model had simply been quiet.
    """
    for block in _FENCED_RE.finditer(text):
        try:
            return json.loads(block.group(1))
        except ValueError:
            continue
    stripped = text.strip()
    try:
        return json.loads(stripped)
    except ValueError:
        pass
    decoder = json.JSONDecoder()
    for match in _JSON_START_RE.finditer(stripped):
        try:
            value, _end = decoder.raw_decode(stripped, match.start())
        except ValueError:
            continue
        return value
    subject = "the answer" if output is None else _output_name(output)
    raise StepOutputInvalid(f"{subject} carries no JSON value")


def _output_name(output: StepOutput) -> str:
    return f"artifact {output.name!r} (declared {output.schema!r})"


def verdict_of(output: StepOutput, value: Any) -> bool:
    """The boolean a ``validate`` gate reads — or a loud failure.

    A gate whose verdict cannot be read is a broken gate, not a passed one: this
    is the one place where being permissive would silently deliver unchecked work.
    """
    if not isinstance(value, dict) or not isinstance(value.get(VERDICT_FIELD), bool):
        raise StepOutputInvalid(
            f"artifact {output.name!r} must carry a boolean {VERDICT_FIELD!r} for a "
            f"{GATE_VALIDATE!r} gate; got {json.dumps(value, ensure_ascii=False)[:200]}"
        )
    return bool(value[VERDICT_FIELD])


def render_value(output: StepOutput, value: Any) -> str:
    """One artifact as the text a run reports as its output."""
    if output.is_text:
        return str(value)
    return json.dumps(value, ensure_ascii=False, indent=2)


def output_kind_of(output: StepOutput) -> str:
    """The ``output_kind`` a stepped run reports — the artifact's own type."""
    return "text" if output.is_text else "json"


# --- declaration validation helpers ----------------------------------------


def _quoted(values: Any) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _identifier(value: Any, where: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not _ID_RE.match(value):
        errors.append(
            f"{where} must be a lowercase identifier of letters, digits or '_' "
            "(at most 64 characters, starting with a letter)"
        )
        return None
    return value


def _text(value: Any, where: str, errors: list[str]) -> str | None:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{where} must be a non-empty string")
        return None
    return value.strip()


def _enum(value: Any, where: str, allowed: Sequence[str], errors: list[str]) -> str | None:
    if value is None:
        errors.append(f"{where} must be declared (one of {_quoted(allowed)})")
        return None
    if value not in allowed:
        errors.append(f"{where} must be one of {_quoted(allowed)}")
        return None
    return str(value)


def _names(value: Any, where: str, errors: list[str]) -> tuple[str, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        errors.append(f"{where} must be an array of artifact names")
        return ()
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not _ID_RE.match(item):
            errors.append(f"{where} must name artifacts with lowercase identifiers")
            return ()
        if item not in names:
            names.append(item)
    return tuple(names)


def _tools(node: Mapping[str, Any], where: str, errors: list[str]) -> tuple[str, ...] | None:
    """A step's tool allow-list: absent inherits, ``[]`` means "no tools at all"."""
    if "tools" not in node or node.get("tools") is None:
        return None
    value = node.get("tools")
    if not isinstance(value, list):
        errors.append(f"{where}.tools must be an array of tool names")
        return None
    names: list[str] = []
    for item in value:
        if not isinstance(item, str) or not item.strip():
            errors.append(f"{where}.tools must name tools with non-empty strings")
            return None
        names.append(item.strip())
    unknown = sorted({name for name in names if name not in _KNOWN_TOOLS})
    if unknown:
        errors.append(f"{where}.tools names unknown built-in tools: {_quoted(unknown)}")
    return tuple(names)


def _max_parallel(value: Any, where: str, errors: list[str]) -> int | None:
    """The ceiling 7.7 puts on the model's own parallelism inside one step.

    Absent is not a zero and not a guess: it means "inherit" — the feature's
    ``agent.max_parallel``, else
    :data:`~octop.infra.features.dispatch.DEFAULT_MAX_PARALLEL`, as
    :func:`~octop.infra.features.dispatch.resolve_max_parallel` resolves it. The
    boundary in ``octop.infra.agents.middleware.feature_dispatch`` is what enforces
    it on the step's dispatches.
    """
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        errors.append(f"{where} must be a positive integer")
        return None
    return value


def _allow_edit(node: Mapping[str, Any], where: str, errors: list[str]) -> bool:
    """Whether a human may correct this step's artifact when an action names it.

    Optional everywhere, absent means no: a step whose author never said a human
    may rewrite its output is a step where a human edit is refused *loudly* — at
    the gate that opens on it, or at the rewind that tries to correct it. The flag
    is not tied to a gate kind, because a rewind can name the artifact of any step
    the run still holds, including one whose gate is ``auto`` (the escalation path:
    the step reported a conflict, a human decides what the value should have been).
    """
    value = node.get("allow_edit")
    if value is None:
        return False
    if not isinstance(value, bool):
        errors.append(f"{where}.allow_edit must be true or false")
        return False
    return value


def _output(value: Any, where: str, errors: list[str]) -> StepOutput | None:
    """The typed artifact a step produces — both halves are required."""
    if not isinstance(value, dict):
        errors.append(f"{where} must be an object with 'name' and 'schema'")
        return None
    unknown = sorted(set(value) - _OUTPUT_KEYS)
    if unknown:
        errors.append(f"{where} uses unsupported keys: {_quoted(unknown)}")
    name = _identifier(value.get("name"), f"{where}.name", errors)
    schema = value.get("schema")
    if not isinstance(schema, str) or not _schema_known(schema):
        errors.append(f"{where}.schema must be one of {SCHEMA_FORMS}")
        return None
    if name is None:
        return None
    return StepOutput(name=name, schema=schema)


def _schema_known(schema: str) -> bool:
    if schema in (SCHEMA_TEXT, SCHEMA_LIST, SCHEMA_OBJECT, SCHEMA_TABLE):
        return True
    declared = _TABLE_RE.match(schema)
    if declared is None or not declared.group(1):
        return False
    return int(declared.group(1)) >= 1


def _type_name(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "a boolean"
    if isinstance(value, list):
        return "an array"
    if isinstance(value, dict):
        return "an object"
    if isinstance(value, str):
        return "a string"
    return f"a {type(value).__name__}"


__all__ = [
    "DISPATCH_TOOL_NAME",
    "GATE_AUTO",
    "GATE_CONFIRM",
    "GATE_VALIDATE",
    "GATES",
    "MAX_STEP_ATTEMPTS",
    "MODE_AGENT",
    "MODE_ORCHESTRATE",
    "MODES",
    "ON_FAILURE_ABORT",
    "ON_FAILURE_ESCALATE",
    "ON_FAILURE_RETRY",
    "ON_FAILURES",
    "SCHEMA_FORMS",
    "STEP_ESCALATED",
    "STEP_FAILED",
    "STEP_PENDING",
    "STEP_RUNNING",
    "STEP_SUCCEEDED",
    "STEP_VOIDED",
    "Artifact",
    "FeatureStep",
    "StepAgentRoleUnmet",
    "StepError",
    "StepGateFailed",
    "StepInputMissing",
    "StepOutput",
    "StepOutputInvalid",
    "StepUnsupported",
    "StepUnknown",
    "dispatch_required",
    "extract_json",
    "output_kind_of",
    "parse_artifact",
    "parse_steps",
    "render_value",
    "resolve_step",
    "unsupported_reasons",
    "validate_artifact",
    "verdict_of",
]
