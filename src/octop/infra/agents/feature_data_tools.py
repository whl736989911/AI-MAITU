"""Agent tools for a feature's own business records.

The feature's dashboard panel and these tools share
:mod:`octop.infra.agents.feature_data`; the tools only add turn identity and the
same ACL/creator rules, so the model cannot read or change another feature's data.
"""

from __future__ import annotations

import json
from typing import Any

from langchain_core.tools import StructuredTool
from langgraph.config import get_config

from octop.infra.agents import feature_data as fd
from octop.infra.agents import feature_workflow as wf
from octop.infra.agents.kinds import feature_id_of_agent, is_feature_agent
from octop.infra.sharing import can_access


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


def _context(registry: Any, repos: Any) -> tuple[Any, Any, Any, int]:
    agent_id, user_id = _turn_identity()
    if not agent_id or not user_id:
        raise ValueError("this tool needs the turn's agent and user")
    row = registry.get_row(agent_id)
    if row is None or not is_feature_agent(str(getattr(row, "kind", ""))):
        raise ValueError("this tool is only available to a feature's own agent")
    feature_id = feature_id_of_agent(agent_id)
    acl = repos.resource_acl_repo
    role, unit_keys = acl.scope_for_user(user_id)
    entries = (acl.get("agent", agent_id), acl.get("feature", feature_id))
    allowed = row.user_id == user_id or any(
        can_access(entry, user_id=user_id, role=role, unit_keys=unit_keys)
        for entry in entries
        if entry is not None
    )
    if not allowed:
        raise ValueError("this caller cannot access the feature")
    workspace = registry.workspace_for_agent(agent_id)
    if workspace is None:
        raise ValueError("this feature's workspace is not reachable")
    return row, workspace, repos.feature_data_repo, user_id


def _can_manage(row: Any, user_id: int) -> bool:
    return bool(row.user_id == user_id)


def _record_read(record: Any, user_id: int) -> None:
    if record.visibility == "private" and record.created_by != user_id:
        raise ValueError("this private record belongs to another caller")


def _record_write(record: Any, row: Any, user_id: int) -> None:
    if record.created_by == user_id:
        return
    if record.visibility == "feature" and _can_manage(row, user_id):
        return
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
    async def feature_data_schema() -> str:
        """Read this feature's business object schema and field definitions."""
        try:
            _row, workspace, _repo, _user = _context(registry, repos)
            loaded = await fd.load_schema(workspace)
            return _ok({"schema": loaded.schema, "error": loaded.error})
        except Exception as exc:
            return _err(exc)

    async def feature_data_list(object_key: str, query: str = "", limit: int = 20) -> str:
        """List business records of one object key visible to the current caller."""
        try:
            _row, _workspace, repo, user_id = _context(registry, repos)
            rows, total = repo.list_records(
                agent_id=_row.agent_id,
                object_key=object_key,
                user_id=user_id,
                query=query,
                limit=max(1, min(limit, 100)),
            )
            return _ok(
                {
                    "records": [
                        fd.record_payload(await fd.record_view(repo, _workspace, row))
                        for row in rows
                    ],
                    "total": total,
                }
            )
        except Exception as exc:
            return _err(exc)

    async def feature_data_get(record_id: str) -> str:
        """Read one business record, its attachments and reminders."""
        try:
            _row, workspace, repo, user_id = _context(registry, repos)
            record = repo.get_record(record_id)
            if record is None or record.agent_id != _row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            return _ok(fd.record_payload(await fd.record_view(repo, workspace, record)))
        except Exception as exc:
            return _err(exc)

    async def feature_data_create(
        object_key: str,
        values_json: str,
    ) -> str:
        """Create a business record of one object key from a JSON values object."""
        try:
            row, workspace, repo, user_id = _context(registry, repos)
            loaded = await wf.load_workflow(workspace)
            active = (
                loaded.definition is not None
                and wf.workflow_status(loaded.definition) == wf.STATUS_ACTIVE
            )
            record = await fd.create_record(
                repo,
                workspace,
                agent_id=row.agent_id,
                object_key=object_key,
                values=_values(values_json),
                user_id=user_id,
                feature_active=active,
            )
            return _ok(fd.record_payload(await fd.record_view(repo, workspace, record)))
        except Exception as exc:
            return _err(exc)

    async def feature_data_update(
        record_id: str,
        values_json: str,
        expected_revision: int,
    ) -> str:
        """Update fields on a record this caller may write, with optimistic revision."""
        try:
            row, workspace, repo, user_id = _context(registry, repos)
            record = repo.get_record(record_id)
            if record is None or record.agent_id != row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            _record_write(record, row, user_id)
            updated = await fd.update_record(
                repo,
                workspace,
                agent_id=row.agent_id,
                record_id=record_id,
                values=_values(values_json),
                expected_revision=expected_revision,
                user_id=user_id,
            )
            return _ok(fd.record_payload(await fd.record_view(repo, workspace, updated)))
        except Exception as exc:
            return _err(exc)

    async def feature_data_history(record_id: str, limit: int = 20) -> str:
        """Read a record's field and attachment change history."""
        try:
            row, _workspace, repo, user_id = _context(registry, repos)
            record = repo.get_record(record_id)
            if record is None or record.agent_id != row.agent_id or record.deleted_at is not None:
                raise ValueError("record not found")
            _record_read(record, user_id)
            changes = repo.list_changes(record_id, limit=max(1, min(limit, 100)))
            return _ok(
                {
                    "changes": [
                        {
                            "id": change.id,
                            "kind": change.kind,
                            "summary": change.summary,
                            "items": change.items,
                            "created_at": change.created_at,
                        }
                        for change in changes
                    ]
                }
            )
        except Exception as exc:
            return _err(exc)

    return [
        StructuredTool.from_function(
            coroutine=feature_data_schema,
            name="feature_data_schema",
            description="Read the feature's business object schema before creating or updating records.",
        ),
        StructuredTool.from_function(
            coroutine=feature_data_list,
            name="feature_data_list",
            description="List visible business records for one object key.",
        ),
        StructuredTool.from_function(
            coroutine=feature_data_get,
            name="feature_data_get",
            description="Read one business record, its fields, attachments and reminders.",
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
