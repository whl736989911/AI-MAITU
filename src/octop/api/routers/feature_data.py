"""Generic per-feature business data API.

The schema is a workspace document; records and their audit/attachments/reminders
are database rows.  The router deliberately stays thin: every record-shaped rule
(validation, reminder calculation, attachment path, optimistic revision) lives in
:mod:`octop.infra.agents.feature_data`.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, File, Form, Query, Response, UploadFile
from pydantic import BaseModel, ConfigDict, Field

from octop.api.common.agent import (
    AgentCapability,
    feature_can_manage,
    require_agent_row,
)
from octop.api.common.workspace import require_agent_workspace
from octop.api.deps import current_user, get_server
from octop.infra.agents import feature_data as fd
from octop.infra.agents import feature_workflow as wf
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


class SchemaBody(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    schema_data: dict[str, Any] = Field(
        ..., alias="schema", description="Feature business-data object schema"
    )


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


def _feature_row(agent_id: str, user: Any, server: OctopServer) -> Any:
    row = require_agent_row(agent_id, user=user, as_user=None, server=server)
    if not is_feature_agent(str(getattr(row, "kind", ""))):
        raise OctopError(ErrorCode.WORKFLOW_NOT_A_FEATURE, "feature data requires a feature agent")
    return row


def _can_manage(row: Any, user: Any, server: OctopServer) -> bool:
    return bool(feature_can_manage(row, user, server))


def _record_write(record: Any, row: Any, user: Any, server: OctopServer) -> None:
    if record.created_by == user.id:
        return
    if record.visibility == "feature" and _can_manage(row, user, server):
        return
    raise OctopError(ErrorCode.FORBIDDEN, "feature data record is not writable by this user")


def _record_read(record: Any, user: Any) -> None:
    if record.visibility == "private" and record.created_by != user.id:
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
        raise OctopError(ErrorCode.WORKFLOW_INVALID, str(exc)) from exc
    return SchemaResponse.model_validate({"schema": schema})


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
    _record_write(record, row, user, server)
    repo.complete_reminder(reminder_id)
    return {"status": "done"}


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
    server: OctopServer = Depends(get_server),
    user: Any = Depends(current_user),
) -> RecordListResponse:
    _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    schema = await fd.load_schema(workspace)
    if schema.schema is None:
        raise OctopError(
            ErrorCode.WORKFLOW_INVALID, schema.error or "feature data schema is not configured"
        )
    fd.object_definition(schema.schema, object_key)
    records, total = _services(server).repos.feature_data_repo.list_records(
        agent_id=agent_id,
        object_key=object_key,
        user_id=user.id,
        query=q,
        limit=limit,
        offset=offset,
    )
    return RecordListResponse(
        records=[
            fd.record_payload(
                await fd.record_view(_services(server).repos.feature_data_repo, workspace, record)
            )
            for record in records
        ],
        total=total,
    )


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
    _feature_row(agent_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    try:
        record = await fd.create_record(
            _services(server).repos.feature_data_repo,
            workspace,
            agent_id=agent_id,
            object_key=object_key,
            values=body.values,
            user_id=user.id,
            feature_active=await _active(workspace),
        )
    except ValueError as exc:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, str(exc)) from exc
    return fd.record_payload(
        await fd.record_view(_services(server).repos.feature_data_repo, workspace, record)
    )


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
    record = await _get_record_or_404(agent_id, record_id, user, server)
    workspace = await _workspace(agent_id, user, server, write=False)
    return fd.record_payload(
        await fd.record_view(_services(server).repos.feature_data_repo, workspace, record)
    )


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
            _services(server).repos.feature_data_repo,
            workspace,
            agent_id=agent_id,
            record_id=record_id,
            values=body.values,
            expected_revision=body.expected_revision,
            user_id=user.id,
        )
    except ValueError as exc:
        raise OctopError(ErrorCode.WORKFLOW_INVALID, str(exc)) from exc
    return fd.record_payload(
        await fd.record_view(_services(server).repos.feature_data_repo, workspace, updated)
    )


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
    return fd.record_payload(await fd.record_view(repo, workspace, updated))


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
    await _get_record_or_404(agent_id, record_id, user, server)
    changes = _services(server).repos.feature_data_repo.list_changes(record_id, limit=limit)
    return ChangeListResponse(
        changes=[
            {
                "id": change.id,
                "kind": change.kind,
                "summary": change.summary,
                "items": change.items,
                "user_id": change.user_id,
                "created_at": change.created_at,
            }
            for change in changes
        ]
    )


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
        raise OctopError(ErrorCode.WORKFLOW_INVALID, str(exc)) from exc
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
            "Content-Disposition": f'attachment; filename="{file_row.filename}"',
            "X-Content-SHA256": file_row.sha256,
        },
    )
