"""Per-knowledge-base document metadata schemas and values.

A metadata schema is a small, declarative list of fields; each document may
carry values for those fields. The schema lives on the knowledge base and the
values live on the document, so a later schema edit never rewrites a document
row. Values are validated here and filtered in the service layer, which keeps
the capability transport-neutral and easy to reuse (drawings, contracts,
certificates, equipment, …) without a dedicated business module.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from datetime import date
from decimal import Decimal, InvalidOperation
from typing import Any

FIELD_TYPES = frozenset({"text", "number", "date", "enum"})
_MAX_FIELDS = 50
_MAX_OPTIONS = 100
_MAX_TEXT = 2000
_KEY_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]{0,63}$")


class MetadataError(ValueError):
    """A schema or value payload that callers must see as invalid input."""


def normalize_schema(raw: Any) -> list[dict[str, Any]]:
    """Validate and canonicalize a metadata schema."""
    if raw is None:
        return []
    if not isinstance(raw, list):
        raise MetadataError("metadata schema must be a list of fields")
    if len(raw) > _MAX_FIELDS:
        raise MetadataError(f"metadata schema supports at most {_MAX_FIELDS} fields")
    fields: list[dict[str, Any]] = []
    seen: set[str] = set()
    for index, item in enumerate(raw):
        if not isinstance(item, Mapping):
            raise MetadataError(f"metadata field {index} must be an object")
        key = str(item.get("key") or "").strip()
        if not _KEY_RE.fullmatch(key):
            raise MetadataError(f"metadata field {index} key must match {_KEY_RE.pattern}")
        if key in seen:
            raise MetadataError(f"duplicate metadata field key {key!r}")
        seen.add(key)
        field_type = str(item.get("type") or "text").strip()
        if field_type not in FIELD_TYPES:
            raise MetadataError(f"metadata field {key!r} type must be one of {sorted(FIELD_TYPES)}")
        label = str(item.get("label") or key).strip()
        if not label or len(label) > 100:
            raise MetadataError(f"metadata field {key!r} label must be 1..100 characters")
        options: list[str] = []
        if field_type == "enum":
            raw_options = item.get("options")
            if not isinstance(raw_options, list) or not raw_options:
                raise MetadataError(f"metadata field {key!r} enum requires options")
            if len(raw_options) > _MAX_OPTIONS:
                raise MetadataError(
                    f"metadata field {key!r} supports at most {_MAX_OPTIONS} options"
                )
            for option in raw_options:
                value = str(option).strip()
                if not value or len(value) > 100:
                    raise MetadataError(f"metadata field {key!r} options must be 1..100 characters")
                if value not in options:
                    options.append(value)
        fields.append(
            {
                "key": key,
                "label": label,
                "type": field_type,
                "options": options,
                "required": bool(item.get("required", False)),
                "searchable": bool(item.get("searchable", True)),
            }
        )
    return fields


def normalize_values(
    schema: list[dict[str, Any]], raw: Any, *, existing: Mapping[str, Any] | None = None
) -> dict[str, Any]:
    """Validate values against *schema*, merging over *existing* values.

    A missing key keeps the stored value. ``None``, ``""`` and empty lists clear
    it. Unknown keys are rejected rather than silently stored, so a typo in an
    import cannot look like a saved field.
    """
    if raw is None:
        raw = {}
    if not isinstance(raw, Mapping):
        raise MetadataError("metadata values must be an object")
    by_key = {str(field["key"]): field for field in schema}
    unknown = sorted(str(key) for key in raw if str(key) not in by_key)
    if unknown:
        raise MetadataError(f"unknown metadata field(s): {', '.join(unknown)}")
    values: dict[str, Any] = dict(existing or {})
    for key, value in raw.items():
        field = by_key[str(key)]
        if value is None or value == "" or value == []:
            values.pop(key, None)
            continue
        values[str(key)] = _validate_value(field, value)
    missing = [
        field["key"] for field in schema if field.get("required") and field["key"] not in values
    ]
    if missing:
        raise MetadataError(f"required metadata field(s) missing: {', '.join(missing)}")
    return {key: values[key] for key in by_key if key in values}


def _validate_value(field: Mapping[str, Any], value: Any) -> Any:
    key = str(field["key"])
    field_type = str(field["type"])
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise MetadataError(f"metadata field {key!r} expects a number")
        try:
            finite = math.isfinite(float(value))
        except OverflowError:
            finite = False
        if not finite:
            raise MetadataError(f"metadata field {key!r} expects a finite number")
        return value
    if field_type == "date":
        text = str(value).strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
            raise MetadataError(f"metadata field {key!r} expects an ISO date YYYY-MM-DD")
        try:
            date.fromisoformat(text)
        except ValueError as exc:
            raise MetadataError(f"metadata field {key!r} expects a real calendar date") from exc
        return text
    text = str(value).strip()
    if not text or len(text) > _MAX_TEXT:
        raise MetadataError(f"metadata field {key!r} expects 1..{_MAX_TEXT} characters")
    if field_type == "enum" and text not in field.get("options", ()):
        raise MetadataError(f"metadata field {key!r} is not one of its options")
    return text


def metadata_matches(values: Mapping[str, Any], filters: Mapping[str, Any]) -> bool:
    """Whether *values* satisfy every equality/contains filter."""
    for key, expected in filters.items():
        actual = values.get(key)
        if actual is None:
            return False
        if isinstance(expected, bool):
            return False
        if isinstance(expected, (int, float)):
            if isinstance(actual, bool) or not isinstance(actual, (int, float)):
                return False
            # Compare exactly: float() loses low bits above 2**53 and would
            # report two different large integers as the same value.
            if actual != expected:
                return False
            continue
        needle = str(expected).strip().lower()
        if not needle:
            continue
        if needle not in str(actual).strip().lower():
            return False
    return True


def validate_suggested_values(schema: list[dict[str, Any]], raw: Any) -> dict[str, Any]:
    """Keep only known, well-formed values from a model suggestion.

    Suggestions are advisory: unknown keys and invalid values are dropped instead
    of failing the enrichment, and required fields are not enforced here because
    a human may still add them before applying.
    """
    if not isinstance(raw, Mapping):
        return {}
    by_key = {str(field["key"]): field for field in schema}
    out: dict[str, Any] = {}
    for key, value in raw.items():
        field = by_key.get(str(key))
        if field is None or value is None or value == "" or value == []:
            continue
        if field.get("type") == "number" and isinstance(value, str):
            # Parse integer-valued suggestions without routing them through a
            # binary float, which would round large but valid integers.
            stripped = value.strip()
            if not re.fullmatch(r"[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?", stripped):
                continue
            try:
                number = Decimal(stripped)
                if not number.is_finite() or not math.isfinite(float(number)):
                    continue
                value = int(number) if number == number.to_integral_value() else float(number)
            except (InvalidOperation, OverflowError, ValueError):
                continue
        try:
            out[str(key)] = _validate_value(field, value)
        except MetadataError:
            continue
    return out


__all__ = [
    "FIELD_TYPES",
    "MetadataError",
    "metadata_matches",
    "normalize_schema",
    "normalize_values",
    "validate_suggested_values",
]
