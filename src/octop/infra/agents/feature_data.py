"""Generic per-feature business data: schema, records, files and reminders.

The feature's *workflow* describes what a feature does; this module describes the
business records a feature keeps while doing it (suppliers, controlled documents,
equipment, contracts, ...).  The schema is a workspace document so it moves with
the feature workspace and is included in workspace backups.  Records are rows in
the main database so they can be listed, filtered and audited across users.

Nothing here is supplier- or document-specific.  A supplier directory is a ledger
object with a date field carrying ``reminder_days``; a controlled document is a
document object whose fields include a file field and a status.  The same engine
serves both.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, Any
from zoneinfo import ZoneInfo

from octop.infra.agents.feature_data_refs import (
    FeatureReferenceService,
    ref_fields,
    validate_reference_shape,
)
from octop.infra.agents.feature_data_rules import (
    apply_computed_fields,
    validate_rule_schema,
    validate_rule_value,
)
from octop.infra.agents.workspace_dir import SYSTEM_FILES_DIRNAME, system_dir_relpaths
from octop.infra.db.repos.feature_data import (
    FeatureDataChangeRow,
    FeatureDataFileRow,
    FeatureDataRecordRow,
    FeatureDataReminderRow,
    FeatureDataRepo,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.utils.ulid import new_ulid

if TYPE_CHECKING:
    from harness_agent.backends.workspace import BackendWorkspace

SCHEMA_VERSION = 1
SCHEMA_LEAF = "feature-data/schema.json"
MAX_OBJECTS = 20
MAX_FIELDS = 40
MAX_FILE_BYTES = 50 * 1024 * 1024
MAX_TEXT_CHARS = 20_000
FIELD_TYPES = frozenset(
    {
        "text",
        "textarea",
        "number",
        "integer",
        "boolean",
        "date",
        "datetime",
        "enum",
        "file",
        "record_ref",
        "knowledge_ref",
        "user",
        "org_unit",
    }
)
OBJECT_KINDS = frozenset({"ledger", "document"})
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


@dataclass(frozen=True)
class SchemaLoad:
    schema: dict[str, Any] | None = None
    error: str | None = None


@dataclass(frozen=True)
class RecordView:
    row: FeatureDataRecordRow
    files: list[FeatureDataFileRow]
    reminders: list[FeatureDataReminderRow]


def _localized(node: Any, locale: str = "zh", fallback: str = "") -> str:
    if isinstance(node, str):
        return node.strip()
    if isinstance(node, Mapping):
        for key in (locale, "zh", "en"):
            value = node.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
    return fallback


def _safe_key(value: Any, what: str) -> str:
    key = str(value or "").strip()
    if not _KEY_RE.fullmatch(key):
        raise ValueError(f"{what} key must match {_KEY_RE.pattern}")
    return key


def _field_label(field: Mapping[str, Any]) -> str:
    return _localized(field.get("label"), fallback=str(field.get("key") or ""))


def validate_schema(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, Mapping):
        raise ValueError("feature data schema must be an object")
    version = raw.get("version", SCHEMA_VERSION)
    if version != SCHEMA_VERSION:
        raise ValueError(f"feature data schema version must be {SCHEMA_VERSION}")
    objects = raw.get("objects")
    if not isinstance(objects, list) or not objects:
        raise ValueError("feature data schema must define at least one object")
    if len(objects) > MAX_OBJECTS:
        raise ValueError(f"feature data schema may contain at most {MAX_OBJECTS} objects")
    seen_keys: set[str] = set()
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(objects):
        if not isinstance(item, Mapping):
            raise ValueError(f"objects[{index}] must be an object")
        key = _safe_key(item.get("key"), f"objects[{index}]")
        if key in seen_keys:
            raise ValueError(f"duplicate feature data object key: {key}")
        seen_keys.add(key)
        kind = str(item.get("kind") or "ledger")
        if kind not in OBJECT_KINDS:
            raise ValueError(f"objects[{index}].kind must be ledger or document")
        fields = item.get("fields")
        if not isinstance(fields, list) or not fields:
            raise ValueError(f"objects[{index}].fields must be a non-empty list")
        if len(fields) > MAX_FIELDS:
            raise ValueError(f"objects[{index}] may contain at most {MAX_FIELDS} fields")
        seen_fields: set[str] = set()
        normalized_fields: list[dict[str, Any]] = []
        has_file = False
        for field_index, field in enumerate(fields):
            if not isinstance(field, Mapping):
                raise ValueError(f"objects[{index}].fields[{field_index}] must be an object")
            field_key = _safe_key(field.get("key"), f"objects[{index}].fields[{field_index}]")
            if field_key in seen_fields:
                raise ValueError(f"duplicate field key in {key}: {field_key}")
            seen_fields.add(field_key)
            field_type = str(field.get("type") or "text")
            if field_type not in FIELD_TYPES:
                raise ValueError(f"{key}.{field_key} has unsupported type {field_type!r}")
            if field_type == "file":
                has_file = True
            if field_type == "record_ref":
                target_object = field.get("target_object")
                if not isinstance(target_object, str) or not target_object.strip():
                    raise ValueError(f"{key}.{field_key} must declare target_object")
                target_agent = field.get("target_agent_id")
                if target_agent is not None and (
                    not isinstance(target_agent, str) or not target_agent.strip()
                ):
                    raise ValueError(f"{key}.{field_key}.target_agent_id must be a feature id")
            options = field.get("options")
            if field_type == "enum":
                if not isinstance(options, list) or not options:
                    raise ValueError(f"{key}.{field_key} enum requires options")
                options = [str(option) for option in options]
            reminder_days = field.get("reminder_days")
            if reminder_days is not None:
                if field_type not in {"date", "datetime"}:
                    raise ValueError(f"{key}.{field_key} reminder_days requires a date field")
                if not isinstance(reminder_days, list) or not reminder_days:
                    raise ValueError(f"{key}.{field_key} reminder_days must be a list")
                normalized_days: list[int] = []
                for value in reminder_days:
                    if type(value) is not int or value < 0 or value > 3650:
                        raise ValueError(f"{key}.{field_key} reminder day is invalid")
                    normalized_days.append(value)
                normalized_days = sorted(set(normalized_days), reverse=True)
            else:
                normalized_days = []
            normalized_fields.append(
                {
                    **dict(field),
                    "key": field_key,
                    "type": field_type,
                    "label": field.get("label") or field_key,
                    "required": bool(field.get("required", False)),
                    **({"options": options} if field_type == "enum" else {}),
                    **({"reminder_days": normalized_days} if normalized_days else {}),
                }
            )
        controlled = bool(item.get("controlled", False))
        if controlled:
            if kind != "document":
                raise ValueError(f"objects[{index}] controlled mode requires kind=document")
            if not has_file:
                raise ValueError(f"objects[{index}] controlled document requires a file field")
        title_field = str(item.get("title_field") or "")
        if title_field and title_field not in seen_fields:
            raise ValueError(f"objects[{index}].title_field is not a field")
        normalized.append(
            {
                **dict(item),
                "key": key,
                "kind": kind,
                "name": item.get("name") or key,
                "title_field": title_field or _first_text_field(normalized_fields),
                "controlled": controlled,
                "fields": normalized_fields,
            }
        )
    return validate_rule_schema({"version": SCHEMA_VERSION, "objects": normalized})


def _first_text_field(fields: list[dict[str, Any]]) -> str:
    for field in fields:
        if field["type"] in {"text", "textarea"}:
            return str(field["key"])
    return str(fields[0]["key"])


def object_definition(schema: Mapping[str, Any], object_key: str) -> dict[str, Any]:
    for item in schema.get("objects", []):
        if isinstance(item, Mapping) and item.get("key") == object_key:
            return dict(item)
    raise OctopError(ErrorCode.NOT_FOUND, f"feature data object {object_key!r} not found")


def field_definition(obj: Mapping[str, Any], field_key: str) -> dict[str, Any]:
    for item in obj.get("fields", []):
        if isinstance(item, Mapping) and item.get("key") == field_key:
            return dict(item)
    raise OctopError(ErrorCode.NOT_FOUND, f"feature data field {field_key!r} not found")


def _schema_path() -> str:
    return system_dir_relpaths(SCHEMA_LEAF)[0]


def file_path(record_id: str, file_id: str) -> str:
    return f"{SYSTEM_FILES_DIRNAME}/feature-data/files/{record_id}/{file_id}"


async def load_schema(workspace: BackendWorkspace) -> SchemaLoad:
    for rel in system_dir_relpaths(SCHEMA_LEAF):
        try:
            text = await workspace.aread_text(rel)
        except Exception:
            continue
        if text is None or not str(text).strip():
            continue
        try:
            return SchemaLoad(schema=validate_schema(json.loads(str(text))))
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            return SchemaLoad(error=f"{rel} is invalid: {exc}")
    return SchemaLoad()


async def save_schema(workspace: BackendWorkspace, raw: Any) -> dict[str, Any]:
    schema = validate_schema(raw)
    payload = json.dumps(schema, ensure_ascii=False, indent=2)
    await workspace.awrite_text(_schema_path(), payload + "\n", force=True)
    return schema


def _validate_value(field: Mapping[str, Any], value: Any) -> Any:
    field_type = str(field.get("type"))
    if value is None:
        return None
    if field_type in {"text", "textarea"}:
        if not isinstance(value, str):
            raise ValueError(f"{field['key']} must be text")
        if len(value) > MAX_TEXT_CHARS:
            raise ValueError(f"{field['key']} exceeds {MAX_TEXT_CHARS} characters")
        return value
    if field_type == "number":
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError(f"{field['key']} must be a number")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"{field['key']} must be a finite number")
        return value
    if field_type == "integer":
        if type(value) is not int:
            raise ValueError(f"{field['key']} must be an integer")
        return value
    if field_type == "boolean":
        if type(value) is not bool:
            raise ValueError(f"{field['key']} must be a boolean")
        return value
    if field_type == "enum":
        options = field.get("options") or []
        if value not in options:
            raise ValueError(f"{field['key']} must be one of {options}")
        return value
    if field_type in {"date", "datetime"}:
        if not isinstance(value, str):
            raise ValueError(f"{field['key']} must be an ISO date string")
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise ValueError(f"{field['key']} must be an ISO date string") from exc
        if field_type == "date" and not _DATE_RE.fullmatch(value):
            raise ValueError(f"{field['key']} must use YYYY-MM-DD")
        return value if value else parsed.isoformat()
    if field_type == "file":
        return None
    if field_type == "record_ref":
        return validate_reference_shape(field, value)
    if field_type == "knowledge_ref":
        return validate_reference_shape(field, value)
    if field_type == "user":
        if type(value) is not int or value <= 0:
            raise ValueError(f"{field['key']} must be a positive user id")
        return value
    if field_type == "org_unit":
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"{field['key']} must be a non-empty organization unit key")
        return value.strip()
    raise ValueError(f"unsupported field type {field_type!r}")


def validate_values(
    obj: Mapping[str, Any],
    raw: Any,
    *,
    existing: Mapping[str, Any] | None = None,
    user_repo: Any | None = None,
    unit_repo: Any | None = None,
) -> dict[str, Any]:
    values = dict(raw) if isinstance(raw, Mapping) else {}
    fields = {str(field["key"]): field for field in obj.get("fields", [])}
    for key in values:
        if key not in fields:
            raise ValueError(f"unknown field {key!r}")
    merged = {**(existing or {}), **values}
    for key, field in fields.items():
        if field.get("type") == "file":
            merged.pop(key, None)
            continue
        empty = merged.get(key) in (None, "", [])
        if field.get("required") and not field.get("computed") and empty:
            raise ValueError(f"{key} is required")
        if key in values and not empty:
            merged[key] = _validate_value(field, values[key])
        elif key not in values and key in merged and not empty:
            merged[key] = _validate_value(field, merged[key])
        if field.get("type") in {"user", "org_unit"} and not empty:
            merged[key] = validate_rule_value(
                field, merged[key], user_repo=user_repo, unit_repo=unit_repo
            )
    return {key: value for key, value in merged.items() if key in fields}


def apply_computed_values(
    schema: Mapping[str, Any], obj: Mapping[str, Any], merged: Mapping[str, Any]
) -> dict[str, Any]:
    """Recompute derived date fields from the non-derived merged values.

    Computed keys are stripped first, so previous derived values never feed
    the next computation and a missing source/amount simply leaves the key
    unset. The output carries only the object's declared fields.
    """
    computed_keys = {str(field["key"]) for field in obj.get("fields", []) if field.get("computed")}
    base = {key: value for key, value in merged.items() if key not in computed_keys}
    computed = apply_computed_fields(
        schema, base, object_key=str(obj["key"]), reject_supplied=False
    )
    field_keys = {str(field["key"]) for field in obj.get("fields", [])}
    result = {key: value for key, value in computed.items() if key in field_keys}
    for field in obj.get("fields", []):
        key = str(field["key"])
        missing = result.get(key) in (None, "", [])
        if field.get("computed") and field.get("required") and missing:
            raise ValueError(f"{key} is required")
    return result


def record_title(obj: Mapping[str, Any], values: Mapping[str, Any]) -> str:
    title_field = str(obj.get("title_field") or "")
    value = values.get(title_field) if title_field else None
    if not value:
        value = next(
            (
                values.get(str(field["key"]))
                for field in obj.get("fields", [])
                if field.get("type") in {"text", "textarea"}
            ),
            "",
        )
    return str(value or "")[:200]


def _change_items(before: Mapping[str, Any], after: Mapping[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for key in sorted(set(before) | set(after)):
        if before.get(key) != after.get(key):
            items.append({"field": key, "before": before.get(key), "after": after.get(key)})
    return items


def _reminder_time(value: str, days: int, timezone_name: str) -> int:
    """Local-midnight timestamp *days* before *value*'s date.

    The server's configured timezone decides what "a day" means for reminder
    scheduling — business midnight, never a fixed UTC instant, so a Shanghai
    deployment and a Berlin one notify on their own calendars. Offsets are
    computed on the calendar (``date - timedelta``), which keeps the target a
    true local midnight across DST changes.
    """
    parsed = date.fromisoformat(value[:10])
    target = parsed - timedelta(days=days)
    tz = ZoneInfo(timezone_name)
    return int(datetime(target.year, target.month, target.day, tzinfo=tz).timestamp())


async def sync_reminders(
    repo: FeatureDataRepo,
    *,
    agent_id: str,
    object_key: str,
    record: FeatureDataRecordRow,
    obj: Mapping[str, Any],
    timezone_name: str,
) -> None:
    now = int(datetime.now(UTC).timestamp())
    for field in obj.get("fields", []):
        if not field.get("reminder_days"):
            continue
        field_key = str(field["key"])
        value = record.values.get(field_key)
        due: str | None = None
        if isinstance(value, str) and value.strip():
            try:
                candidate = value[:10]
                date.fromisoformat(candidate)
                due = candidate
            except ValueError:
                due = None
        repo.dismiss_reminders_for_field(record.id, field_key, keep_due_at=due)
        if due is None:
            continue
        next_notify = _reminder_time(
            due, max(int(day) for day in field["reminder_days"]), timezone_name
        )
        repo.upsert_reminder(
            agent_id=agent_id,
            record_id=record.id,
            object_key=object_key,
            field_key=field_key,
            due_at=due,
            title=f"{record.title} · {_field_label(field)}",
            next_notify_at=max(next_notify, now),
            created_by=record.created_by,
        )


async def create_record(
    repos: Any,
    workspace: BackendWorkspace,
    *,
    agent_id: str,
    object_key: str,
    values: Any,
    user_id: int,
    feature_active: bool,
    registry: Any,
    timezone_name: str,
) -> FeatureDataRecordRow:
    """Create one record through every write-path gate in order.

    ``repos`` is the shared repo bundle and ``registry`` the agent registry;
    both arrive from the HTTP router and the agent tools alike, so reference
    targets, user/org-unit fields and computed dates are validated the same
    way no matter which surface sent the values.
    """
    schema = (await load_schema(workspace)).schema
    if schema is None:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, "feature data schema is not configured")
    obj = object_definition(schema, object_key)
    repo: FeatureDataRepo = repos.feature_data_repo
    patch = await _validated_patch(
        repos=repos,
        registry=registry,
        agent_id=agent_id,
        obj=obj,
        schema=schema,
        patch=values,
        existing=None,
        user_id=user_id,
    )
    row = repo.create_record(
        agent_id=agent_id,
        object_key=object_key,
        title=record_title(obj, patch),
        values=patch,
        visibility="feature" if feature_active else "private",
        created_by=user_id,
    )
    repo.add_change(
        record_id=row.id,
        agent_id=agent_id,
        user_id=user_id,
        kind="create",
        summary="created",
        items=[{"field": key, "before": None, "after": value} for key, value in patch.items()],
    )
    await sync_reminders(
        repo,
        agent_id=agent_id,
        object_key=object_key,
        record=row,
        obj=obj,
        timezone_name=timezone_name,
    )
    return row


def _split_reference_patch(
    obj: Mapping[str, Any], patch: Mapping[str, Any], existing: Mapping[str, Any]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Split a patch into (merge values, refs needing access validation).

    Pure local-PATCH semantics for reference fields: only a reference the
    caller *actually changes* is re-validated against current access and
    re-pinned.

    * A value equal to the stored one merges as a no-op — a stale-but-
      unmodified pinned SHA never blocks an unrelated rename and is never
      silently repinned.
    * The redaction marker a client round-trips from a read
      (``{"redacted": true}``) means "no change", so saving a form that was
      served an inaccessible reference cannot wipe or corrupt it.
    * A knowledge_ref pointing at the *same document* keeps its stored pin and
      only updates the page/layout locator it explicitly carries — the target
      did not change, so no re-authorization or re-pin applies.
    """
    merge_patch = dict(patch)
    ref_patch: dict[str, Any] = {}
    for key, field in ref_fields(obj).items():
        if key not in merge_patch:
            continue
        value = merge_patch[key]
        if isinstance(value, Mapping) and value.get("redacted") is True:
            del merge_patch[key]
            continue
        previous = existing.get(key)
        if str(field.get("type")) == "record_ref":
            if value == previous:
                del merge_patch[key]
            else:
                ref_patch[key] = value
            continue
        if not isinstance(value, Mapping) or not isinstance(previous, Mapping):
            ref_patch[key] = value
            continue
        if value.get("kb_id") != previous.get("kb_id") or value.get("document_id") != previous.get(
            "document_id"
        ):
            ref_patch[key] = value
            continue
        merged = dict(previous)
        for locator in ("page", "layout"):
            if locator in value:
                merged[locator] = value[locator]
        merge_patch[key] = merged
    return merge_patch, ref_patch


async def _validated_patch(
    *,
    repos: Any,
    registry: Any,
    agent_id: str,
    obj: Mapping[str, Any],
    schema: Mapping[str, Any],
    patch: Any,
    existing: Mapping[str, Any] | None,
    user_id: int,
) -> dict[str, Any]:
    """Reference pinning, value validation and computed recomputation.

    Shared by create and update so no write path can skip a gate. Reference
    values the client actually sends are resolved against the caller's current
    access (missing knowledge versions are pinned to the document's current
    content hash); computed keys in the patch are rejected as forged and every
    derived value is recomputed from the non-derived merged values.
    """
    if not isinstance(patch, Mapping):
        patch = {}
    merge_patch, ref_patch = _split_reference_patch(obj, patch, existing or {})
    references = FeatureReferenceService(registry=registry, repos=repos, user_id=user_id)
    checked = await references.pin_and_validate_patch(agent_id=agent_id, obj=obj, patch=ref_patch)
    merged = validate_values(
        obj,
        {**merge_patch, **checked},
        existing=existing,
        user_repo=repos.user_repo,
        unit_repo=repos.org_unit_repo,
    )
    supplied_computed = {
        str(field["key"])
        for field in obj.get("fields", [])
        if field.get("computed") and str(field["key"]) in merge_patch
    }
    if supplied_computed:
        raise ValueError(
            f"computed field(s) {', '.join(sorted(supplied_computed))} cannot be supplied"
        )
    return apply_computed_values(schema, obj, merged)


async def update_record(
    repos: Any,
    workspace: BackendWorkspace,
    *,
    agent_id: str,
    record_id: str,
    values: Any,
    expected_revision: int,
    user_id: int,
    registry: Any,
    timezone_name: str,
) -> FeatureDataRecordRow:
    repo: FeatureDataRepo = repos.feature_data_repo
    record = repo.get_record(record_id)
    if record is None or record.agent_id != agent_id or record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature data record {record_id!r} not found")
    schema = (await load_schema(workspace)).schema
    if schema is None:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, "feature data schema is not configured")
    obj = object_definition(schema, record.object_key)
    normalized = await _validated_patch(
        repos=repos,
        registry=registry,
        agent_id=agent_id,
        obj=obj,
        schema=schema,
        patch=values,
        existing=record.values,
        user_id=user_id,
    )
    updated = repo.update_record(
        record_id=record.id,
        values=normalized,
        title=record_title(obj, normalized),
        expected_revision=expected_revision,
    )
    if updated is None:
        raise OctopError(
            ErrorCode.WORKFLOW_CHANGE_CONFLICT, "feature data record was changed by another user"
        )
    repo.add_change(
        record_id=record.id,
        agent_id=agent_id,
        user_id=user_id,
        kind="update",
        summary="updated",
        items=_change_items(record.values, normalized),
    )
    await sync_reminders(
        repo,
        agent_id=agent_id,
        object_key=record.object_key,
        record=updated,
        obj=obj,
        timezone_name=timezone_name,
    )
    return updated


def publish_record(
    repo: FeatureDataRepo, *, agent_id: str, record_id: str, user_id: int
) -> FeatureDataRecordRow:
    record = repo.get_record(record_id)
    if record is None or record.agent_id != agent_id or record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature data record {record_id!r} not found")
    updated = repo.publish_record(record_id)
    assert updated is not None
    repo.add_change(
        record_id=record_id,
        agent_id=agent_id,
        user_id=user_id,
        kind="publish",
        summary="published to feature",
        items=[{"field": "visibility", "before": record.visibility, "after": "feature"}],
    )
    return updated


def delete_record(repo: FeatureDataRepo, *, agent_id: str, record_id: str, user_id: int) -> None:
    record = repo.get_record(record_id)
    if record is None or record.agent_id != agent_id or record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature data record {record_id!r} not found")
    if not repo.soft_delete(record_id):
        raise OctopError(ErrorCode.NOT_FOUND, f"feature data record {record_id!r} not found")
    repo.add_change(
        record_id=record_id,
        agent_id=agent_id,
        user_id=user_id,
        kind="delete",
        summary="deleted",
        items=[],
    )


async def add_file(
    repo: FeatureDataRepo,
    workspace: BackendWorkspace,
    *,
    agent_id: str,
    record_id: str,
    field_key: str,
    filename: str,
    content_type: str,
    data: bytes,
    note: str,
    user_id: int,
) -> FeatureDataFileRow:
    record = repo.get_record(record_id)
    if record is None or record.agent_id != agent_id or record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, f"feature data record {record_id!r} not found")
    schema = (await load_schema(workspace)).schema
    if schema is None:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, "feature data schema is not configured")
    obj = object_definition(schema, record.object_key)
    field = field_definition(obj, field_key)
    if field.get("type") != "file":
        raise OctopError(ErrorCode.WORKFLOW_INVALID, f"{field_key} is not a file field")
    if not data:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, "empty files are not accepted")
    if len(data) > MAX_FILE_BYTES:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, f"file exceeds {MAX_FILE_BYTES} bytes")
    file_id = new_ulid()
    path = file_path(record_id, file_id)
    await workspace.aupload_bytes(path, data)
    row = repo.add_file(
        record_id=record_id,
        agent_id=agent_id,
        field_key=field_key,
        filename=(
            PurePosixPath(filename or "file")
            .name.replace('"', "_")
            .replace("\r", "_")
            .replace("\n", "_")[:255]
        ),
        content_type=(content_type or "application/octet-stream")[:255],
        size=len(data),
        sha256=hashlib.sha256(data).hexdigest(),
        storage_path=path,
        note=note[:1000],
        uploaded_by=user_id,
    )
    repo.add_change(
        record_id=record_id,
        agent_id=agent_id,
        user_id=user_id,
        kind="file",
        summary=f"uploaded {field_key} v{row.version}",
        items=[{"field": field_key, "before": row.version - 1, "after": row.version}],
    )
    return row


async def record_view(
    repo: FeatureDataRepo, workspace: BackendWorkspace, record: FeatureDataRecordRow
) -> RecordView:
    del workspace
    return RecordView(
        row=record, files=repo.list_files(record.id), reminders=repo.list_reminders(record.id)
    )


def record_payload(view: RecordView) -> dict[str, Any]:
    row = view.row
    return {
        "id": row.id,
        "agent_id": row.agent_id,
        "object_key": row.object_key,
        "title": row.title,
        "values": row.values,
        "visibility": row.visibility,
        "revision": row.revision,
        "created_by": row.created_by,
        "responsible_user_id": row.responsible_user_id,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
        "files": [file_payload(file) for file in view.files],
        "reminders": [reminder_payload(reminder) for reminder in view.reminders],
    }


def file_payload(row: FeatureDataFileRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "field_key": row.field_key,
        "version": row.version,
        "filename": row.filename,
        "content_type": row.content_type,
        "size": row.size,
        "sha256": row.sha256,
        "note": row.note,
        "uploaded_by": row.uploaded_by,
        "created_at": row.created_at,
        "current": row.current,
    }


def reminder_payload(row: FeatureDataReminderRow) -> dict[str, Any]:
    return {
        "id": row.id,
        "record_id": row.record_id,
        "object_key": row.object_key,
        "field_key": row.field_key,
        "due_at": row.due_at,
        "title": row.title,
        "status": row.status,
        "next_notify_at": row.next_notify_at,
        "last_notified_at": row.last_notified_at,
    }


__all__ = [
    "FeatureDataChangeRow",
    "FeatureDataFileRow",
    "FeatureDataReminderRow",
    "FeatureDataRepo",
    "RecordView",
    "SchemaLoad",
    "add_file",
    "create_record",
    "delete_record",
    "field_definition",
    "file_path",
    "file_payload",
    "load_schema",
    "object_definition",
    "publish_record",
    "record_payload",
    "record_title",
    "record_view",
    "reminder_payload",
    "save_schema",
    "sync_reminders",
    "update_record",
    "validate_schema",
    "validate_values",
]
