"""Feature catalog — enterprise feature directory declared on disk.

A *feature* is metadata in ``library/<id>/feature.json`` plus an optional
``PROMPT.md`` system prompt in the same directory. Definitions travel with the
repository, so an enterprise deployment can add or tailor a feature by dropping
a directory in the library root — no database row, no migration.

Definitions are discovered lazily by :class:`FeatureCatalog`. Loading is
tolerant: a manifest that fails :func:`~octop.infra.features.schema.validate_manifest`
(or that points at a missing system prompt) is skipped and recorded in
:meth:`FeatureCatalog.warnings`, so a single bad directory never takes the
feature directory offline.

Besides the bundled library, a catalog can scan writable overlay roots
(``~/.octop/features/``) — :mod:`octop.infra.features.store` is what writes them.
An overlay wins on an id collision, so an edited feature stays edited.
"""

from __future__ import annotations

import builtins
import json
import logging
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octop.infra.features.schema import validate_manifest

logger = logging.getLogger(__name__)

MANIFEST_FILENAME = "feature.json"
"""Feature definition filename — one directory per feature under the library root."""

MAX_INJECTED_RULES = 10
"""How many approved rules one prompt may carry.

Rules accumulate as people correct drafts, so the block is capped: an unbounded
list would crowd out the current inputs and let stale rules bury fresh ones.
:func:`build_user_prompt` keeps the first ``MAX_INJECTED_RULES`` entries it is
given, so callers order them narrowest layer first (personal, then the caller's
unit — see :func:`octop.infra.features.rules.injectable_rule_rows`).
"""

_CJK_RE = re.compile(r"[\u3400-\u9fff]")
_PLACEHOLDER_RE = re.compile(r"\{\{(\w+)\}\}")
_ZH_SEPARATOR = "："
_EN_SEPARATOR = ": "

_RULES_HEADING_ZH = (
    "以下要求来自历史修正记录归纳（人工审核通过），不是本次输入的一部分，请一并遵守："
)
_RULES_HEADING_EN = (
    "The following requirements were distilled from past corrections "
    "(human-reviewed and approved). They are NOT part of the current input:"
)
"""Wording that keeps approved rules from reading as part of the user's input."""

_SCOPE_TAGS: dict[str, tuple[str, str]] = {
    "personal": ("【个人规则】", "【Personal rule】"),
    "unit": ("【部门规则】", "【Department rule】"),
    "global": ("【全局规则】", "【Global rule】"),
}
"""Source-layer tags keyed by ``feature_rules.scope`` (schema v23).

The model has to be able to tell a colleague's personal preference from a
requirement the whole organization agreed on, so every rule says where it came
from. A layer this table does not know renders untagged rather than guessed at.
"""


@dataclass(frozen=True)
class ScopedRule:
    """One rule to inject, plus the layer it came from.

    ``scope`` is a ``feature_rules.scope`` value (``personal`` | ``unit`` |
    ``global``); ``None`` renders the rule without a tag, which is what a caller
    that only has texts gets.
    """

    text: str
    scope: str | None = None


@dataclass(frozen=True)
class FeatureAgent:
    """Capability layer one feature declares for its own agent (design 5.1/5.2).

    ``None`` on a field means "inherit the caller's agent"; a tuple is a scope,
    and an empty tuple is the explicit "none of them". ``tools_disabled`` is the
    one field with no per-caller dimension — it is the same for every run.
    """

    model: str | None = None
    temperature: float | None = None
    top_p: float | None = None
    max_tokens: int | None = None
    max_iters: int | None = None
    max_input_length: int | None = None
    tools_disabled: tuple[str, ...] | None = None
    skills: tuple[str, ...] | None = None
    subagents: tuple[str, ...] | None = None
    mcp_servers: tuple[str, ...] | None = None
    knowledge_base_ids: tuple[str, ...] | None = None

    @classmethod
    def from_dict(cls, node: Mapping[str, Any]) -> FeatureAgent:
        """Read one validated ``agent`` node (see :func:`validate_manifest`)."""
        return cls(
            model=_optional_str(node.get("model")),
            temperature=_optional_float(node.get("temperature")),
            top_p=_optional_float(node.get("top_p")),
            max_tokens=_optional_int(node.get("max_tokens")),
            max_iters=_optional_int(node.get("max_iters")),
            max_input_length=_optional_int(node.get("max_input_length")),
            tools_disabled=_optional_names(node.get("tools_disabled")),
            skills=_optional_names(node.get("skills")),
            subagents=_optional_names(node.get("subagents")),
            mcp_servers=_optional_names(node.get("mcp_servers")),
            knowledge_base_ids=_optional_names(node.get("knowledge_base_ids")),
        )

    def as_dict(self) -> dict[str, Any]:
        """The node exactly as declared — ``None`` fields omitted, lists as lists.

        This is what the settings UI reads back, so a key the author never set
        must not appear as ``null`` and read as a deliberate choice.
        """
        out: dict[str, Any] = {}
        for key in (
            "model",
            "temperature",
            "top_p",
            "max_tokens",
            "max_iters",
            "max_input_length",
        ):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        for key in ("tools_disabled", "skills", "subagents", "mcp_servers", "knowledge_base_ids"):
            value = getattr(self, key)
            if value is not None:
                out[key] = list(value)
        return out

    def runtime_values(self) -> dict[str, Any]:
        """Sampling / budget knobs, in the shape the agent runtime helpers consume."""
        out: dict[str, Any] = {}
        for key in ("temperature", "top_p", "max_tokens", "max_iters", "max_input_length"):
            value = getattr(self, key)
            if value is not None:
                out[key] = value
        return out


def _optional_str(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def _optional_float(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _optional_int(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value


def _optional_names(value: Any) -> tuple[str, ...] | None:
    """A declared scope: ``None`` inherits, a list is the scope (possibly empty)."""
    if not isinstance(value, list):
        return None
    return tuple(str(item) for item in value)


@dataclass(frozen=True)
class Feature:
    """One enterprise feature definition (immutable, as loaded from disk)."""

    id: str
    version: int
    label: dict[str, str]
    description: dict[str, str]
    icon_name: str
    color: str | None
    unit: str
    input_schema: dict[str, Any]
    ui_schema: dict[str, Any]
    user_template: str
    system_prompt: str | None
    output_kind: str
    permissions: dict[str, Any]
    agent: FeatureAgent | None = None


class FeatureCatalog:
    """Loads feature definitions from ``<root>/<id>/feature.json``.

    The library is read once on first access (:meth:`list`, :meth:`get`,
    :meth:`warnings`) and cached; call :meth:`reload` after adding or editing a
    definition on disk.

    ``extra_roots`` are writable overlays — normally ``~/.octop/features/``, the
    directory the settings UI writes to. A definition found in an extra root
    **wins over the bundled one carrying the same id**: an edit somebody made
    must survive a restart, whereas the bundled copy would silently restore the
    shipped wording on every boot. An overlay that fails validation is skipped
    like any other bad definition, leaving the bundled one in place.
    """

    def __init__(self, root: Path | None = None, extra_roots: list[Path] | None = None) -> None:
        self._root = Path(root) if root is not None else default_library_root()
        self._extra_roots = [Path(extra) for extra in extra_roots or []]
        self._features: dict[str, Feature] = {}
        self._feature_dirs: dict[str, Path] = {}
        self._bundled_ids: set[str] = set()
        self._warnings: list[str] = []
        self._loaded = False

    @property
    def root(self) -> Path:
        """Library root this catalog scans."""
        return self._root

    @property
    def roots(self) -> tuple[Path, ...]:
        """Every scanned root, bundled library first — later roots override."""
        return (self._root, *self._extra_roots)

    def list(self) -> list[Feature]:
        """Every valid feature, sorted by ``unit`` then label (zh, en, id)."""
        self._ensure_loaded()
        return sorted(self._features.values(), key=_sort_key)

    def get(self, feature_id: str) -> Feature | None:
        """Return one feature, or ``None`` when unknown (or skipped as invalid)."""
        self._ensure_loaded()
        return self._features.get(feature_id)

    def feature_dir(self, feature_id: str) -> Path | None:
        """Directory the effective definition of *feature_id* was loaded from."""
        self._ensure_loaded()
        return self._feature_dirs.get(feature_id)

    def is_bundled(self, feature_id: str) -> bool:
        """Whether *feature_id* is served from the read-only bundled library."""
        self._ensure_loaded()
        return feature_id in self._bundled_ids

    def reload(self) -> None:
        """Re-scan every root from disk, replacing the cached catalog."""
        features: dict[str, Feature] = {}
        feature_dirs: dict[str, Path] = {}
        bundled_ids: set[str] = set()
        warnings: list[str] = []
        for root in self.roots:
            if not root.is_dir():
                continue
            from_bundled = _same_path(root, self._root)
            for entry in sorted(root.iterdir()):
                if not entry.is_dir():
                    continue
                manifest_path = entry / MANIFEST_FILENAME
                if not manifest_path.is_file():
                    continue
                feature, reason = _load_feature(entry, manifest_path)
                if feature is None:
                    reason = reason or "invalid definition"
                    warnings.append(f"{entry.name}/{MANIFEST_FILENAME}: {reason}")
                    logger.warning("feature %s skipped: %s", entry.name, reason)
                    continue
                # A later root overrides: the overlay is the edited version.
                features[feature.id] = feature
                feature_dirs[feature.id] = entry
                if from_bundled:
                    bundled_ids.add(feature.id)
                else:
                    bundled_ids.discard(feature.id)
        if not self._root.is_dir():
            warnings.append(f"library root not found: {self._root}")
            logger.warning("feature library root %s not found", self._root)

        self._features = features
        self._feature_dirs = feature_dirs
        self._bundled_ids = bundled_ids
        self._warnings = warnings
        self._loaded = True
        logger.info(
            "feature catalog loaded: %d features (%d skipped, %d bundled)",
            len(features),
            len(warnings),
            len(bundled_ids),
        )

    def warnings(self) -> builtins.list[str]:
        """Skipped definitions with their reasons (empty when the library is clean)."""
        self._ensure_loaded()
        return list(self._warnings)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()


def default_library_root() -> Path:
    """Return the in-package feature library directory."""
    return Path(__file__).parent / "library"


def _same_path(left: Path, right: Path) -> bool:
    """Whether two roots point at the same directory (symlinks/case included)."""
    try:
        return left.resolve() == right.resolve()
    except OSError:
        return left == right


def _sort_key(feature: Feature) -> tuple[str, str, str, str]:
    return (
        feature.unit,
        feature.label.get("zh") or "",
        feature.label.get("en") or "",
        feature.id,
    )


def _load_feature(feature_dir: Path, manifest_path: Path) -> tuple[Feature | None, str | None]:
    """Parse and validate one definition; ``(None, reason)`` when it must be skipped."""
    try:
        raw = json.loads(manifest_path.read_text(encoding="utf-8"))
    except OSError as exc:
        return None, f"unreadable manifest: {exc}"
    except ValueError as exc:
        return None, f"invalid JSON: {exc}"
    if not isinstance(raw, dict):
        return None, "manifest must be a JSON object"

    errors = validate_manifest(raw, feature_dir.name)
    if errors:
        return None, "; ".join(errors)

    prompt = raw["prompt"]
    system_prompt: str | None = None
    system_file = prompt.get("system_file")
    if isinstance(system_file, str):
        try:
            system_prompt = (feature_dir / system_file).read_text(encoding="utf-8")
        except OSError as exc:
            return None, f"prompt.system_file {system_file!r} unreadable: {exc}"

    return (
        Feature(
            id=raw["id"],
            version=int(raw["version"]),
            label=dict(raw["label"]),
            description=dict(raw["description"]),
            icon_name=raw["icon_name"],
            color=raw.get("color"),
            unit=raw["unit"],
            input_schema=raw["input_schema"],
            ui_schema=raw.get("ui_schema") or {},
            user_template=prompt["user_template"],
            system_prompt=system_prompt,
            output_kind=raw["output"]["kind"],
            permissions=raw.get("permissions") or {},
            agent=(
                FeatureAgent.from_dict(raw["agent"]) if isinstance(raw.get("agent"), dict) else None
            ),
        ),
        None,
    )


def build_user_prompt(
    feature: Feature,
    inputs: dict[str, Any],
    *,
    rules: Sequence[ScopedRule] | None = None,
) -> str:
    """Render ``prompt.user_template`` with *inputs*.

    ``{{inputs}}`` becomes a human-readable ``label: value`` list — fields follow
    ``ui_schema.order`` (then the declaration order, then any extra input keys) and
    each label uses the Chinese title when that value contains CJK characters and
    the English title otherwise. ``{{inputs_json}}`` becomes the raw inputs as
    JSON. Unknown ``{{placeholder}}`` tokens are left untouched.

    *rules* are the approved, human-reviewed requirements distilled from past
    corrections (M4 self-improvement), each with the scope layer it came from. They
    are appended as their own labeled paragraph so the model cannot mistake them
    for the user's current input; blank entries are dropped and at most
    :data:`MAX_INJECTED_RULES` are used, so callers order them narrowest layer
    first. A rule that names its layer is tagged with it, which is how the model
    tells a personal preference from an org-wide requirement. ``None`` (the
    default) injects nothing and the rendered template is returned verbatim.
    """
    rendered = {
        "inputs": _render_inputs_text(feature, inputs),
        "inputs_json": json.dumps(inputs, ensure_ascii=False, indent=2),
    }
    prompt = _PLACEHOLDER_RE.sub(
        lambda match: rendered.get(match.group(1), match.group(0)),
        feature.user_template,
    )
    injected = [rule for rule in rules or [] if str(rule.text).strip()]
    if not injected:
        return prompt
    block = _render_rules_block(injected[:MAX_INJECTED_RULES], chinese=_looks_chinese(prompt))
    return f"{prompt}\n\n{block}"


def _render_inputs_text(feature: Feature, inputs: dict[str, Any]) -> str:
    """Human-readable input summary; empty or missing values are omitted."""
    lines: list[str] = []
    for name in _ordered_field_names(feature, inputs):
        value = inputs.get(name)
        if _is_blank(value):
            continue
        label, separator = _field_label(feature, name, value)
        if isinstance(value, list):
            rows = "\n".join(f"  - {_format_cell(row)}" for row in value)
            lines.append(f"- {label}{separator}\n{rows}")
        else:
            lines.append(f"- {label}{separator}{_format_cell(value)}")
    return "\n".join(lines)


def _render_rules_block(rules: Sequence[ScopedRule], *, chinese: bool) -> str:
    """One labeled paragraph of approved rules — heading plus numbered lines.

    Each line carries the layer its rule came from (``【个人规则】…``), so the
    heading itself can stay about provenance and authority rather than about
    scope. A rule without a named layer is numbered like the others.
    """
    heading = _RULES_HEADING_ZH if chinese else _RULES_HEADING_EN
    lines = [heading]
    for index, rule in enumerate(rules, start=1):
        tag = _SCOPE_TAGS.get(str(rule.scope), ("", ""))[0 if chinese else 1]
        lines.append(f"{index}. {tag}{str(rule.text).strip()}")
    return "\n".join(lines)


def _ordered_field_names(feature: Feature, inputs: dict[str, Any]) -> list[str]:
    """``ui_schema.order`` first, then declared fields, then extra input keys."""
    properties = feature.input_schema.get("properties")
    declared = [str(name) for name in properties] if isinstance(properties, dict) else []
    order = feature.ui_schema.get("order")
    ordered = [str(name) for name in order] if isinstance(order, list) else []

    names: list[str] = []
    for name in ordered:
        if name in declared and name not in names:
            names.append(name)
    for name in declared:
        if name not in names:
            names.append(name)
    for name in inputs:
        if name not in names:
            names.append(str(name))
    return names


def _field_label(feature: Feature, name: str, value: Any) -> tuple[str, str]:
    """Pick the label and separator for one field from its bilingual ``title``."""
    chinese = _looks_chinese(_probe_text(value))
    separator = _ZH_SEPARATOR if chinese else _EN_SEPARATOR
    properties = feature.input_schema.get("properties")
    schema = properties.get(name) if isinstance(properties, dict) else None
    title = schema.get("title") if isinstance(schema, dict) else None
    if isinstance(title, dict):
        preferred = title.get("zh") if chinese else title.get("en")
        fallback = title.get("en") if chinese else title.get("zh")
        for candidate in (preferred, fallback):
            if isinstance(candidate, str) and candidate.strip():
                return candidate.strip(), separator
    return name, separator


def _probe_text(value: Any) -> str:
    """Text used to language-detect a value (lists are probed cell by cell)."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        return " ".join(_probe_text(item) for item in value)
    return ""


def _looks_chinese(text: str) -> bool:
    return bool(_CJK_RE.search(text))


def _is_blank(value: Any) -> bool:
    """``False`` and ``0`` are real answers; only missing/empty values are blank."""
    if value is None:
        return True
    if isinstance(value, str):
        return not value.strip()
    if isinstance(value, (list, dict)):
        return not value
    return False


def _format_cell(value: Any) -> str:
    """Flatten one value (or one grid row) into a single line of text."""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return " | ".join(_format_cell(item) for item in value)
    return json.dumps(value, ensure_ascii=False)
