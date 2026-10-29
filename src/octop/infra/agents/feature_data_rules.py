"""Safe schema validation and deterministic computed fields for feature data."""

from __future__ import annotations

import calendar
from collections.abc import Mapping
from datetime import date, datetime, timedelta
from typing import Any

from octop.infra.users.identity import Role

COMPUTED_OPS = frozenset({"add_days", "add_months", "add_years"})
RECIPIENT_KEYS = frozenset(
    {"creator", "owner", "responsible", "user_fields", "unit_fields", "role_keys"}
)


def validate_rule_schema(schema: Mapping[str, Any]) -> dict[str, Any]:
    """Validate computed-field dependencies and recipient declarations.

    Called after the base schema validator has normalized object/field keys and
    field types. The returned schema is a detached shallow copy; no schema is
    modified in place.
    """
    if not isinstance(schema, Mapping) or not isinstance(schema.get("objects"), list):
        raise ValueError("feature data schema must define objects")
    normalized_objects: list[dict[str, Any]] = []
    for obj in schema["objects"]:
        if not isinstance(obj, Mapping):
            raise ValueError("feature data object must be an object")
        fields = obj.get("fields")
        if not isinstance(fields, list):
            raise ValueError("feature data object fields must be a list")
        by_key = {str(field.get("key")): field for field in fields if isinstance(field, Mapping)}
        computed: dict[str, tuple[str, ...]] = {}
        for field in fields:
            if not isinstance(field, Mapping):
                continue
            rule = field.get("computed")
            if rule is None:
                continue
            key = str(field.get("key"))
            if field.get("type") not in {"date", "datetime"}:
                raise ValueError(f"{key}.computed requires a date or datetime result field")
            if not isinstance(rule, Mapping) or rule.get("op") not in COMPUTED_OPS:
                raise ValueError(f"{key}.computed has an unsupported operation")
            source = rule.get("source_field")
            if not isinstance(source, str) or source not in by_key:
                raise ValueError(f"{key}.computed source_field must name an existing field")
            if by_key[source].get("type") not in {"date", "datetime"}:
                raise ValueError(f"{key}.computed source_field must be a date or datetime")
            has_amount_field = "amount_field" in rule
            has_amount = "amount" in rule
            if has_amount_field == has_amount:
                raise ValueError(f"{key}.computed requires exactly one of amount_field or amount")
            if has_amount_field:
                amount_field = rule["amount_field"]
                if not isinstance(amount_field, str) or amount_field not in by_key:
                    raise ValueError(f"{key}.computed amount_field must name an existing field")
                if by_key[amount_field].get("type") != "integer":
                    raise ValueError(f"{key}.computed amount_field must be integer")
                computed[key] = (source, amount_field)
            else:
                if type(rule["amount"]) is not int:
                    raise ValueError(f"{key}.computed amount must be an integer")
                computed[key] = (source,)
        _validate_computed_graph(computed)
        recipient_policy = obj.get("reminder_recipients")
        if recipient_policy is not None:
            if not isinstance(recipient_policy, Mapping):
                raise ValueError("reminder_recipients must be an object")
            extra = set(recipient_policy) - RECIPIENT_KEYS
            if extra:
                raise ValueError(f"unsupported reminder recipient options: {sorted(extra)}")
            for flag in ("creator", "owner", "responsible"):
                if flag in recipient_policy and type(recipient_policy[flag]) is not bool:
                    raise ValueError(f"reminder_recipients.{flag} must be boolean")
            for list_key in ("user_fields", "unit_fields", "role_keys"):
                values = recipient_policy.get(list_key, [])
                if not isinstance(values, list) or any(
                    not isinstance(value, str) or not value for value in values
                ):
                    raise ValueError(
                        f"reminder_recipients.{list_key} must be a list of non-empty strings"
                    )
                if list_key == "role_keys":
                    allowed_roles = {role.value for role in Role}
                    invalid_roles = set(values) - allowed_roles
                    if invalid_roles:
                        raise ValueError(
                            "reminder_recipients.role_keys must use supported roles: "
                            f"{sorted(allowed_roles)}"
                        )
                if list_key == "user_fields":
                    for field_key in values:
                        if field_key not in by_key or by_key[field_key].get("type") != "user":
                            raise ValueError(
                                f"reminder recipient field {field_key!r} must be a user field"
                            )
                if list_key == "unit_fields":
                    for field_key in values:
                        if field_key not in by_key or by_key[field_key].get("type") != "org_unit":
                            raise ValueError(
                                f"reminder recipient field {field_key!r} must be an org_unit field"
                            )
                if len(values) != len(set(values)):
                    raise ValueError(f"reminder_recipients.{list_key} contains duplicates")
        normalized_objects.append({**dict(obj), "fields": [dict(field) for field in fields]})
    return {**dict(schema), "objects": normalized_objects}


def _validate_computed_graph(dependencies: Mapping[str, tuple[str, ...]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visiting:
            raise ValueError("computed fields contain a dependency cycle")
        if key in visited:
            return
        visiting.add(key)
        for dependency in dependencies.get(key, ()):
            if dependency in dependencies:
                visit(dependency)
        visiting.remove(key)
        visited.add(key)

    for field_key in dependencies:
        visit(field_key)


def validate_rule_value(
    field: Mapping[str, Any],
    value: Any,
    *,
    user_repo: Any | None = None,
    unit_repo: Any | None = None,
) -> Any:
    """Normalize values for added user and organization-unit field types.

    Supplying repositories also enforces current existence and account/unit
    state; callers validating at a write boundary should supply both.
    """
    field_type = field.get("type")
    if field_type == "user":
        if value is None:
            return None
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field.get('key')} must be a positive user id")
        if user_repo is not None:
            user = user_repo.get(value)
            if user is None or int(getattr(user, "disabled", 0)):
                raise ValueError(f"{field.get('key')} must reference an active user")
        return value
    if field_type == "org_unit":
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field.get('key')} must be a non-empty organization unit key")
        normalized = value.strip()
        if unit_repo is not None and unit_repo.get(normalized) is None:
            raise ValueError(f"{field.get('key')} must reference an existing organization unit")
        return normalized
    return value


def apply_computed_fields(
    schema: Mapping[str, Any],
    values: Mapping[str, Any],
    *,
    object_key: str | None = None,
    reject_supplied: bool = True,
) -> dict[str, Any]:
    """Recompute date fields without evaluating schema-provided code.

    ``values`` should already contain validated editable values. Missing inputs
    clear derived values. A client-supplied computed field is rejected by default.
    """
    result = dict(values)
    objects = schema.get("objects", [])
    selected = [obj for obj in objects if object_key is None or obj.get("key") == object_key]
    if object_key is not None and not selected:
        raise ValueError(f"unknown feature data object {object_key!r}")
    if object_key is None and len(objects) > 1:
        raise ValueError("object_key is required for a multi-object schema")
    for obj in selected:
        fields = obj.get("fields", [])
        rules = {
            str(field["key"]): field["computed"]
            for field in fields
            if isinstance(field, Mapping) and field.get("computed")
        }
        for key in rules:
            if reject_supplied and key in values:
                raise ValueError(f"computed field {key!r} cannot be supplied")
            result.pop(key, None)
        for key in _computed_order(rules):
            rule = rules[key]
            source_value = result.get(rule["source_field"])
            amount = result.get(rule["amount_field"]) if "amount_field" in rule else rule["amount"]
            if source_value is None or amount is None:
                continue
            if type(amount) is not int:
                raise ValueError(f"computed field {key!r} amount must be an integer")
            if abs(amount) > 36500:
                raise ValueError(f"computed field {key!r} amount is out of range")
            result[key] = _calculate_date(source_value, rule["op"], amount, key)
    return result


def _computed_order(rules: Mapping[str, Mapping[str, Any]]) -> list[str]:
    order: list[str] = []
    visited: set[str] = set()

    def visit(key: str) -> None:
        if key in visited:
            return
        visited.add(key)
        source = rules[key]["source_field"]
        if source in rules:
            visit(source)
        order.append(key)

    for key in rules:
        visit(key)
    return order


def _calculate_date(value: Any, op: str, amount: int, field_key: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"computed field {field_key!r} source must be an ISO date string")
    try:
        parsed_date = date.fromisoformat(value) if len(value) == 10 else None
        parsed_datetime = (
            datetime.fromisoformat(value.replace("Z", "+00:00")) if parsed_date is None else None
        )
    except ValueError as exc:
        raise ValueError(f"computed field {field_key!r} source is not a valid date") from exc
    source: date | datetime = parsed_date if parsed_date is not None else parsed_datetime  # type: ignore[assignment]
    if source is None:
        raise ValueError(f"computed field {field_key!r} source is not a valid date")
    if op == "add_days":
        target = source + timedelta(days=amount)
    elif op in {"add_months", "add_years"}:
        months = amount if op == "add_months" else amount * 12
        month_index = source.year * 12 + source.month - 1 + months
        year, month0 = divmod(month_index, 12)
        month = month0 + 1
        target = source.replace(
            year=year, month=month, day=min(source.day, calendar.monthrange(year, month)[1])
        )
    else:
        raise ValueError(f"unsupported computed date operation {op!r}")
    return target.isoformat()


__all__ = ["apply_computed_fields", "validate_rule_schema", "validate_rule_value"]
