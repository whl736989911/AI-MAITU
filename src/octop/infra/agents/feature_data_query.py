"""Structured AND-filtering and sorting for feature data record lists.

The HTTP list endpoint and the ``feature_data_list`` tool both parse their
query here, so both surfaces validate identically: every condition names a
declared schema field (or one of three system columns), the operator fits the
field's type, and values are bound as SQL parameters. User input never
reaches the statement as SQL text — the compiler emits ``?`` placeholders and
a parameter list, and the JSON path of each field is built from keys the
schema validator already restricted to ``^[a-z][a-z0-9_]{0,63}$``.

The emitted SQL runs unchanged on SQLite and PostgreSQL: JSON access goes
through ``json_extract`` on SQLite and a ``::jsonb ->>`` cast on PostgreSQL,
with numeric comparisons cast to ``numeric`` on PostgreSQL where the text
extraction would otherwise compare lexicographically.
"""

from __future__ import annotations

import json
import math
from collections.abc import Mapping
from datetime import date, datetime
from typing import Any

from octop.infra.errors import ErrorCode, OctopError

MAX_FILTERS = 20
MAX_IN_VALUES = 50
SYSTEM_SORT_KEYS = ("created_at", "updated_at", "title")
SORT_ORDERS = ("asc", "desc")

_FILTER_OPS = frozenset({"eq", "ne", "contains", "gt", "gte", "lt", "lte", "in", "is_empty"})
_TEXT_OPS = frozenset({"eq", "ne", "contains", "in", "is_empty"})
_NUMBER_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "in", "is_empty"})
_DATE_OPS = frozenset({"eq", "ne", "gt", "gte", "lt", "lte", "is_empty"})
_IDENTITY_OPS = frozenset({"eq", "ne", "in", "is_empty"})
_OPS_BY_TYPE = {
    "text": _TEXT_OPS,
    "textarea": _TEXT_OPS,
    "enum": _TEXT_OPS,
    "number": _NUMBER_OPS,
    "integer": _NUMBER_OPS,
    "boolean": frozenset({"eq", "ne", "is_empty"}),
    "date": _DATE_OPS,
    "datetime": _DATE_OPS,
    "record_ref": _IDENTITY_OPS,
    "user": _IDENTITY_OPS,
    "org_unit": _TEXT_OPS,
    "knowledge_ref": frozenset({"is_empty"}),
    "file": frozenset({"is_empty"}),
}


class FilterError(ValueError):
    """A filter or sort request the schema does not allow.

    Raised as a ``ValueError`` so both callers map it onto their existing
    invalid-request error (the router wraps it in ``WORKFLOW_INVALID``).
    """


def parse_filters(raw: Any, obj: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Validate a filters payload against *obj*'s declared fields.

    Accepts the JSON string the HTTP query carries or an already-parsed list
    (the tool path). Returns a normalized condition list of
    ``{"field", "op", "value"}`` dicts in request order — AND semantics.
    """
    if raw is None or raw == "" or raw == []:
        return []
    if isinstance(raw, str):
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FilterError(f"filters is not valid JSON: {exc}") from exc
    else:
        parsed = raw
    if not isinstance(parsed, list):
        raise FilterError("filters must be a JSON array")
    if len(parsed) > MAX_FILTERS:
        raise FilterError(f"filters may contain at most {MAX_FILTERS} conditions")
    fields = {str(field["key"]): field for field in obj.get("fields", [])}
    conditions: list[dict[str, Any]] = []
    for index, item in enumerate(parsed):
        if not isinstance(item, Mapping):
            raise FilterError(f"filters[{index}] must be an object")
        field_key = str(item.get("field") or "")
        op = str(item.get("op") or "")
        if op not in _FILTER_OPS:
            raise FilterError(f"filters[{index}].op {op!r} is not supported")
        if field_key in SYSTEM_SORT_KEYS:
            allowed = _TEXT_OPS if field_key == "title" else _NUMBER_OPS
            if op not in allowed:
                raise FilterError(f"filters[{index}].op {op!r} does not apply to {field_key}")
            field_type = "text" if field_key == "title" else "integer"
            value = _validated_value(
                {"key": field_key, "type": field_type},
                op,
                item.get("value"),
                f"filters[{index}].value",
            )
            conditions.append(
                {
                    "field": field_key,
                    "op": op,
                    "value": value,
                    "type": field_type,
                }
            )
            continue
        field = fields.get(field_key)
        if field is None:
            raise FilterError(f"filters[{index}].field {field_key!r} is not a declared field")
        if op not in _OPS_BY_TYPE.get(str(field.get("type")), frozenset()):
            raise FilterError(
                f"filters[{index}].op {op!r} does not apply to {field_key} of type {field.get('type')!r}"
            )
        value = _validated_value(field, op, item.get("value"), f"filters[{index}].value")
        conditions.append(
            {"field": field_key, "op": op, "value": value, "type": str(field.get("type"))}
        )
    return conditions


def parse_sort(
    obj: Mapping[str, Any], sort_by: str | None, sort_order: str | None
) -> tuple[str, str]:
    """Validate ``sort_by``/``sort_order``; defaults mirror the legacy listing."""
    order = (sort_order or "desc").strip().lower()
    if order not in SORT_ORDERS:
        raise FilterError(f"sort_order must be one of {SORT_ORDERS}")
    key = (sort_by or "").strip()
    if not key:
        return "updated_at", order
    if key in SYSTEM_SORT_KEYS:
        return key, order
    for field in obj.get("fields", []):
        if str(field.get("key")) == key:
            return key, order
    raise FilterError(f"sort_by {key!r} is not a declared field or system column")


def _validated_value(field: Mapping[str, Any], op: str, value: Any, where: str) -> Any:
    if op == "is_empty":
        return None
    field_type = str(field.get("type"))
    if op == "in":
        if not isinstance(value, list) or not value:
            raise FilterError(f"{where} must be a non-empty array for op 'in'")
        if len(value) > MAX_IN_VALUES:
            raise FilterError(f"{where} may contain at most {MAX_IN_VALUES} values")
        return [_validated_scalar(field, field_type, item, where) for item in value]
    return _validated_scalar(field, field_type, value, where)


def _validated_scalar(field: Mapping[str, Any], field_type: str, value: Any, where: str) -> Any:
    key = str(field.get("key"))
    if field_type in {"number", "integer"}:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise FilterError(f"{where} must be a number for {key}")
        if isinstance(value, float) and not math.isfinite(value):
            raise FilterError(f"{where} must be a finite number for {key}")
        return value
    if field_type == "boolean":
        if type(value) is not bool:
            raise FilterError(f"{where} must be true or false for {key}")
        return value
    if field_type in {"date", "datetime"}:
        if not isinstance(value, str) or not value:
            raise FilterError(f"{where} must be an ISO date string for {key}")
        try:
            if field_type == "date":
                if len(value) != 10:
                    raise ValueError("date must use YYYY-MM-DD")
                return date.fromisoformat(value).isoformat()
            datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            expected = "YYYY-MM-DD" if field_type == "date" else "an ISO datetime string"
            raise FilterError(f"{where} must be {expected} for {key}") from exc
        return value
    if field_type == "enum":
        options = field.get("options") or []
        if value not in options:
            raise FilterError(f"{where} must be one of {options} for {key}")
        return value
    if field_type == "user":
        if type(value) is not int or value <= 0:
            raise FilterError(f"{where} must be a positive user id for {key}")
        return value
    if field_type in {"text", "textarea", "record_ref", "org_unit"}:
        if not isinstance(value, str) or not value:
            raise FilterError(f"{where} must be a non-empty string for {key}")
        return value
    raise FilterError(f"op not supported for {key} of type {field_type!r}")


def _value_expr(field_key: str, field_type: str, dialect: str) -> str:
    """The SQL expression that yields one record field's comparable value."""
    if dialect == "postgresql":
        extracted = f"(values_json::jsonb->>'{field_key}')"
        if field_type in {"number", "integer"}:
            return (
                f"(CASE WHEN jsonb_typeof(values_json::jsonb->'{field_key}') = 'number' "
                f"THEN ({extracted})::numeric END)"
            )
        return extracted
    extracted = f"json_extract(values_json, '$.{field_key}')"
    if field_type in {"number", "integer"}:
        return (
            f"(CASE WHEN json_type(values_json, '$.{field_key}') IN ('integer', 'real') "
            f"THEN {extracted} END)"
        )
    return extracted


def compile_filters(
    conditions: list[dict[str, Any]],
    obj: Mapping[str, Any],
    *,
    dialect: str,
) -> tuple[list[str], list[Any]]:
    r"""Compile parsed conditions into WHERE fragments and bound parameters.

    Every fragment is a conjunction; the caller ANDs them with its own
    visibility clause. String containment escapes ``%``/``_``/``\`` so a
    filter value can never widen into a pattern.
    """
    fragments: list[str] = []
    params: list[Any] = []
    for condition in conditions:
        key = condition["field"]
        op = condition["op"]
        value = condition["value"]
        field_type = condition["type"]
        expr = key if key in SYSTEM_SORT_KEYS else _value_expr(key, field_type, dialect)
        if op == "is_empty":
            fragments.append(f"({expr} IS NULL OR {expr} = '')")
            continue
        if op == "in":
            marks = ", ".join("?" for _ in value)
            fragments.append(f"{expr} IN ({marks})")
            params.extend(_param(field_type, item, dialect) for item in value)
            continue
        if op == "contains":
            needle = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            fragments.append(f"{expr} LIKE ? ESCAPE '\\'")
            params.append(f"%{needle}%")
            continue
        operator = {"eq": "=", "ne": "!=", "gt": ">", "gte": ">=", "lt": "<", "lte": "<="}[op]
        fragments.append(f"{expr} {operator} ?")
        params.append(_param(field_type, value, dialect))
    return fragments, params


def _param(field_type: str, value: Any, dialect: str) -> Any:
    if field_type == "boolean":
        if dialect == "postgresql":
            return "true" if value else "false"
        return 1 if value else 0
    return value


def compile_order(
    sort_by: str,
    sort_order: str,
    obj: Mapping[str, Any],
    *,
    dialect: str,
) -> str:
    """ORDER BY for one validated sort key.

    Nulls sort last regardless of direction and ``id`` breaks ties, so equal
    keys keep a stable order across pages. The null probe runs before the
    direction-dependent expression, which keeps SQLite's default NULL-first
    ordering and PostgreSQL's NULL-first-on-DESC from resurfacing.
    """
    if sort_by in SYSTEM_SORT_KEYS:
        expr = sort_by
    else:
        field = next(field for field in obj.get("fields", []) if str(field.get("key")) == sort_by)
        expr = _value_expr(sort_by, str(field.get("type")), dialect)
    direction = "ASC" if sort_order == "asc" else "DESC"
    return f"({expr}) IS NULL ASC, {expr} {direction}, id {direction}"


def filters_from_request(
    raw_filters: Any,
    sort_by: str | None,
    sort_order: str | None,
    obj: Mapping[str, Any],
    *,
    dialect: str,
) -> tuple[list[str], list[Any], str]:
    """One call for the HTTP/tool callers: WHERE fragments, params, ORDER BY."""
    conditions = parse_filters(raw_filters, obj)
    key, order = parse_sort(obj, sort_by, sort_order)
    fragments, params = compile_filters(conditions, obj, dialect=dialect)
    return fragments, params, compile_order(key, order, obj, dialect=dialect)


def filters_error(exc: Exception) -> OctopError:
    """Map a filter/sort refusal onto the existing invalid-workflow error."""
    return OctopError(ErrorCode.WORKFLOW_INVALID, str(exc))


__all__ = [
    "MAX_FILTERS",
    "MAX_IN_VALUES",
    "FilterError",
    "compile_filters",
    "compile_order",
    "filters_error",
    "filters_from_request",
    "parse_filters",
    "parse_sort",
]
