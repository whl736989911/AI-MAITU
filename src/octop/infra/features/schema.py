"""Feature manifest validation — ``library/<id>/feature.json``.

The dashboard renders the run form straight from ``input_schema``, so only the
documented JSON Schema subset is accepted (see the M1 contract §2). Problems are
reported as plain strings: :class:`~octop.infra.features.catalog.FeatureCatalog`
skips a bad definition with a warning instead of failing the whole catalog.

:func:`feature_json_schema` describes the manifest format itself (draft 2020-12)
for editors and offline tooling.
"""

from __future__ import annotations

from collections.abc import Iterable
from copy import deepcopy
from typing import Any

# 定义格式版本；只有结构被真正改动时才递增。
SUPPORTED_VERSIONS: tuple[int, ...] = (1,)

# §2 允许的 input_schema 子集：根必须是 object，字段只能是这些类型。
ALLOWED_FIELD_TYPES: tuple[str, ...] = ("string", "number", "integer", "boolean", "array")
ALLOWED_FORMATS: tuple[str, ...] = ("textarea", "date", "email")
ALLOWED_OUTPUT_KINDS: tuple[str, ...] = ("markdown", "json", "text")

_LOCALES: tuple[str, ...] = ("zh", "en")
_ROOT_SCHEMA_KEYS = frozenset({"type", "properties", "required", "title", "description"})
_FIELD_SCHEMA_KEYS = frozenset({"type", "title", "description", "format", "enum", "items"})
_UI_SCHEMA_KEYS = frozenset({"order", "widgets"})


def validate_manifest(data: dict[str, Any], dir_name: str | None = None) -> list[str]:
    """Return the problems found in one ``feature.json`` payload (empty = valid).

    *dir_name* is the containing directory name when the caller knows it; passing
    it enables the contract check that ``id`` equals the directory name.
    """
    errors: list[str] = []
    if not isinstance(data, dict):
        return ["manifest must be a JSON object"]

    feature_id = data.get("id")
    if not isinstance(feature_id, str) or not feature_id.strip():
        errors.append("id must be a non-empty string")
    elif dir_name is not None and feature_id != dir_name:
        errors.append(f"id {feature_id!r} must match the directory name {dir_name!r}")

    version = data.get("version")
    if not isinstance(version, int) or isinstance(version, bool):
        errors.append("version must be an integer")
    elif version not in SUPPORTED_VERSIONS:
        errors.append(
            f"version {version} is not supported (expected {_quoted(SUPPORTED_VERSIONS)})"
        )

    for field in ("label", "description"):
        if field not in data:
            errors.append(f"{field} is required")
        else:
            _check_localized(data[field], field, errors)

    icon_name = data.get("icon_name")
    if not isinstance(icon_name, str) or not icon_name.strip():
        errors.append("icon_name must be a non-empty string")

    if "color" in data:
        color = data["color"]
        if not isinstance(color, str) or not color.strip():
            errors.append("color must be a non-empty string when present")

    unit = data.get("unit")
    if not isinstance(unit, str) or not unit.strip():
        errors.append("unit must be a non-empty string")

    field_names: set[str] = set()
    if "input_schema" not in data:
        errors.append("input_schema is required")
    else:
        _check_input_schema(data["input_schema"], errors)
        schema = data["input_schema"]
        properties = schema.get("properties") if isinstance(schema, dict) else None
        if isinstance(properties, dict):
            field_names = {str(name) for name in properties}

    if "ui_schema" in data:
        _check_ui_schema(data["ui_schema"], field_names, errors)

    _check_prompt(data.get("prompt"), errors)
    _check_output(data.get("output"), errors)

    if "permissions" in data:
        _check_permissions(data["permissions"], errors)

    return errors


def _quoted(values: Iterable[object]) -> str:
    return ", ".join(f"'{value}'" for value in values)


def _check_localized(node: Any, field: str, errors: list[str]) -> None:
    """Bilingual text must be an object carrying non-empty ``zh`` and ``en``."""
    if not isinstance(node, dict):
        errors.append(f"{field} must be an object with 'zh' and 'en' keys")
        return
    for locale in _LOCALES:
        value = node.get(locale)
        if not isinstance(value, str) or not value.strip():
            errors.append(f"{field}.{locale} must be a non-empty string")


def _check_input_schema(node: Any, errors: list[str]) -> None:
    """Validate the root ``input_schema`` node (an object with ``properties``)."""
    if not isinstance(node, dict):
        errors.append("input_schema must be an object")
        return
    unknown = sorted(set(node) - _ROOT_SCHEMA_KEYS)
    if unknown:
        errors.append(f"input_schema uses unsupported keys: {_quoted(unknown)}")
    if node.get("type") != "object":
        errors.append("input_schema.type must be 'object'")
    if "title" in node:
        _check_localized(node["title"], "input_schema.title", errors)
    if "description" in node:
        _check_localized(node["description"], "input_schema.description", errors)

    properties = node.get("properties")
    if not isinstance(properties, dict) or not properties:
        errors.append("input_schema.properties must be a non-empty object of field schemas")
        properties = None
    else:
        for name, sub in properties.items():
            _check_field_schema(sub, f"input_schema.properties.{name}", errors)

    if "required" in node:
        required = node["required"]
        if not isinstance(required, list) or not all(isinstance(name, str) for name in required):
            errors.append("input_schema.required must be an array of strings")
        elif properties is not None:
            missing = sorted({name for name in required if name not in properties})
            if missing:
                errors.append(
                    f"input_schema.required references unknown fields: {_quoted(missing)}"
                )


def _check_field_schema(node: Any, field: str, errors: list[str]) -> None:
    """Validate one non-root field: ``object`` is not allowed below the root."""
    if not isinstance(node, dict):
        errors.append(f"{field} must be an object")
        return
    unknown = sorted(set(node) - _FIELD_SCHEMA_KEYS)
    if unknown:
        errors.append(f"{field} uses unsupported keys: {_quoted(unknown)}")

    node_type = node.get("type")
    if node_type not in ALLOWED_FIELD_TYPES:
        errors.append(f"{field}.type must be one of {_quoted(ALLOWED_FIELD_TYPES)}")
    if "title" in node:
        _check_localized(node["title"], f"{field}.title", errors)
    if "description" in node:
        _check_localized(node["description"], f"{field}.description", errors)
    if "format" in node and node["format"] not in ALLOWED_FORMATS:
        errors.append(f"{field}.format must be one of {_quoted(ALLOWED_FORMATS)}")
    if "enum" in node:
        enum = node["enum"]
        if not isinstance(enum, list) or not enum or not all(isinstance(v, str) for v in enum):
            errors.append(f"{field}.enum must be a non-empty array of strings")

    if node_type == "array":
        if "items" not in node:
            errors.append(f"{field}.items is required when type is 'array'")
        else:
            _check_field_schema(node["items"], f"{field}.items", errors)
    elif "items" in node:
        errors.append(f"{field}.items is only allowed when type is 'array'")


def _check_ui_schema(node: Any, field_names: set[str], errors: list[str]) -> None:
    """Validate ``ui_schema.order`` / ``ui_schema.widgets`` against real fields."""
    if not isinstance(node, dict):
        errors.append("ui_schema must be an object")
        return
    unknown = sorted(set(node) - _UI_SCHEMA_KEYS)
    if unknown:
        errors.append(f"ui_schema uses unsupported keys: {_quoted(unknown)}")

    order = node.get("order")
    if order is not None:
        if not isinstance(order, list) or not all(
            isinstance(name, str) and name.strip() for name in order
        ):
            errors.append("ui_schema.order must be an array of field names")
        else:
            missing = sorted({name for name in order if name not in field_names})
            if missing:
                errors.append(f"ui_schema.order references unknown fields: {_quoted(missing)}")

    widgets = node.get("widgets")
    if widgets is not None:
        well_formed = isinstance(widgets, dict) and all(
            isinstance(name, str) and name.strip() and isinstance(widget, str) and widget.strip()
            for name, widget in widgets.items()
        )
        if not well_formed:
            errors.append("ui_schema.widgets must map field names to widget names")
        else:
            missing = sorted({name for name in widgets if name not in field_names})
            if missing:
                errors.append(f"ui_schema.widgets references unknown fields: {_quoted(missing)}")


def _check_prompt(node: Any, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append("prompt must be an object")
        return
    template = node.get("user_template")
    if not isinstance(template, str) or not template.strip():
        errors.append("prompt.user_template must be a non-empty string")

    system_file = node.get("system_file")
    if system_file is not None:
        if not isinstance(system_file, str) or not system_file.strip():
            errors.append("prompt.system_file must be a non-empty string when present")
        elif system_file.startswith(("/", "\\")) or ".." in system_file:
            errors.append("prompt.system_file must be a relative path inside the feature directory")


def _check_output(node: Any, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append("output must be an object")
        return
    if node.get("kind") not in ALLOWED_OUTPUT_KINDS:
        errors.append(f"output.kind must be one of {_quoted(ALLOWED_OUTPUT_KINDS)}")


def _check_permissions(node: Any, errors: list[str]) -> None:
    if not isinstance(node, dict):
        errors.append("permissions must be an object")
        return
    for key in ("allow_units", "allow_roles"):
        if key not in node:
            continue
        value = node[key]
        if not isinstance(value, list) or not all(
            isinstance(entry, str) and entry.strip() for entry in value
        ):
            errors.append(f"permissions.{key} must be an array of strings")


_FEATURE_JSON_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "title": "MAITU Smart Manufacturing feature definition (feature.json)",
    "description": (
        "One enterprise feature under the feature library root "
        "(library/<id>/feature.json). The run form is rendered from input_schema, "
        "which only accepts the documented keyword subset."
    ),
    "type": "object",
    "required": [
        "id",
        "version",
        "label",
        "description",
        "icon_name",
        "unit",
        "input_schema",
        "prompt",
        "output",
    ],
    "properties": {
        "id": {
            "type": "string",
            "minLength": 1,
            "description": "Feature id; must equal the containing directory name.",
        },
        "version": {
            "type": "integer",
            "enum": list(SUPPORTED_VERSIONS),
            "description": "Definition format version.",
        },
        "label": {"$ref": "#/$defs/localized"},
        "description": {"$ref": "#/$defs/localized"},
        "icon_name": {
            "type": "string",
            "minLength": 1,
            "description": "lucide-react icon name (for example 'file-text').",
        },
        "color": {
            "type": "string",
            "minLength": 1,
            "description": "Optional accent colour; the dashboard falls back to the brand colour.",
        },
        "unit": {
            "type": "string",
            "minLength": 1,
            "description": "Organisation unit key; features are grouped by it.",
        },
        "input_schema": {"$ref": "#/$defs/inputSchema"},
        "ui_schema": {"$ref": "#/$defs/uiSchema"},
        "prompt": {"$ref": "#/$defs/prompt"},
        "output": {"$ref": "#/$defs/output"},
        "permissions": {"$ref": "#/$defs/permissions"},
    },
    "$defs": {
        "localized": {
            "type": "object",
            "description": "Bilingual text; both locales are required.",
            "required": ["zh", "en"],
            "properties": {
                "zh": {"type": "string", "minLength": 1},
                "en": {"type": "string", "minLength": 1},
            },
        },
        "inputSchema": {
            "type": "object",
            "description": (
                "Root field container. 'object' is only valid here; array items may nest "
                "but must not contain objects."
            ),
            "required": ["type", "properties"],
            "additionalProperties": False,
            "properties": {
                "type": {"const": "object"},
                "title": {"$ref": "#/$defs/localized"},
                "description": {"$ref": "#/$defs/localized"},
                "properties": {
                    "type": "object",
                    "minProperties": 1,
                    "additionalProperties": {"$ref": "#/$defs/fieldSchema"},
                },
                "required": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Property names the user has to fill in.",
                },
            },
        },
        "fieldSchema": {
            "type": "object",
            "description": "Single input field; only the documented keywords are accepted.",
            "required": ["type"],
            "additionalProperties": False,
            "properties": {
                "type": {"enum": list(ALLOWED_FIELD_TYPES)},
                "title": {"$ref": "#/$defs/localized"},
                "description": {"$ref": "#/$defs/localized"},
                "format": {"enum": list(ALLOWED_FORMATS)},
                "enum": {"type": "array", "minItems": 1, "items": {"type": "string"}},
                "items": {
                    "description": "Element schema; nested arrays render as a grid of strings.",
                    "$ref": "#/$defs/fieldSchema",
                },
            },
        },
        "uiSchema": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "order": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Field order for the run form.",
                },
                "widgets": {
                    "type": "object",
                    "additionalProperties": {"type": "string"},
                    "description": "Per-field widget override, for example 'textarea'.",
                },
            },
        },
        "prompt": {
            "type": "object",
            "required": ["user_template"],
            "additionalProperties": False,
            "properties": {
                "system_file": {
                    "type": "string",
                    "pattern": "^(?![/\\\\])(?!.*\\.\\.).+$",
                    "description": (
                        "Optional system prompt file, relative to the feature directory."
                    ),
                },
                "user_template": {
                    "type": "string",
                    "minLength": 1,
                    "description": (
                        "User prompt template. '{{inputs}}' renders the human-readable input "
                        "summary, '{{inputs_json}}' the raw JSON; other placeholders stay as-is."
                    ),
                },
            },
        },
        "output": {
            "type": "object",
            "required": ["kind"],
            "additionalProperties": False,
            "properties": {"kind": {"enum": list(ALLOWED_OUTPUT_KINDS)}},
        },
        "permissions": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "allow_units": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Unit keys allowed to run this feature; '*' means any.",
                },
                "allow_roles": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "Roles allowed to run this feature.",
                },
            },
        },
    },
}


def feature_json_schema() -> dict[str, Any]:
    """Return the JSON Schema (draft 2020-12) describing the ``feature.json`` format."""
    return deepcopy(_FEATURE_JSON_SCHEMA)
