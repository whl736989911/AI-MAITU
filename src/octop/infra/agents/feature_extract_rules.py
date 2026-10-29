"""Configurable extraction rules: which drawing text fills which data field.

A feature's ledger schema says *what* is recorded; these rules say *where each
value comes from*. They live next to the schema in the feature's workspace
(``feature-data/extract-rules.json``), so a customer can point a field at their
own title-block labels without touching code, and an agent can rewrite them
after the user agrees.

Version 2 adds **profiles**: one feature may hold several drawing layouts
(forging process cards, mould drawings, assembly drawings), each with its own
field mapping. A profile is selected per drawing by ``when`` — the labels found
in the drawing's text and/or its file name — and the first match wins. A rules
document without profiles is read as version 1 and treated as one default
profile, so an existing file keeps working.

Shape::

    {
      "version": 2,
      "profiles": [
        {
          "name": {"zh": "锻造工艺卡", "en": "Forging card"},
          "when": {"labels_any": ["锻造工艺卡", "下料重量"], "filename_pattern": "工艺卡"},
          "fields": {
            "drawing_no": {"labels": ["图号", "产品图号"]},
            "max_od_mm":  {"from": "dimensions", "pick": "max_outer"}
          }
        }
      ]
    }
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from octop.infra.agents.workspace_dir import system_dir_relpaths

RULES_LEAF = "feature-data/extract-rules.json"
RULES_VERSION = 2
MAX_PROFILES = 20
MAX_RULES = 40
MAX_LABELS = 20
MAX_PATTERN_CHARS = 300

_RULE_KEYS = frozenset({"labels", "pattern", "unit", "from", "pick", "limit"})
_PROFILE_KEYS = frozenset({"name", "when", "fields"})
_WHEN_KEYS = frozenset({"labels_any", "filename_pattern"})
_SOURCES = frozenset({"label", "dimensions"})
_PICKS = frozenset({"max_outer", "min_inner", "text"})
_UNITS = frozenset({"kg"})


@dataclass(frozen=True)
class RulesLoad:
    rules: dict[str, Any] | None = None
    error: str | None = None


def _rules_path() -> str:
    return system_dir_relpaths(RULES_LEAF)[0]


def _validate_fields(
    fields: Mapping[str, Any], declared: set[str] | None, where: str
) -> dict[str, dict[str, Any]]:
    if not fields:
        raise ValueError(f"{where} must define at least one field")
    if len(fields) > MAX_RULES:
        raise ValueError(f"{where} may hold at most {MAX_RULES} fields")
    normalized: dict[str, dict[str, Any]] = {}
    for key, rule in fields.items():
        field_key = str(key or "").strip()
        if not re.fullmatch(r"^[a-z][a-z0-9_]{0,63}$", field_key):
            raise ValueError(f"field {field_key!r} must match ^[a-z][a-z0-9_]{{0,63}}$")
        if declared is not None and field_key not in declared:
            raise ValueError(f"field {field_key!r} is not declared in the data schema")
        if not isinstance(rule, Mapping):
            raise ValueError(f"field {field_key!r} rule must be an object")
        unknown = set(rule) - _RULE_KEYS
        if unknown:
            raise ValueError(f"field {field_key!r} has unsupported keys: {sorted(unknown)}")

        source = str(rule.get("from") or "label")
        if source not in _SOURCES:
            raise ValueError(f"field {field_key!r}.from must be one of {sorted(_SOURCES)}")

        entry: dict[str, Any] = {}
        labels = rule.get("labels")
        if labels is not None:
            if not isinstance(labels, list) or not labels:
                raise ValueError(f"field {field_key!r}.labels must be a non-empty array")
            if len(labels) > MAX_LABELS:
                raise ValueError(f"field {field_key!r} declares too many labels")
            cleaned = []
            for label in labels:
                text = str(label or "").strip()
                if not text:
                    raise ValueError(f"field {field_key!r}.labels must not contain blanks")
                cleaned.append(text)
            entry["labels"] = cleaned
        pattern = rule.get("pattern")
        if pattern is not None:
            text = str(pattern)
            if len(text) > MAX_PATTERN_CHARS:
                raise ValueError(f"field {field_key!r}.pattern is too long")
            try:
                re.compile(text)
            except re.error as exc:
                raise ValueError(f"field {field_key!r}.pattern is invalid: {exc}") from exc
            entry["pattern"] = text
        unit = rule.get("unit")
        if unit is not None:
            if str(unit) not in _UNITS:
                raise ValueError(f"field {field_key!r}.unit must be one of {sorted(_UNITS)}")
            entry["unit"] = str(unit)
        if source == "dimensions":
            entry["from"] = "dimensions"
            pick = rule.get("pick")
            if pick is not None:
                if str(pick) not in _PICKS:
                    raise ValueError(f"field {field_key!r}.pick must be one of {sorted(_PICKS)}")
                entry["pick"] = str(pick)
            limit = rule.get("limit")
            if limit is not None:
                if type(limit) is not int or limit < 1 or limit > 50:
                    raise ValueError(f"field {field_key!r}.limit must be 1..50")
                entry["limit"] = limit
        if (
            not entry.get("labels")
            and not entry.get("pattern")
            and entry.get("from") != "dimensions"
        ):
            raise ValueError(f"field {field_key!r} needs labels, a pattern, or from=dimensions")
        normalized[field_key] = entry
    return normalized


def _validate_when(when: Any, where: str) -> dict[str, Any]:
    if when is None:
        return {}
    if not isinstance(when, Mapping):
        raise ValueError(f"{where}.when must be an object")
    unknown = set(when) - _WHEN_KEYS
    if unknown:
        raise ValueError(f"{where}.when has unsupported keys: {sorted(unknown)}")
    entry: dict[str, Any] = {}
    labels = when.get("labels_any")
    if labels is not None:
        if not isinstance(labels, list) or not labels:
            raise ValueError(f"{where}.when.labels_any must be a non-empty array")
        cleaned = [str(item or "").strip() for item in labels]
        if any(not item for item in cleaned):
            raise ValueError(f"{where}.when.labels_any must not contain blanks")
        entry["labels_any"] = cleaned
    pattern = when.get("filename_pattern")
    if pattern is not None:
        text = str(pattern)
        if len(text) > MAX_PATTERN_CHARS:
            raise ValueError(f"{where}.when.filename_pattern is too long")
        try:
            re.compile(text)
        except re.error as exc:
            raise ValueError(f"{where}.when.filename_pattern is invalid: {exc}") from exc
        entry["filename_pattern"] = text
    return entry


def validate_rules(raw: Any, *, schema: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Normalize a rules document; raise ``ValueError`` when it cannot run.

    Both versions are accepted: a flat ``fields`` document (v1) becomes one
    default profile, and a ``profiles`` document (v2) is validated per profile.
    *schema*, when given, requires every rule key to name a declared field.
    """
    if not isinstance(raw, Mapping):
        raise ValueError("extraction rules must be an object")

    declared: set[str] | None = None
    if schema is not None:
        declared = {
            str(field.get("key"))
            for obj in schema.get("objects", [])
            if isinstance(obj, Mapping)
            for field in obj.get("fields", [])
            if isinstance(field, Mapping)
        }

    if "profiles" in raw:
        version = raw.get("version", RULES_VERSION)
        if version != RULES_VERSION:
            raise ValueError(f"extraction rules version must be {RULES_VERSION}")
        profiles_raw = raw.get("profiles")
        if not isinstance(profiles_raw, list) or not profiles_raw:
            raise ValueError("extraction rules must define at least one profile")
        if len(profiles_raw) > MAX_PROFILES:
            raise ValueError(f"extraction rules may hold at most {MAX_PROFILES} profiles")
        profiles: list[dict[str, Any]] = []
        seen: set[str] = set()
        for index, item in enumerate(profiles_raw):
            where = f"profiles[{index}]"
            if not isinstance(item, Mapping):
                raise ValueError(f"{where} must be an object")
            unknown = set(item) - _PROFILE_KEYS
            if unknown:
                raise ValueError(f"{where} has unsupported keys: {sorted(unknown)}")
            name = item.get("name")
            if not isinstance(name, (str, Mapping)) or not name:
                raise ValueError(f"{where}.name must be a non-empty string or locale object")
            key = name if isinstance(name, str) else json.dumps(name, sort_keys=True)
            if key in seen:
                raise ValueError(f"{where}.name duplicates an earlier profile")
            seen.add(key)
            fields = item.get("fields")
            if not isinstance(fields, Mapping):
                raise ValueError(f"{where}.fields must be an object")
            profiles.append(
                {
                    "name": name,
                    "when": _validate_when(item.get("when"), where),
                    "fields": _validate_fields(fields, declared, f"{where}.fields"),
                }
            )
        return {"version": RULES_VERSION, "profiles": profiles}

    version = raw.get("version", 1)
    if version != 1:
        raise ValueError("extraction rules version must be 1 or 2")
    fields = raw.get("fields")
    if not isinstance(fields, Mapping):
        raise ValueError("extraction rules must define fields or profiles")
    return {
        "version": RULES_VERSION,
        "profiles": [
            {
                "name": "default",
                "when": {},
                "fields": _validate_fields(fields, declared, "fields"),
            }
        ],
    }


def profile_label(profile: Mapping[str, Any], locale: str = "zh") -> str:
    name = profile.get("name")
    if isinstance(name, str):
        return name
    if isinstance(name, Mapping):
        for key in (locale, "zh", "en"):
            value = name.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return "default"


def select_profile(
    rules: Mapping[str, Any],
    *,
    filename: str,
    texts: Sequence[str],
) -> dict[str, Any]:
    """The first profile whose ``when`` matches this drawing, else the default.

    ``labels_any`` matches when any listed label appears in the drawing's text;
    ``filename_pattern`` is a case-insensitive regex over the file name. A
    profile with no ``when`` always matches, which is how the legacy single
    mapping keeps applying to every drawing.
    """
    profiles = [item for item in rules.get("profiles", []) if isinstance(item, Mapping)]
    if not profiles:
        return {"name": "default", "when": {}, "fields": {}}
    haystack = "\n".join(texts)
    for profile in profiles:
        when = profile.get("when") or {}
        if not isinstance(when, Mapping):
            continue
        labels = [str(item) for item in when.get("labels_any") or []]
        pattern = when.get("filename_pattern")
        label_hit = any(label and label in haystack for label in labels)
        name_hit = bool(pattern) and re.search(str(pattern), filename, re.IGNORECASE) is not None
        if labels and pattern:
            if label_hit and name_hit:
                return dict(profile)
            continue
        if labels:
            if label_hit:
                return dict(profile)
            continue
        if pattern:
            if name_hit:
                return dict(profile)
            continue
        return dict(profile)
    return dict(profiles[-1])


async def load_rules(workspace: Any) -> RulesLoad:
    for rel in system_dir_relpaths(RULES_LEAF):
        try:
            text = await workspace.aread_text(rel)
        except Exception:
            continue
        if text is None or not str(text).strip():
            continue
        try:
            return RulesLoad(rules=validate_rules(json.loads(str(text))))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return RulesLoad(error=f"{rel} is invalid: {exc}")
    return RulesLoad()


async def save_rules(
    workspace: Any, raw: Any, *, schema: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    rules = validate_rules(raw, schema=schema)
    payload = json.dumps(rules, ensure_ascii=False, indent=2)
    await workspace.awrite_text(_rules_path(), payload + "\n", force=True)
    return rules


__all__ = [
    "RULES_LEAF",
    "RULES_VERSION",
    "RulesLoad",
    "load_rules",
    "profile_label",
    "save_rules",
    "select_profile",
    "validate_rules",
]
