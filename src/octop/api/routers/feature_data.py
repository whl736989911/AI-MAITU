"""Generic per-feature business data API.

The schema is a workspace document; records and their audit/attachments/reminders
are database rows.  The router deliberately stays thin: every record-shaped rule
(validation, reminder calculation, attachment path, optimistic revision) lives in
:mod:`octop.infra.agents.feature_data`, every access decision in
:mod:`octop.infra.agents.feature_access` — the same policy the agent tools run
under — and structured filtering/sorting in
:mod:`octop.infra.agents.feature_data_query`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.agent import AgentCapability, require_agent_row
from octop.api.common.content_disposition import content_disposition
from octop.api.common.workspace import require_agent_workspace
from octop.api.deps import current_user, get_server
from octop.infra.agents import feature_data as fd
from octop.infra.agents import feature_data_query as fdq
from octop.infra.agents import feature_extract_rules as fer
from octop.infra.agents import feature_workflow as wf
from octop.infra.agents.feature_access import (
    can_read_record,
    can_use_feature,
    can_write_record,
    is_feature_manager,
)
from octop.infra.agents.feature_data_refs import FeatureReferenceService
from octop.infra.agents.feature_data_responsibility import handover_feature_records
from octop.infra.agents.kinds import is_feature_agent
from octop.infra.db.services import SharedServices
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.server import OctopServer

router = APIRouter(prefix="/agents", tags=["agents"])


def _services(server: OctopServer) -> SharedServices:
    services = server.services
    if services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "server services are not initialized")
    return services


def _registry(server: OctopServer) -> Any:
    assert server.app_runtime is not None
    return server.app_runtime.agent_registry


def _timezone(server: OctopServer) -> str:
    return str(_services(server).config.default_timezone or "UTC")


class SchemaBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_data: dict[str, Any] = Field(
        ..., alias="schema", description="Feature business-data object schema"
    )


class ExtractRulesBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    data: dict[str, Any] = Field(
        ...,
        alias="rules",
        description=(
            "Field extraction rules: `{version, fields: {field_key: {labels, pattern, "
            "unit, from, pick, limit}}}`. Every key must be a field of the data schema."
        ),
    )


class ExtractRulesResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    rules: dict[str, Any] | None = Field(default=None, alias="rules")
    error: str | None = None


class SchemaResponse(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_data: dict[str, Any] | None = Field(default=None, alias="schema")
    error: str | None = None


class RecordCreateBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)


class RecordUpdateBody(BaseModel):
    values: dict[str, Any] = Field(default_factory=dict)
    expected_revision: int = Field(..., ge=1)


class RecordListResponse(BaseModel):
    records: list[dict[str, Any]]
    total: int


class ChangeListResponse(BaseModel):
    changes: list[dict[str, Any]]


class NotificationResponse(BaseModel):
    notifications: list[dict[str, Any]]


class PrincipalListResponse(BaseModel):
    principals: list[dict[str, Any]]
    total: int


class HandoverBody(BaseModel):
    from_user_id: int = Field(..., ge=1)
    to_user_id: int = Field(..., ge=1)
    object_key: str | None = None


def _feature_row(agent_id: str, user: Any, server: OctopServer) -> Any:
    row = require_agent_row(agent_id, user=user, as_user=None, server=server)
    if not is_feature_agent(str(getattr(row, "kind", ""))):
        raise OctopError(ErrorCode.WORKFLOW_NOT_A_FEATURE, "feature data requires a feature agent")
    return row


def _can_manage(row: Any, user: Any, server: OctopServer) -> bool:
    repos = _services(server).repos
    return is_feature_manager(
        row,
        user_id=int(user.id),
        user_repo=repos.user_repo,
        org_unit_repo=repos.org_unit_repo,
    )


def _record_write(record: Any, row: Any, user: Any, server: OctopServer) -> None:
    repos = _services(server).repos
    if not can_write_record(
        record,
        row,
        user_id=int(user.id),
        user_repo=repos.user_repo,
        org_unit_repo=repos.org_unit_repo,
    ):
        raise OctopError(ErrorCode.FORBIDDEN, "feature data record is not writable by this user")


def _record_read(record: Any, user: Any) -> None:
    if not can_read_record(record, user_id=int(user.id)):
        raise OctopError(ErrorCode.FORBIDDEN, "private feature data record is not visible")
    if record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, "feature data record not found")


async def _workspace(agent_id: str, user: Any, server: OctopServer, *, write: bool) -> Any:
    return await require_agent_workspace(
        agent_id,
        user=user,
        server=server,
        capability=AgentCapability.CONFIGURATION if write else None,
    )


async def _active(workspace: Any) -> bool:
    loaded = await wf.load_workflow(workspace)
    return (
        loaded.definition is not None and wf.workflow_status(loaded.definition) == wf.STATUS_ACTIVE
    )


def _record(repo: Any, agent_id: str, record_id: str) -> Any:
    record = repo.get_record(record_id)
    if record is None or record.agent_id != agent_id or record.deleted_at is not None:
        raise OctopError(ErrorCode.NOT_FOUND, "feature data record not found")
    return record


async def _described_payload(
    record: Any,
    *,
    row: Any,
    server: OctopServer,
    workspace: Any,
    user: Any,
) -> dict[str, Any]:
    """Record payload with the caller's current view of its references."""
    repos = _services(server).repos
    payload = fd.record_payload(await fd.record_view(repos.feature_data_repo, workspace, record))
    payload["can_write"] = can_write_record(
        record,
        row,
        user_id=int(user.id),
        user_repo=repos.user_repo,
        org_unit_repo=repos.org_unit_repo,
    )
    loaded = await fd.load_schema(workspace)
    if loaded.schema is not None:
        obj = fd.object_definition(loaded.schema, record.object_key)
        references = FeatureReferenceService(
            registry=_registry(server), repos=repos, user_id=int(user.id)
        )
        described, values = await references.describe_with_values(
            agent_id=str(record.agent_id), obj=obj, record=record
        )
        payload["references"] = described
        payload["values"] = values
    return payload


@router.get(
    "/{agent_id}/feature-data/schema",
    response_model=SchemaResponse,
    summary="Read a feature's business-data schema",
)
async def get_schema(
    agent_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> SchemaResponse:
    _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    loaded = await fd.load_schema(workspace)
    return SchemaResponse.model_validate({"schema": loaded.schema, "error": loaded.error})


@router.put(
    "/{agent_id}/feature-data/schema",
    response_model=SchemaResponse,
    summary="Write a feature's business-data schema",
)
async def put_schema(
    agent_id: str,
    body: SchemaBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> SchemaResponse:
    row = _feature_row(agent_id, user, server)
    if not _can_manage(row, user, server):
        raise OctopError(ErrorCode.FORBIDDEN, "only a feature manager may edit its data schema")
    workspace = await _workspace(agent_id, user, server, write=True)
    try:
        schema = await fd.save_schema(workspace, body.schema_data)
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc
    return SchemaResponse.model_validate({"schema": schema})


@router.get(
    "/{agent_id}/feature-data/extract-rules",
    response_model=ExtractRulesResponse,
    summary="Read a feature's field-extraction rules",
)
async def get_extract_rules(
    agent_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> ExtractRulesResponse:
    """Which drawing text fills which ledger field, as the workspace holds it."""
    _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    loaded = await fer.load_rules(workspace)
    return ExtractRulesResponse(rules=loaded.rules, error=loaded.error)


@router.put(
    "/{agent_id}/feature-data/extract-rules",
    response_model=ExtractRulesResponse,
    summary="Write a feature's field-extraction rules",
    description=(
        "Stores `feature-data/extract-rules.json`. Every rule key must be a field of the "
        "feature's data schema, so a mapping cannot point at a field nobody can store; "
        "labels, regex fallbacks and dimension picks stay the author's choice."
    ),
)
async def put_extract_rules(
    agent_id: str,
    body: ExtractRulesBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> ExtractRulesResponse:
    row = _feature_row(agent_id, user, server)
    if not _can_manage(row, user, server):
        raise OctopError(
            ErrorCode.FORBIDDEN, "only a feature manager may edit its extraction rules"
        )
    workspace = await _workspace(agent_id, user, server, write=True)
    schema = (await fd.load_schema(workspace)).schema
    try:
        rules = await fer.save_rules(workspace, body.data, schema=schema)
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc
    return ExtractRulesResponse(rules=rules)


@router.get(
    "/{agent_id}/feature-data/notifications",
    response_model=NotificationResponse,
    summary="List in-app feature-data reminders",
)
async def get_notifications(
    agent_id: str,
    unread_only: bool = Query(False),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> NotificationResponse:
    _feature_row(agent_id, user, server)
    repo = _services(server).repos.feature_data_repo
    rows = repo.list_notifications(agent_id=agent_id, user_id=user.id, unread_only=unread_only)
    return NotificationResponse(
        notifications=[
            {
                "id": row.id,
                "agent_id": row.agent_id,
                "reminder_id": row.reminder_id,
                "title": row.title,
                "body": row.body,
                "channel": row.channel,
                "read_at": row.read_at,
                "created_at": row.created_at,
            }
            for row in rows
        ]
    )


@router.post(
    "/{agent_id}/feature-data/notifications/{notification_id}/read",
    summary="Mark an in-app feature-data reminder read",
)
async def read_notification(
    agent_id: str,
    notification_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    _feature_row(agent_id, user, server)
    changed = _services(server).repos.feature_data_repo.mark_notification_read(
        notification_id, user.id
    )
    if not changed:
        raise OctopError(ErrorCode.NOT_FOUND, "notification not found or already read")
    return {"status": "read"}


@router.post(
    "/{agent_id}/feature-data/reminders/{reminder_id}/complete",
    summary="Complete a feature-data reminder",
)
async def complete_reminder(
    agent_id: str,
    reminder_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    repo = _services(server).repos.feature_data_repo
    reminder = repo.get_reminder(reminder_id)
    if reminder is None or reminder.agent_id != agent_id:
        raise OctopError(ErrorCode.NOT_FOUND, "reminder not found")
    record = _record(repo, agent_id, reminder.record_id)
    _record_read(record, user)
    _record_write(record, row, user, server)
    repo.complete_reminder(reminder_id)
    return {"status": "done"}


@router.get(
    "/{agent_id}/feature-data/principals",
    response_model=PrincipalListResponse,
    summary="List active accounts that can reach this feature",
)
async def list_principals(
    agent_id: str,
    q: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> PrincipalListResponse:
    """Lightweight picker source for user fields and handover targets.

    Every returned account is active and can currently read the feature — the
    same set the server accepts as a handover target — so forms never need the
    user-management endpoints.
    """
    row = _feature_row(agent_id, user, server)
    repos = _services(server).repos
    acl_repo = repos.resource_acl_repo
    needle = q.strip().lower()
    principals: list[dict[str, Any]] = []
    for account in repos.user_repo.list(include_disabled=False):
        if not can_use_feature(
            row,
            user_id=int(account.id),
            acl_repo=acl_repo,
            user_repo=repos.user_repo,
            org_unit_repo=repos.org_unit_repo,
        ):
            continue
        if needle and needle not in str(account.username).lower():
            display = str(account.display_name or "").lower()
            if needle not in display:
                continue
        unit_label = None
        unit_key = getattr(account, "org_unit", None)
        if unit_key:
            unit = repos.org_unit_repo.get(str(unit_key))
            if unit is not None:
                unit_label = {"zh": unit.label_zh, "en": unit.label_en}
        principals.append(
            {
                "id": int(account.id),
                "username": str(account.username),
                "display_name": account.display_name,
                "org_unit": unit_key,
                "org_unit_label": unit_label,
                "role": str(account.role),
            }
        )
    principals.sort(key=lambda item: str(item["username"]))
    total = len(principals)
    window = principals[offset : offset + limit]
    return PrincipalListResponse(principals=window, total=total)


@router.post(
    "/{agent_id}/feature-data/handover",
    summary="Hand a feature's shared records to a new responsible user",
)
async def handover_records(
    agent_id: str,
    body: HandoverBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    """Move one account's shared records to a new responsible user.

    Feature-manager only, target must be active and able to reach the
    feature, and only shared records move: private drafts stay with their
    creator, `created_by` and the audit trail are preserved, revisions advance
    and future notifications follow the new responsible user.
    """
    row = _feature_row(agent_id, user, server)
    repos = _services(server).repos
    try:
        return handover_feature_records(
            repo=repos.feature_data_repo,
            feature_row=row,
            agent_id=agent_id,
            from_user_id=body.from_user_id,
            to_user_id=body.to_user_id,
            actor_user_id=int(user.id),
            user_repo=repos.user_repo,
            org_unit_repo=repos.org_unit_repo,
            acl_repo=repos.resource_acl_repo,
            object_key=body.object_key,
        )
    except PermissionError as exc:
        raise OctopError(ErrorCode.FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc


@router.get(
    "/{agent_id}/feature-data/references/records",
    summary="List authorized record options for reference fields",
)
async def list_reference_targets(
    agent_id: str,
    target_agent_id: str | None = Query(None),
    object_key: str = Query(...),
    q: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> RecordListResponse:
    """Records of the target object the current caller may reference.

    Without ``target_agent_id`` the feature's own records are listed. Every
    option already passes the target feature's ACL and that feature's record
    visibility for this caller.
    """
    _feature_row(agent_id, user, server)
    references = FeatureReferenceService(
        registry=_registry(server), repos=_services(server).repos, user_id=int(user.id)
    )
    try:
        options, total = await references.selector_records(
            agent_id=agent_id,
            target_agent_id=target_agent_id,
            object_key=object_key,
            query=q,
            limit=limit,
            offset=offset,
        )
    except PermissionError as exc:
        raise OctopError(ErrorCode.FORBIDDEN, str(exc)) from exc
    except ValueError as exc:
        raise OctopError(ErrorCode.NOT_FOUND, str(exc)) from exc
    return RecordListResponse(records=options, total=total)


@router.get(
    "/{agent_id}/feature-data/{object_key}/records",
    response_model=RecordListResponse,
    summary="List feature business records",
)
async def list_records(
    agent_id: str,
    object_key: str,
    q: str = Query(""),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    filters: str = Query("", description="JSON array of AND filter conditions"),
    sort_by: str | None = Query(None),
    sort_order: str | None = Query(None),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> RecordListResponse:
    row = _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    schema = await fd.load_schema(workspace)
    if schema.schema is None:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, schema.error or "feature data schema is not configured"
        )
    obj = fd.object_definition(schema.schema, object_key)
    try:
        fragments, params, order_by = fdq.filters_from_request(
            filters, sort_by, sort_order, obj, dialect=_services(server).repos.db.dialect
        )
    except fdq.FilterError as exc:
        raise fdq.filters_error(exc) from exc
    repos = _services(server).repos
    records, total = repos.feature_data_repo.list_records(
        agent_id=agent_id,
        object_key=object_key,
        user_id=user.id,
        query=q,
        limit=limit,
        offset=offset,
        filter_fragments=fragments,
        filter_params=params,
        order_by=order_by,
    )
    references = FeatureReferenceService(
        registry=_registry(server), repos=repos, user_id=int(user.id)
    )
    payloads = []
    for record in records:
        payload = fd.record_payload(
            await fd.record_view(repos.feature_data_repo, workspace, record)
        )
        payload["can_write"] = can_write_record(
            record,
            row,
            user_id=int(user.id),
            user_repo=repos.user_repo,
            org_unit_repo=repos.org_unit_repo,
        )
        payload["references"], payload["values"] = await references.describe_with_values(
            agent_id=agent_id, obj=obj, record=record
        )
        payloads.append(payload)
    return RecordListResponse(records=payloads, total=total)


@router.post(
    "/{agent_id}/feature-data/{object_key}/records",
    summary="Create a feature business record",
)
async def create_record(
    agent_id: str,
    object_key: str,
    body: RecordCreateBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    try:
        record = await fd.create_record(
            _services(server).repos,
            workspace,
            agent_id=agent_id,
            object_key=object_key,
            values=body.values,
            user_id=user.id,
            feature_active=await _active(workspace),
            registry=_registry(server),
            timezone_name=_timezone(server),
        )
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc
    return await _described_payload(record, row=row, server=server, workspace=workspace, user=user)


async def _get_record_or_404(agent_id: str, record_id: str, user: Any, server: OctopServer) -> Any:
    _feature_row(agent_id, user, server)
    record = _record(_services(server).repos.feature_data_repo, agent_id, record_id)
    _record_read(record, user)
    return record


@router.get(
    "/{agent_id}/feature-data/records/{record_id}",
    summary="Read a feature business record",
)
async def get_record(
    agent_id: str,
    record_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    record = await _get_record_or_404(agent_id, record_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    return await _described_payload(record, row=row, server=server, workspace=workspace, user=user)


@router.patch(
    "/{agent_id}/feature-data/records/{record_id}",
    summary="Update a feature business record",
)
async def patch_record(
    agent_id: str,
    record_id: str,
    body: RecordUpdateBody,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    record = _record(_services(server).repos.feature_data_repo, agent_id, record_id)
    _record_read(record, user)
    _record_write(record, row, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    try:
        updated = await fd.update_record(
            _services(server).repos,
            workspace,
            agent_id=agent_id,
            record_id=record_id,
            values=body.values,
            expected_revision=body.expected_revision,
            user_id=user.id,
            registry=_registry(server),
            timezone_name=_timezone(server),
        )
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc
    return await _described_payload(updated, row=row, server=server, workspace=workspace, user=user)


@router.post(
    "/{agent_id}/feature-data/records/{record_id}/publish",
    summary="Publish a private feature record to feature visibility",
)
async def publish_record(
    agent_id: str,
    record_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    repo = _services(server).repos.feature_data_repo
    record = _record(repo, agent_id, record_id)
    _record_read(record, user)
    _record_write(record, row, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    if not await _active(workspace):
        raise OctopError(ErrorCode.FORBIDDEN, "a draft feature cannot publish records")
    updated = fd.publish_record(repo, agent_id=agent_id, record_id=record_id, user_id=user.id)
    return await _described_payload(updated, row=row, server=server, workspace=workspace, user=user)


@router.delete(
    "/{agent_id}/feature-data/records/{record_id}",
    summary="Soft-delete a feature business record",
)
async def remove_record(
    agent_id: str,
    record_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    repo = _services(server).repos.feature_data_repo
    record = _record(repo, agent_id, record_id)
    _record_read(record, user)
    _record_write(record, row, user, server)
    fd.delete_record(repo, agent_id=agent_id, record_id=record_id, user_id=user.id)
    return {"status": "deleted"}


@router.get(
    "/{agent_id}/feature-data/records/{record_id}/history",
    response_model=ChangeListResponse,
    summary="Read a feature record's change history",
)
async def get_history(
    agent_id: str,
    record_id: str,
    limit: int = Query(50, ge=1, le=200),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> ChangeListResponse:
    record = await _get_record_or_404(agent_id, record_id, user, server)
    repos = _services(server).repos
    workspace = await _workspace(agent_id, user, server, write=False)
    changes = repos.feature_data_repo.list_changes(record_id, limit=limit)
    serialized: list[dict[str, Any]] = []
    loaded = await fd.load_schema(workspace)
    references = FeatureReferenceService(
        registry=_registry(server), repos=repos, user_id=int(user.id)
    )
    for change in changes:
        items: list[dict[str, Any]] = list(change.items)
        if loaded.schema is not None:
            obj = fd.object_definition(loaded.schema, record.object_key)
            items = await references.redact_history(agent_id=agent_id, obj=obj, items=items)
        serialized.append(
            {
                "id": change.id,
                "kind": change.kind,
                "summary": change.summary,
                "items": items,
                "user_id": change.user_id,
                "created_at": change.created_at,
            }
        )
    return ChangeListResponse(changes=serialized)


@router.get(
    "/{agent_id}/feature-data/records/{record_id}/files",
    summary="List a feature record's attachment versions",
)
async def list_files(
    agent_id: str,
    record_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    await _get_record_or_404(agent_id, record_id, user, server)
    files = _services(server).repos.feature_data_repo.list_files(record_id)
    return {"files": [fd.file_payload(file) for file in files]}


@router.post(
    "/{agent_id}/feature-data/records/{record_id}/files/{field_key}",
    summary="Upload a new attachment version",
)
async def upload_file(
    agent_id: str,
    record_id: str,
    field_key: str,
    file: UploadFile = File(...),
    note: str = Form(""),
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> dict[str, Any]:
    row = _feature_row(agent_id, user, server)
    repo = _services(server).repos.feature_data_repo
    record = _record(repo, agent_id, record_id)
    _record_read(record, user)
    _record_write(record, row, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    data = await file.read(fd.MAX_FILE_BYTES + 1)
    try:
        saved = await fd.add_file(
            repo,
            workspace,
            agent_id=agent_id,
            record_id=record_id,
            field_key=field_key,
            filename=file.filename or "file",
            content_type=file.content_type or "application/octet-stream",
            data=data,
            note=note,
            user_id=user.id,
        )
    except ValueError as exc:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, str(exc), details={"reason": str(exc)}
        ) from exc
    return fd.file_payload(saved)


@router.get(
    "/{agent_id}/feature-data/records/{record_id}/files/{file_id}/download",
    summary="Download a feature attachment version",
)
async def download_file(
    agent_id: str,
    record_id: str,
    file_id: str,
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> Response:
    await _get_record_or_404(agent_id, record_id, user, server)
    repo = _services(server).repos.feature_data_repo
    file_row = repo.get_file(file_id)
    if file_row is None or file_row.record_id != record_id:
        raise OctopError(ErrorCode.NOT_FOUND, "attachment not found")
    workspace = await _workspace(agent_id, user, server, write=False)
    data = await workspace.adownload_bytes(file_row.storage_path)
    if data is None:
        raise OctopError(ErrorCode.NOT_FOUND, "attachment content not found")
    return Response(
        content=data,
        media_type=file_row.content_type,
        headers={
            "Content-Disposition": content_disposition(file_row.filename),
            "X-Content-SHA256": file_row.sha256,
        },
    )
