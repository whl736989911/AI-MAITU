"""A feature's workflow: what it asks for, the fixed steps it runs, what comes back.

A feature *is* an agent (:mod:`octop.infra.agents.kinds`), and this module holds the
one part of it that an expert has no equivalent of: a **declarative workflow** —
the form the caller fills in, the ordered steps the run follows, the artifacts it
hands back, and the rules that hold for every step.

**Where it lives.** ``{workspace}/.octop/workflow.json``, read and written through
``HarnessAgent.workspace`` like every other workspace content file. It travels with
the agent: publishing a feature as an expert template, installing one, backing the
instance up and restoring it all carry the definition along, and nothing has to be
re-created in a second store. The neighbouring ``.octop/manifest.json`` (welcome
cards) is the same idea and the same directory.

**Two statuses, two strictnesses.** ``draft`` is the training state: only the shape
of the document is checked, so a workflow can be saved half-written while its author
(or the configuration assistant) works it out in conversation. ``active`` is the
published state: every step must carry a name and a prompt, the document may not be
empty, and references have to be resolvable. The status is stored, not inferred, so
``GET`` answers whether a definition is ready to be offered to callers.

**The run is prompt-driven, on purpose.** Nothing here schedules steps: the
definition is rendered into one system block
(:func:`render_run_block`) that tells the model which steps to follow in which
order, where a human gate stops it, and which rules hold. A step turn may still
decompose its own work across subagents — the platform neither plans nor forbids
that. What the platform does keep is the *declaration*: this module is the single
place that decides whether a definition is well-formed.

**Rules here are the soft ones.** A feature's own ``rules`` are written by its
author and may be overridden per caller by that caller's personal overlay (a text
layer injected above this block). Enterprise-wide hard rules are a different layer
with a different owner — they are injected before both and are not part of a
feature's definition, so they cannot be edited, weakened or dropped from here.
"""

from __future__ import annotations

import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from octop.i18n import tr
from octop.infra.agents.workspace_dir import DEFAULT_SYSTEM_FILES_PATH
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.locale import normalize_locale

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

logger = logging.getLogger(__name__)

WORKFLOW_FILENAME = "workflow.json"
"""The definition's filename inside the workspace system directory."""

WORKSPACE_WORKFLOW_PATH = f"{DEFAULT_SYSTEM_FILES_PATH}/{WORKFLOW_FILENAME}"
"""Agent-workspace path of the definition (``.octop/workflow.json``)."""

WORKFLOW_VERSION = 1
"""The only definition format version this build understands."""

STATUS_DRAFT = "draft"
"""Training: the shape is checked, the content may still be half-written."""

STATUS_ACTIVE = "active"
"""Published: the definition must stand on its own and be runnable as declared."""

WORKFLOW_STATUSES = (STATUS_DRAFT, STATUS_ACTIVE)

CONFIGURABLE_WORKFLOW_KEY = "octop_feature_workflow"
"""``configurable`` entry carrying this turn's :class:`WorkflowRunContext`.

Stamped by the turn path — which can read the workspace asynchronously — so the
middleware that renders the block does no I/O of its own per model call, and so
both the dashboard path and the IM path hand the run the same thing.
"""

FEATURE_RUN_META_KEY = "octop_feature_run"
"""Inbound metadata (and the message's own kwargs) of one submitted run.

``{"inputs": {"customer_name": "ACME"}, "attachments": ["inbound/quote.pdf"]}`` —
what the input card sent. It reaches the harness through the turn's stamped
context and stays on the human message so the conversation can show, read-only,
what that run was given.
"""


@dataclass(frozen=True)
class WorkflowRunContext:
    """Everything one turn's block is rendered from.

    One value rather than five ``configurable`` keys: the definition, the feature's
    name, the run's submitted values and attachments, the caller's overlay and the
    language are only meaningful together — a turn that has half of them would
    render a block that lies about what the run was given.
    """

    definition: Mapping[str, Any]
    name: str = ""
    locale: str = ""
    values: Mapping[str, Any] = field(default_factory=dict)
    attachments: tuple[str, ...] = ()
    overlay: str = ""


MAX_STEPS = 24
MAX_RULES = 20
MAX_INPUT_FIELDS = 40
MAX_OVERLAY_CHARS = 4000
"""How long one caller's own overlay text may be.

Bounded because every run of that caller carries it into the model call: an overlay
is a standing instruction, not a document store, and the definition's own rules
(``MAX_RULE_CHARS`` each) are where a long policy belongs.
"""
MAX_PROMPT_CHARS = 8000
MAX_NAME_CHARS = 120
MAX_RULE_CHARS = 500
MAX_ACCEPT_CHARS = 200

_INPUT_TYPES = ("string", "number", "integer", "boolean", "array", "file")
_ITEM_TYPES = ("string", "number", "integer", "boolean")
_STRING_FORMATS = ("text", "textarea", "date", "email")
_STEP_GATES = ("auto", "confirm")
_OUTPUT_FORMS = ("markdown", "json", "text", "file")

_INPUT_ROOT_KEYS = frozenset({"type", "properties", "required"})
_INPUT_FIELD_KEYS = frozenset(
    {"type", "title", "description", "format", "enum", "items", "accept", "multiple"}
)
_STEP_KEYS = frozenset(
    {"id", "name", "prompt", "depends_on", "skills", "subagents", "tools", "gate"}
)
_OUTPUT_KEYS = frozenset({"name", "form", "path", "description"})
_DOCUMENT_KEYS = frozenset({"version", "status", "inputs", "steps", "outputs", "rules"})

_STEP_ID_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


def slugify_step_id(name: str, *, index: int | None = None) -> str:
    """A step id derived from its name: lowercase, underscores, always legal.

    Derived rather than asked for: an author (or a model writing one of these
    documents) should never have to invent an identifier, and a definition the
    editor produced and one the assistant produced must agree on the same rule.

    A name with no ASCII letters or digits in it — a Chinese step name, which is
    the common case here — carries nothing an id can be built from, so *index*
    (the step's 1-based position) names it instead: ``step_3`` is at least stable
    and unique, where an empty slug would collide with every other such step.
    """
    out: list[str] = []
    for char in name.strip().lower():
        if char.isalnum() and char.isascii():
            out.append(char)
        elif out and out[-1] != "_":
            out.append("_")
    slug = "".join(out).strip("_")[:64]
    if not slug:
        return f"step_{index}" if index is not None else "step"
    if not slug[0].isalpha():
        slug = f"step_{slug}"[:64]
    return slug


def fill_step_ids(definition: Mapping[str, Any]) -> dict[str, Any]:
    """*definition* with an id derived for every step that has none.

    Identifiers are the one part of a step a writer should never have to invent: a
    person drags steps around an editor that names them, and a model asked to fill in
    a document should be thinking about the work, not about ``^[a-z][a-z0-9_]{0,63}$``.
    So a *missing* id is derived from the step's name, uniquely against the ids already
    present.

    Only missing ids are touched. An id that is there but malformed is left exactly as
    written, because rewriting it silently would hide a typo rather than report it —
    validation says what is wrong with it, and its author decides.
    """
    steps = definition.get("steps")
    if not isinstance(steps, list) or not steps:
        return dict(definition)
    used = {
        str(step.get("id"))
        for step in steps
        if isinstance(step, Mapping) and isinstance(step.get("id"), str)
    }
    filled: list[Any] = []
    for position, step in enumerate(steps, start=1):
        if not isinstance(step, Mapping) or isinstance(step.get("id"), str):
            filled.append(step)
            continue
        name = str(step.get("name") or "")
        candidate = slugify_step_id(name, index=position)
        suffix = 2
        while candidate in used:
            candidate = f"{slugify_step_id(name, index=position)[:60]}_{suffix}"
            suffix += 1
        used.add(candidate)
        filled.append({**step, "id": candidate})
    return {**definition, "steps": filled}


def _localized(node: Any, locale: str, fallback: str = "") -> str:
    """One locale's text from a ``{"zh": …, "en": …}`` pair, else the first non-empty."""
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, Mapping):
        value = node.get(locale) or node.get("zh") or node.get("en")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return fallback


def _is_localized_text(node: Any) -> bool:
    """Whether *node* is a bilingual text pair with both keys filled."""
    if not isinstance(node, Mapping):
        return False
    for key in ("zh", "en"):
        value = node.get(key)
        if not isinstance(value, str) or not value.strip():
            return False
    return True


def _problems_in_text(node: Any, where: str, problems: list[str], *, required: bool) -> None:
    if node is None:
        if required:
            problems.append(f"{where} is required")
        return
    if not _is_localized_text(node):
        problems.append(f"{where} must be a non-empty {{'zh': …, 'en': …}} pair")


def _problems_in_input_field(name: str, spec: Any, problems: list[str]) -> None:
    where = f"inputs.properties.{name}"
    if not isinstance(spec, Mapping):
        problems.append(f"{where} must be an object")
        return
    unknown = sorted(set(spec) - _INPUT_FIELD_KEYS)
    if unknown:
        problems.append(f"{where} has unsupported keys: {', '.join(unknown)}")
    field_type = spec.get("type")
    if field_type not in _INPUT_TYPES:
        problems.append(f"{where}.type must be one of {', '.join(_INPUT_TYPES)}")
        return
    _problems_in_text(spec.get("title"), f"{where}.title", problems, required=True)
    _problems_in_text(spec.get("description"), f"{where}.description", problems, required=False)
    if field_type == "string":
        fmt = spec.get("format")
        if fmt is not None and fmt not in _STRING_FORMATS:
            problems.append(f"{where}.format must be one of {', '.join(_STRING_FORMATS)}")
        enum = spec.get("enum")
        if enum is not None and (
            not isinstance(enum, list)
            or not enum
            or any(not isinstance(item, str) or not item.strip() for item in enum)
        ):
            problems.append(f"{where}.enum must be a non-empty array of strings")
    elif spec.get("format") is not None or spec.get("enum") is not None:
        problems.append(f"{where}.format/.enum only apply to a string field")
    if field_type == "array":
        items = spec.get("items")
        if not isinstance(items, Mapping) or items.get("type") not in _ITEM_TYPES:
            problems.append(f"{where}.items.type must be one of {', '.join(_ITEM_TYPES)}")
        elif set(items) - {"type"}:
            problems.append(f"{where}.items supports only 'type'")
    elif spec.get("items") is not None:
        problems.append(f"{where}.items only applies to an array field")
    if field_type == "file":
        accept = spec.get("accept")
        if accept is not None and (not isinstance(accept, str) or len(accept) > MAX_ACCEPT_CHARS):
            problems.append(f"{where}.accept must be a string of at most {MAX_ACCEPT_CHARS} chars")
        if spec.get("multiple") is not None and not isinstance(spec.get("multiple"), bool):
            problems.append(f"{where}.multiple must be a boolean")
    elif spec.get("accept") is not None or spec.get("multiple") is not None:
        problems.append(f"{where}.accept/.multiple only apply to a file field")


def _problems_in_inputs(node: Any, problems: list[str]) -> None:
    if node is None:
        return
    if not isinstance(node, Mapping):
        problems.append("inputs must be an object")
        return
    unknown = sorted(set(node) - _INPUT_ROOT_KEYS)
    if unknown:
        problems.append(f"inputs has unsupported keys: {', '.join(unknown)}")
    if node.get("type") != "object":
        problems.append("inputs.type must be 'object'")
    properties = node.get("properties")
    if not isinstance(properties, Mapping) or not properties:
        problems.append("inputs.properties must be a non-empty object")
        return
    if len(properties) > MAX_INPUT_FIELDS:
        problems.append(f"inputs may declare at most {MAX_INPUT_FIELDS} fields")
    for name, spec in properties.items():
        if not isinstance(name, str) or not name.strip():
            problems.append("inputs.properties keys must be non-empty strings")
            continue
        _problems_in_input_field(name, spec, problems)
    required = node.get("required")
    if required is not None:
        if not isinstance(required, list) or any(not isinstance(item, str) for item in required):
            problems.append("inputs.required must be an array of field names")
        else:
            missing = [item for item in required if item not in properties]
            if missing:
                problems.append(f"inputs.required names unknown fields: {', '.join(missing)}")


def _problems_in_name_list(node: Any, where: str, problems: list[str]) -> None:
    if node is None:
        return
    if not isinstance(node, list) or any(
        not isinstance(item, str) or not item.strip() for item in node
    ):
        problems.append(f"{where} must be an array of non-empty strings")


def _problems_in_steps(node: Any, problems: list[str], *, active: bool) -> None:
    if node is None:
        # No steps declared is a definition, not a mistake: a workflow may ask for
        # inputs and hand back deliverables in one pass, and a *draft* may not have
        # its steps written yet. ``active`` still refuses an empty list, because
        # publishing something that declares a pipeline with nothing in it is not
        # what "active" means.
        return
    if not isinstance(node, list):
        problems.append("steps must be an array")
        return
    if active and not node:
        problems.append("steps must not be empty for an active workflow")
    if len(node) > MAX_STEPS:
        problems.append(f"steps may hold at most {MAX_STEPS} entries")
    seen: dict[str, int] = {}
    # ``depends_on`` is resolved through the ids, so it is only askable once the
    # ids themselves make sense — but "once the ids are fine", not "once the whole
    # document is fine": a missing prompt must not hide a step that waits on one
    # that comes after it, or the second round of the same edit reports a problem
    # the first round could have said already.
    ids_resolvable = True
    for index, step in enumerate(node):
        where = f"steps[{index}]"
        if not isinstance(step, Mapping):
            problems.append(f"{where} must be an object")
            continue
        unknown = sorted(set(step) - _STEP_KEYS)
        if unknown:
            problems.append(f"{where} has unsupported keys: {', '.join(unknown)}")
        raw_id = step.get("id")
        if raw_id is None and active:
            problems.append(f"{where}.id is required for an active workflow")
            ids_resolvable = False
        if raw_id is not None:
            if not isinstance(raw_id, str) or not _STEP_ID_RE.match(raw_id):
                problems.append(f"{where}.id must match ^[a-z][a-z0-9_]{{0,63}}$")
                ids_resolvable = False
            else:
                step_id = raw_id
                if step_id in seen:
                    problems.append(f"{where}.id duplicates steps[{seen[step_id]}].id")
                    ids_resolvable = False
                else:
                    seen[step_id] = index
        name = step.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}.name is required")
        elif len(name) > MAX_NAME_CHARS:
            problems.append(f"{where}.name is longer than {MAX_NAME_CHARS} chars")
        prompt = step.get("prompt")
        if active and (not isinstance(prompt, str) or not prompt.strip()):
            problems.append(f"{where}.prompt is required for an active workflow")
        elif prompt is not None and (not isinstance(prompt, str) or len(prompt) > MAX_PROMPT_CHARS):
            problems.append(f"{where}.prompt must be at most {MAX_PROMPT_CHARS} chars")
        gate = step.get("gate")
        if gate is not None and gate not in _STEP_GATES:
            problems.append(f"{where}.gate must be one of {', '.join(_STEP_GATES)}")
        for key in ("skills", "subagents", "tools", "depends_on"):
            _problems_in_name_list(step.get(key), f"{where}.{key}", problems)
    if ids_resolvable:
        _problems_in_dependencies(node, seen, problems)


def _problems_in_dependencies(
    steps: Sequence[Any], index_of: Mapping[str, int], problems: list[str]
) -> None:
    """``depends_on`` may only name an *earlier* step.

    A forward or self reference is not a step order, it is a cycle the run cannot
    follow — refused here so a model writing the document gets one clear answer
    instead of a run that never reaches the step it claims to wait for.
    """
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        depends_on = step.get("depends_on")
        if not isinstance(depends_on, list):
            continue
        for ref in depends_on:
            if not isinstance(ref, str):
                continue
            target = index_of.get(ref)
            if target is None:
                problems.append(f"steps[{index}].depends_on names unknown step {ref!r}")
            elif target >= index:
                problems.append(f"steps[{index}].depends_on must name an earlier step ({ref!r})")


def _problems_in_outputs(node: Any, problems: list[str]) -> None:
    if node is None:
        return
    if not isinstance(node, list):
        problems.append("outputs must be an array")
        return
    seen: set[str] = set()
    for index, output in enumerate(node):
        where = f"outputs[{index}]"
        if not isinstance(output, Mapping):
            problems.append(f"{where} must be an object")
            continue
        unknown = sorted(set(output) - _OUTPUT_KEYS)
        if unknown:
            problems.append(f"{where} has unsupported keys: {', '.join(unknown)}")
        name = output.get("name")
        if not isinstance(name, str) or not name.strip():
            problems.append(f"{where}.name is required")
        elif name in seen:
            problems.append(f"{where}.name duplicates an earlier output")
        else:
            seen.add(name)
        form = output.get("form")
        if form not in _OUTPUT_FORMS:
            problems.append(f"{where}.form must be one of {', '.join(_OUTPUT_FORMS)}")
        path = output.get("path")
        if path is not None and (not isinstance(path, str) or not path.strip()):
            problems.append(f"{where}.path must be a non-empty string")
        _problems_in_text(
            output.get("description"), f"{where}.description", problems, required=False
        )


def _problems_in_rules(node: Any, problems: list[str]) -> None:
    if node is None:
        return
    if not isinstance(node, list):
        problems.append("rules must be an array")
        return
    if len(node) > MAX_RULES:
        problems.append(f"rules may hold at most {MAX_RULES} entries")
    for index, rule in enumerate(node):
        if not isinstance(rule, str) or not rule.strip():
            problems.append(f"rules[{index}] must be a non-empty string")
        elif len(rule) > MAX_RULE_CHARS:
            problems.append(f"rules[{index}] is longer than {MAX_RULE_CHARS} chars")


def workflow_status(raw: Any) -> str:
    """The status *raw* declares, defaulting to ``draft``.

    Defaulting to ``draft`` is deliberate: a document that does not say it is
    published is treated as work in progress, so a half-written definition can
    never be offered to callers by omission.
    """
    if isinstance(raw, Mapping) and raw.get("status") == STATUS_ACTIVE:
        return STATUS_ACTIVE
    return STATUS_DRAFT


def validate_workflow(raw: Any) -> list[str]:
    """Every problem in *raw*, in the order they appear. Empty list = valid.

    Pure and total: it never raises, and it reports *all* problems at once rather
    than the first — the editor puts them on the section they belong to and a
    model writing a definition gets one answer it can fix in one pass.
    """
    if not isinstance(raw, Mapping):
        return ["definition must be a JSON object"]
    problems: list[str] = []
    if raw.get("version") != WORKFLOW_VERSION:
        problems.append(f"version must be {WORKFLOW_VERSION}")
    if raw.get("status") is not None and raw.get("status") not in WORKFLOW_STATUSES:
        problems.append(f"status must be one of {', '.join(WORKFLOW_STATUSES)}")
    unknown = sorted(set(raw) - _DOCUMENT_KEYS)
    if unknown:
        problems.append(f"definition has unsupported keys: {', '.join(unknown)}")
    _problems_in_inputs(raw.get("inputs"), problems)
    _problems_in_steps(raw.get("steps"), problems, active=workflow_status(raw) == STATUS_ACTIVE)
    _problems_in_outputs(raw.get("outputs"), problems)
    _problems_in_rules(raw.get("rules"), problems)
    return problems


def parse_workflow(raw: Any) -> dict[str, Any]:
    """Validate *raw* and return it as the document this build stores.

    The refusal carries every problem in ``details['reason']`` so the API envelope
    can interpolate them into the localized ``WORKFLOW_INVALID`` sentence with the
    code's own status (400) — the caller sees the list, not "invalid definition".
    """
    problems = validate_workflow(raw)
    if problems:
        reason = "; ".join(problems)
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID,
            reason,
            details={"reason": reason},
        )
    assert isinstance(raw, Mapping)
    return dict(raw)


@dataclass(frozen=True)
class WorkflowLoad:
    """What the workspace held: a definition, or why it could not be read."""

    definition: dict[str, Any] | None = None
    error: str | None = None

    @property
    def present(self) -> bool:
        return self.definition is not None


async def load_workflow(workspace: BackendWorkspace) -> WorkflowLoad:
    """Read ``.octop/workflow.json``.

    Absent is normal — a feature need not declare a workflow — and answers
    ``WorkflowLoad()``. A file that exists but cannot be read as a definition
    answers ``error``: the *run* path logs it and carries on without the block
    (a broken definition must not take the conversation down), while the editor
    surfaces it so its author can fix it.
    """
    text = await workspace.aread_text(WORKSPACE_WORKFLOW_PATH)
    if text is None or not str(text).strip():
        return WorkflowLoad()
    try:
        raw = json.loads(str(text))
    except (json.JSONDecodeError, UnicodeDecodeError, TypeError, ValueError) as exc:
        return WorkflowLoad(error=f"{WORKSPACE_WORKFLOW_PATH} is not valid JSON: {exc}")
    problems = validate_workflow(raw)
    if problems:
        return WorkflowLoad(error="; ".join(problems))
    assert isinstance(raw, Mapping)
    return WorkflowLoad(definition=dict(raw))


async def save_workflow(workspace: BackendWorkspace, raw: Any) -> dict[str, Any]:
    """Validate *raw* and write it as the definition; returns what was stored."""
    definition = parse_workflow(raw)
    payload = json.dumps(definition, ensure_ascii=False, indent=2, sort_keys=False)
    await workspace.awrite_text(WORKSPACE_WORKFLOW_PATH, f"{payload}\n", force=True)
    return definition


async def clear_workflow(workspace: BackendWorkspace) -> None:
    """Remove the definition. A feature without one is a plain conversational agent."""
    await workspace.adelete(WORKSPACE_WORKFLOW_PATH)


@dataclass(frozen=True)
class WorkflowCapabilities:
    """What a feature's agent actually has, as the names a definition may reference."""

    skills: frozenset[str] = frozenset()
    subagents: frozenset[str] = frozenset()


def _available_hint(available: frozenset[str], limit: int = 20) -> str:
    names = sorted(available)
    shown = names[:limit]
    extra = "" if len(names) <= limit else f", +{len(names) - limit} more"
    return ", ".join(shown) + extra


def validate_references(
    definition: Mapping[str, Any],
    capabilities: WorkflowCapabilities,
) -> list[str]:
    """References in *definition* that its agent does not have — with what it has.

    Checked when a definition is **published**, not while it is a draft: a training
    workflow may name a skill the author is about to install, and refusing that would
    make the half-written state unsavable. A published one, though, must be runnable
    as declared — a step naming a skill nobody has is a step that silently does less
    than it says.

    The hint matters as much as the problem: whoever wrote the name (a person, or a
    model asked to fill the form in) can fix it in one pass when the message says what
    exists, instead of guessing again.
    """
    problems: list[str] = []
    steps = definition.get("steps")
    if not isinstance(steps, list):
        return problems
    for index, step in enumerate(steps):
        if not isinstance(step, Mapping):
            continue
        for key, available, what in (
            ("skills", capabilities.skills, "skill"),
            ("subagents", capabilities.subagents, "subagent"),
        ):
            raw = step.get(key)
            if not isinstance(raw, list):
                continue
            missing = [str(name) for name in raw if str(name) not in available]
            if not missing:
                continue
            hint = _available_hint(available)
            problems.append(
                f"steps[{index}].{key} names unknown {what}(s): {', '.join(missing)}"
                + (f" (available: {hint})" if hint else " (this agent has none installed)")
            )
    return problems


async def read_capabilities(workspace: BackendWorkspace) -> WorkflowCapabilities:
    """The skill and subagent names this agent actually has.

    Skills are the directories under the workspace's two discovery roots; subagents
    are the ``agents/*.md`` files it installed. Only what this agent has is offered,
    so a refusal's "available:" list is the truth about *this* feature rather than
    the catalog's — which is exactly what someone fixing a typo needs.
    """
    skills = await _entry_names(workspace, ("skills", f"{DEFAULT_SYSTEM_FILES_PATH}/skills"))
    subagents = await _entry_names(workspace, ("agents",), strip_suffix=".md", dirs_only=False)
    return WorkflowCapabilities(skills=frozenset(skills), subagents=frozenset(subagents))


async def _entry_names(
    workspace: BackendWorkspace,
    roots: Sequence[str],
    *,
    strip_suffix: str = "",
    dirs_only: bool = True,
) -> set[str]:
    """Entry names under whichever of *roots* exist; an unreadable root is empty."""
    names: set[str] = set()
    for root in roots:
        try:
            result = await workspace.als(root)
        except Exception:  # a workspace without that root is normal, not an error
            continue
        for entry in getattr(result, "entries", None) or []:
            raw = entry.get("name") if isinstance(entry, Mapping) else getattr(entry, "name", "")
            if not raw:
                # An ``LsResult`` entry carries ``path`` (``skills/xlsx``) and not a
                # name, so the last segment is the name.
                path = (
                    entry.get("path") if isinstance(entry, Mapping) else getattr(entry, "path", "")
                )
                raw = str(path or "").rstrip("/").rsplit("/", 1)[-1]
            is_dir = (
                entry.get("is_dir")
                if isinstance(entry, Mapping)
                else getattr(entry, "is_dir", False)
            )
            text = str(raw or "").strip()
            if not text or text.startswith("."):
                continue
            if dirs_only and not is_dir:
                continue
            if strip_suffix and text.endswith(strip_suffix):
                text = text[: -len(strip_suffix)]
            if text:
                names.add(text)
    return names


def input_fields(definition: Mapping[str, Any]) -> list[tuple[str, Mapping[str, Any]]]:
    """The form's fields in declaration order (JSON objects keep their key order)."""
    inputs = definition.get("inputs")
    if not isinstance(inputs, Mapping):
        return []
    properties = inputs.get("properties")
    if not isinstance(properties, Mapping):
        return []
    return [(name, spec) for name, spec in properties.items() if isinstance(spec, Mapping)]


def _format_value(value: Any, *, locale: str) -> str:
    if isinstance(value, bool):
        return tr("workflow.value_yes" if value else "workflow.value_no", locale)
    if isinstance(value, list | tuple):
        return "、".join(_format_value(item, locale=locale) for item in value)
    return str(value)


def render_inputs_text(
    definition: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    locale: str | None = None,
) -> str:
    """The submitted values as the model reads them: one labelled line each.

    Labels are the field's own localized ``title``, values are the submitted ones;
    an empty or missing value is omitted rather than rendered as a blank that a
    model could read as "the caller left this blank on purpose" when they simply
    did not fill it in.
    """
    loc = normalize_locale(str(locale or "en"))
    lines: list[str] = []
    for name, spec in input_fields(definition):
        value = values.get(name)
        if value is None or value == "" or value == [] or value == {}:
            continue
        label = _localized(spec.get("title"), loc, name)
        lines.append(f"- {label}: {_format_value(value, locale=loc)}")
    return "\n".join(lines)


def apply_placeholders(text: str, *, inputs_text: str, inputs_json: str) -> str:
    """Replace ``{{inputs}}`` / ``{{inputs_json}}``; leave every other placeholder alone."""
    return text.replace("{{inputs_json}}", inputs_json).replace("{{inputs}}", inputs_text)


def _step_uses(step: Mapping[str, Any], *, locale: str) -> str:
    parts: list[str] = []
    for key, label_key in (
        ("skills", "workflow.uses_skills"),
        ("subagents", "workflow.uses_subagents"),
        ("tools", "workflow.uses_tools"),
    ):
        raw = step.get(key)
        if isinstance(raw, list) and raw:
            parts.append(tr(label_key, locale, names="、".join(str(item) for item in raw)))
    return "；".join(parts)


def render_run_block(
    definition: Mapping[str, Any],
    *,
    locale: str | None = None,
    name: str | None = None,
    values: Mapping[str, Any] | None = None,
    attachments: Sequence[str] = (),
) -> str:
    """The workflow as the run's own system block, in the caller's language.

    Order matters to a reader: what this run was given, then the steps it must
    follow, then the rules that hold throughout, then what to hand back. The block
    states the steps are fixed — a model that "improves" a declared pipeline by
    adding or dropping steps is the failure mode this text exists to prevent.

    *name* is the feature's display name (the agent's), not a field of the
    definition: the two are already one thing, and a second copy inside the
    document could disagree with the row the product shows everywhere else.
    """
    loc = normalize_locale(str(locale or "en"))
    label = str(name or "").strip()
    sections: list[str] = [
        tr("workflow.block.title", loc, name=label)
        if label
        else tr("workflow.block.title_plain", loc),
        tr("workflow.block.lead", loc),
    ]

    inputs_text = render_inputs_text(definition, values or {}, locale=loc)
    attachment_lines = [f"- {path}" for path in attachments if str(path).strip()]
    if inputs_text or attachment_lines:
        section = [tr("workflow.block.inputs_title", loc)]
        if inputs_text:
            section.append(inputs_text)
        if attachment_lines:
            section.append(tr("workflow.block.attachments_title", loc))
            section.extend(attachment_lines)
    else:
        section = [tr("workflow.block.inputs_title", loc), tr("workflow.block.inputs_none", loc)]
    sections.append("\n".join(section))

    steps = definition.get("steps")
    step_lines = [tr("workflow.block.steps_title", loc)]
    if isinstance(steps, list) and steps:
        for index, step in enumerate(steps, start=1):
            if not isinstance(step, Mapping):
                continue
            step_name = str(step.get("name") or step.get("id") or index)
            step_lines.append(
                tr("workflow.block.step_line", loc, index=index, name=step_name, total=len(steps))
            )
            prompt = str(step.get("prompt") or "").strip()
            if prompt:
                rendered = apply_placeholders(prompt, inputs_text=inputs_text, inputs_json="{}")
                step_lines.append(tr("workflow.block.step_prompt", loc, prompt=rendered))
            uses = _step_uses(step, locale=loc)
            if uses:
                step_lines.append(tr("workflow.block.step_uses", loc, uses=uses))
            depends_on = step.get("depends_on")
            if isinstance(depends_on, list) and depends_on:
                step_lines.append(
                    tr(
                        "workflow.block.step_depends",
                        loc,
                        steps="、".join(str(item) for item in depends_on),
                    )
                )
            if step.get("gate") == "confirm":
                step_lines.append(tr("workflow.block.step_gate_confirm", loc))
    else:
        step_lines.append(tr("workflow.block.steps_none", loc))
    sections.append("\n".join(step_lines))

    rules = definition.get("rules")
    if isinstance(rules, list) and rules:
        sections.append(
            "\n".join(
                [tr("workflow.block.rules_title", loc)]
                + [f"- {str(rule).strip()}" for rule in rules if str(rule).strip()]
            )
        )

    outputs = definition.get("outputs")
    if isinstance(outputs, list) and outputs:
        lines = [tr("workflow.block.outputs_title", loc)]
        for output in outputs:
            if not isinstance(output, Mapping):
                continue
            lines.append(
                tr(
                    "workflow.block.output_line",
                    loc,
                    name=str(output.get("name") or ""),
                    form=str(output.get("form") or ""),
                )
            )
        sections.append("\n".join(lines))

    return "\n\n".join(section for section in sections if section)


def render_overlay_block(text: str, *, locale: str | None = None) -> str:
    """The caller's personal overlay, stated to outrank everything above it.

    The precedence is written into the block because it is the whole point of the
    layer: where the overlay and the feature's own wording disagree, the run
    follows the overlay.
    """
    loc = normalize_locale(str(locale or "en"))
    body = text.strip()
    if not body:
        return ""
    return "\n".join([tr("workflow.overlay.title", loc), body])


def render_run_context(context: WorkflowRunContext) -> str:
    """The system block one turn runs under: the workflow, then the caller's overlay.

    The overlay goes *last* and says it wins, which is the ranking this product
    settled on — the feature's steps stay the feature's (an author declares them
    once for everybody), while a caller's own wording outranks what the definition
    says where the two disagree.
    """
    parts = [
        render_run_block(
            context.definition,
            locale=context.locale,
            name=context.name,
            values=context.values,
            attachments=context.attachments,
        ),
        render_overlay_block(context.overlay, locale=context.locale),
    ]
    return "\n\n".join(part for part in parts if part)


__all__ = [
    "CONFIGURABLE_WORKFLOW_KEY",
    "FEATURE_RUN_META_KEY",
    "MAX_OVERLAY_CHARS",
    "MAX_STEPS",
    "STATUS_ACTIVE",
    "STATUS_DRAFT",
    "WORKFLOW_FILENAME",
    "WORKFLOW_STATUSES",
    "WORKFLOW_VERSION",
    "WORKSPACE_WORKFLOW_PATH",
    "WorkflowCapabilities",
    "WorkflowLoad",
    "WorkflowRunContext",
    "apply_placeholders",
    "clear_workflow",
    "fill_step_ids",
    "input_fields",
    "load_workflow",
    "parse_workflow",
    "render_inputs_text",
    "render_overlay_block",
    "render_run_block",
    "render_run_context",
    "save_workflow",
    "slugify_step_id",
    "validate_workflow",
    "workflow_status",
]
