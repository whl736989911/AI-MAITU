"""Agent tools for a feature's own business records.

The feature's dashboard panel and these tools share
:mod:`octop.infra.agents.feature_data`; the tools only add turn identity and
call the same central policy (:mod:`octop.infra.agents.feature_access`) the
HTTP router uses, so the model cannot read or change another feature's data
and an enterprise manager's reach over published records is identical through
either surface. Reference targets, computed fields and user/org-unit values
go through the same write-path gates as the API.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from octop.infra.agents import feature_data as fd
from octop.infra.agents import feature_data_query as fdq
from octop.infra.agents import feature_extract_rules as fer
from octop.infra.agents import feature_workflow as wf
from octop.infra.agents.feature_access import (
    can_read_record,
    can_use_feature,
    can_write_record,
)
from octop.infra.agents.feature_data_refs import FeatureReferenceService
from octop.infra.agents.kinds import is_feature_agent

DEFAULT_TIMEZONE = "Asia/Shanghai"


def _turn_identity() -> tuple[str, int]:
    try:
        config = get_config()
    except RuntimeError:
        return "", 0
    configurable = (config.get("configurable") or {}) if isinstance(config, dict) else {}
    agent_id = str(configurable.get("agent_id") or "")
    raw = configurable.get("user")
    user_id = int(raw) if isinstance(raw, int | str) and str(raw).isdigit() else 0
    return agent_id, user_id


def _timezone(registry: Any) -> str:
    config = getattr(registry, "octop_config", None)
    return str(getattr(config, "default_timezone", "") or DEFAULT_TIMEZONE)


def _context(registry: Any, repos: Any) -> tuple[Any, Any, Any, int]:
    agent_id, user_id = _turn_identity()
    if not agent_id or not user_id:
        raise ValueError("this tool needs the turn's agent and user")
    row = registry.get_row(agent_id)
    if row is None or not is_feature_agent(str(getattr(row, "kind", ""))):
        raise ValueError("this tool is only available to a feature's own agent")
    if not can_use_feature(
        row,
        user_id=user_id,
        acl_repo=repos.resource_acl_repo,
        user_repo=repos.user_repo,
        org_unit_repo=repos.org_unit_repo,
    ):
        raise ValueError("this caller cannot access the feature")
    workspace = registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise ValueError("this feature's workspace is not reachable")
    return row, workspace, repos, user_id


def _record_read(record: Any, user_id: int) -> None:
    if not can_read_record(record, user_id=user_id):
        raise ValueError("this private record belongs to another caller")


def _record_write(record: Any, row: Any, repos: Any, user_id: int) -> None:
    if not can_write_record(
        record, row, user_id=user_id, user_repo=repos.user_repo, org_unit_repo=repos.org_unit_repo
    ):
        raise ValueError("this caller cannot write the record")


def _ok(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2)


def _err(exc: Exception) -> str:
    return json.dumps({"error": str(exc)}, ensure_ascii=False, indent=2)


def _values(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"values_json must be a JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise ValueError("values_json must be a JSON object")
    return value


def build_feature_data_tools(*, registry: Any, repos: Any) -> list[StructuredTool]:
    async def _described_payload(
        record: Any,
        row: Any,
        workspace: Any,
        user_id: int,
        *,
        obj: dict[str, Any] | None = None,
        references: FeatureReferenceService | None = None,
    ) -> dict[str, Any]:
        if obj is None:
            loaded = await fd.load_schema(workspace)
            if loaded.schema is None:
                raise ValueError(loaded.error or "feature data schema is not configured")
            obj = fd.object_definition(loaded.schema, record.object_key)
        payload = fd.record_payload(
            await fd.record_view(repos.feature_data_repo, workspace, record)
        )
        payload["can_write"] = can_write_record(
            record,
            row,
            user_id=user_id,
            user_repo=repos.user_repo,
            org_unit_repo=repos.org_unit_repo,
        )
        if references is None:
            references = FeatureReferenceService(registry=registry, repos=repos, user_id=user_id)
        payload["references"], payload["values"] = await references.describe_with_values(
            agent_id=row.agent_id, obj=obj, record=record
        )
        return payload

    async def feature_data_schema() -> str:
        """Read this feature's business object schema and field definitions."""
        try:
            _row, workspace, _repos, _user = _context(registry, repos)
            loaded = await fd.load_schema(workspace)
            return _ok({"schema": loaded.schema, "error": loaded.error})
        except Exception as exc:
            return _err(exc)

    async def extract_rules_get() -> str:
        """Read the field extraction rules (which drawing text fills which field)."""
        try:
            _row, workspace, _repos, _user = _context(registry, repos)
            loaded = await fer.load_rules(workspace)
            return _ok({"rules": loaded.rules, "error": loaded.error})
        except Exception as exc:
            return _err(exc)

    async def extract_rules_set(rules_json: str) -> str:
        """Replace the extraction rules. Ask the user to confirm the mapping first.

        Every rule key must already be a field of the data schema (read it with
        feature_data_schema); labels/patterns/picks are free to change.
        """
        try:
            _row, workspace, _repos, _user = _context(registry, repos)
            schema = (await fd.load_schema(workspace)).schema
            if schema is None:
                raise ValueError("feature data schema is not configured")
            rules = await fer.save_rules(workspace, json.loads(rules_json), schema=schema)
            return _ok({"rules": rules})
        except Exception as exc:
            return _err(exc)

    async def feature_data_list(
        object_key: str,
        query: str = "",
        limit: int = 20,
        offset: int = 0,
        filters_json: str = "",
        sort_by: str = "",
        sort_order: str = "",
    ) -> str:
        """List business records of one object key visible to the current caller.

        filters_json is a JSON array of AND conditions, e.g.
        [{"field":"weight","op":"gte","value":100}]; ops: eq, ne, contains, gt,
        gte, lt, lte, in, is_empty. sort_by: a declared field or
        created_at/updated_at/title; sort_order: asc or desc.
        """
        try:
            row, workspace, repos_bundle, user_id = _context(registry, repos)
            loaded = await fd.load_schema(workspace)
            if loaded.schema is None:
                raise ValueError(loaded.error or "feature data schema is not configured")
            obj = fd.object_definition(loaded.schema, object_key)
            fragments, params, order_by = fdq.filters_from_request(
                filters_json,
                sort_by or None,
                sort_order or None,
                obj,
                dialect=repos_bundle.db.dialect,
            )
            records, total = repos_bundle.feature_data_repo.list_records(
                agent_id=row.agent_id,
                object_key=object_key,
                user_id=user_id,
                query=query,
                limit=max(1, min(limit, 100)),
                offset=max(0, offset),
                filter_fragments=fragments,
                filter_params=params,
                order_by=order_by,
            )
            references = FeatureReferenceService(
                registry=registry, repos=repos_bundle, user_id=user_id
            )
            payloads = []
            for record in records:
                payloads.append(
                    await _described_payload(
                        record, row, workspace, user_id, obj=obj, references=references
                    )
                )
            return _ok({"records": payloads, "total": total})
        except Exception as exc:
            return _err(exc)

    async def feature_data_get(record_id: str) -> str:
        """Read one business record, its attachments, reminders and references."""
        try:
            row, workspace, repos_bundle, user_id = _context(registry, repos)
            record = repos_bundle.feature_data_repo.get_record(record_id)
            if record is None or record.agent_id != row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            return _ok(await _described_payload(record, row, workspace, user_id))
        except Exception as exc:
            return _err(exc)

    async def feature_data_create(object_key: str, values_json: str) -> str:
        """Create a business record of one object key from a JSON values object."""
        try:
            row, workspace, repos_bundle, user_id = _context(registry, repos)
            loaded = await wf.load_workflow(workspace)
            active = (
                loaded.definition is not None
                and wf.workflow_status(loaded.definition) == wf.STATUS_ACTIVE
            )
            record = await fd.create_record(
                repos_bundle,
                workspace,
                agent_id=row.agent_id,
                object_key=object_key,
                values=_values(values_json),
                user_id=user_id,
                feature_active=active,
                registry=registry,
                timezone_name=_timezone(registry),
            )
            return _ok(await _described_payload(record, row, workspace, user_id))
        except Exception as exc:
            return _err(exc)

    async def feature_data_update(
        record_id: str,
        values_json: str,
        expected_revision: int,
    ) -> str:
        """Update fields on a record this caller may write, with optimistic revision."""
        try:
            row, workspace, repos_bundle, user_id = _context(registry, repos)
            record = repos_bundle.feature_data_repo.get_record(record_id)
            if record is None or record.agent_id != row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            _record_write(record, row, repos_bundle, user_id)
            updated = await fd.update_record(
                repos_bundle,
                workspace,
                agent_id=row.agent_id,
                record_id=record_id,
                values=_values(values_json),
                expected_revision=expected_revision,
                user_id=user_id,
                registry=registry,
                timezone_name=_timezone(registry),
            )
            return _ok(await _described_payload(updated, row, workspace, user_id))
        except Exception as exc:
            return _err(exc)

    async def feature_data_history(record_id: str, limit: int = 20) -> str:
        """Read a record's field and attachment change history."""
        try:
            row, workspace, repos_bundle, user_id = _context(registry, repos)
            record = repos_bundle.feature_data_repo.get_record(record_id)
            if record is None or record.agent_id != row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            changes = repos_bundle.feature_data_repo.list_changes(
                record_id, limit=max(1, min(limit, 100))
            )
            serialized: list[dict[str, Any]] = []
            loaded = await fd.load_schema(workspace)
            references = FeatureReferenceService(
                registry=registry, repos=repos_bundle, user_id=user_id
            )
            for change in changes:
                items: list[dict[str, Any]] = list(change.items)
                if loaded.schema is not None:
                    obj = fd.object_definition(loaded.schema, record.object_key)
                    items = await references.redact_history(
                        agent_id=row.agent_id, obj=obj, items=items
                    )
                serialized.append(
                    {
                        "id": change.id,
                        "kind": change.kind,
                        "summary": change.summary,
                        "items": items,
                        "created_at": change.created_at,
                    }
                )
            return _ok({"changes": serialized})
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=feature_data_schema,
            name="feature_data_schema",
            description="Read the feature's business object schema before creating or updating records.",
        ),
        StructuredTool.from_function(
            coroutine=extract_rules_get,
            name="extract_rules_get",
            description=(
                "Read the field extraction rules: which drawing text fills which ledger "
                "field. Read this before proposing a mapping change."
            ),
        ),
        StructuredTool.from_function(
            coroutine=extract_rules_set,
            name="extract_rules_set",
            description=(
                "Replace the extraction rules (JSON with version/fields). Only call this "
                "after the user has explicitly confirmed the proposed mapping; every rule "
                "key must already exist in the data schema."
            ),
        ),
        StructuredTool.from_function(
            coroutine=feature_data_list,
            name="feature_data_list",
            description=(
                "List visible business records for one object key, with optional AND "
                "filters (filters_json), sort_by/sort_order and pagination."
            ),
        ),
        StructuredTool.from_function(
            coroutine=feature_data_get,
            name="feature_data_get",
            description="Read one business record, its fields, attachments, reminders and references.",
        ),
        StructuredTool.from_function(
            coroutine=feature_data_create,
            name="feature_data_create",
            description="Create a business record from an object key and JSON values.",
        ),
        StructuredTool.from_function(
            coroutine=feature_data_update,
            name="feature_data_update",
            description="Update one record with JSON values and the revision read from feature_data_get.",
        ),
        StructuredTool.from_function(
            coroutine=feature_data_history,
            name="feature_data_history",
            description="Read the change history of a business record.",
        ),
    ]


__all__ = ["build_feature_data_tools"]
