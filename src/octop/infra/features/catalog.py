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
"""

from __future__ import annotations

import json
import logging
import re
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
given, so callers order them newest-approved first.
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


class FeatureCatalog:
    """Loads feature definitions from ``<root>/<id>/feature.json``.

    The library is read once on first access (:meth:`list`, :meth:`get`,
    :meth:`warnings`) and cached; call :meth:`reload` after adding or editing a
    definition on disk.
    """

    def __init__(self, root: Path | None = None) -> None:
        self._root = Path(root) if root is not None else default_library_root()
        self._features: dict[str, Feature] = {}
        self._warnings: list[str] = []
        self._loaded = False

    @property
    def root(self) -> Path:
        """Library root this catalog scans."""
        return self._root

    def list(self) -> list[Feature]:
        """Every valid feature, sorted by ``unit`` then label (zh, en, id)."""
        self._ensure_loaded()
        return sorted(self._features.values(), key=_sort_key)

    def get(self, feature_id: str) -> Feature | None:
        """Return one feature, or ``None`` when unknown (or skipped as invalid)."""
        self._ensure_loaded()
        return self._features.get(feature_id)

    def reload(self) -> None:
        """Re-scan the library root from disk, replacing the cached catalog."""
        features: dict[str, Feature] = {}
        warnings: list[str] = []
        if self._root.is_dir():
            for entry in sorted(self._root.iterdir()):
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
                features[feature.id] = feature
        else:
            warnings.append(f"library root not found: {self._root}")
            logger.warning("feature library root %s not found", self._root)

        self._features = features
        self._warnings = warnings
        self._loaded = True
        logger.info(
            "feature catalog loaded: %d features (%d skipped)",
            len(features),
            len(warnings),
        )

    def warnings(self) -> list[str]:
        """Skipped definitions with their reasons (empty when the library is clean)."""
        self._ensure_loaded()
        return list(self._warnings)

    def _ensure_loaded(self) -> None:
        if not self._loaded:
            self.reload()


def default_library_root() -> Path:
    """Return the in-package feature library directory."""
    return Path(__file__).parent / "library"


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
        ),
        None,
    )


def build_user_prompt(
    feature: Feature,
    inputs: dict[str, Any],
    *,
    rules: list[str] | None = None,
) -> str:
    """Render ``prompt.user_template`` with *inputs*.

    ``{{inputs}}`` becomes a human-readable ``label: value`` list — fields follow
    ``ui_schema.order`` (then the declaration order, then any extra input keys) and
    each label uses the Chinese title when that value contains CJK characters and
    the English title otherwise. ``{{inputs_json}}`` becomes the raw inputs as
    JSON. Unknown ``{{placeholder}}`` tokens are left untouched.

    *rules* are the approved, human-reviewed requirements distilled from past
    corrections (M4 self-improvement). They are appended as their own labeled
    paragraph so the model cannot mistake them for the user's current input; blank
    entries are dropped and at most :data:`MAX_INJECTED_RULES` are used, so callers
    order them newest-approved first. ``None`` (the default) injects nothing and
    the rendered template is returned verbatim.
    """
    rendered = {
        "inputs": _render_inputs_text(feature, inputs),
        "inputs_json": json.dumps(inputs, ensure_ascii=False, indent=2),
    }
    prompt = _PLACEHOLDER_RE.sub(
        lambda match: rendered.get(match.group(1), match.group(0)),
        feature.user_template,
    )
    injected = [text for rule in rules or [] if (text := str(rule).strip())]
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


def _render_rules_block(rules: list[str], *, chinese: bool) -> str:
    """One labeled paragraph of approved rules — heading plus numbered lines."""
    heading = _RULES_HEADING_ZH if chinese else _RULES_HEADING_EN
    lines = [heading]
    lines.extend(f"{index}. {text}" for index, text in enumerate(rules, start=1))
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
