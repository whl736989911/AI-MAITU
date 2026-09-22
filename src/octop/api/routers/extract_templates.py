"""HTTP API for extraction templates (design §7).

A template is an enterprise resource rather than a knowledge base's (§7), so the
routes hang off ``/api/extract-templates`` and take no base id.

Reading needs the knowledge module key — an employee sees which template applies
to a file (§7.2) — and writing needs the settings key, because managing
templates is administration. The fine-grained keys this really wants ("manage
extraction templates", "bind extraction templates") are declared for the
permissions workstream and not implemented yet, which is the transition the
parallel-development contract records; the module keys gate it in the meantime.
"""

from __future__ import annotations

from dataclasses import asdict
from typing import Any

from fastapi import APIRouter, Depends, Query, Request, status
from pydantic import BaseModel, ConfigDict, Field

from octop.api.deps import get_server, require_permission

# A template failure is a knowledge-domain failure, so the knowledge router's
# mapping answers for the cases it already knows (not found, forbidden) instead
# of describing the same statuses twice.
from octop.api.routers.knowledge_bases import _map_knowledge_error
from octop.infra.db.repos.extract_templates import (
    ExtractBindingRow,
    ExtractTemplateRow,
    TemplateVersionRow,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.extract_templates import ExtractTemplateService, TemplateInUse
from octop.infra.knowledge.template_match import FIELD_TYPES
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter()


class FieldBody(BaseModel):
    """One field a template asks for (design §7.1)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64, description="Key the value is returned under.")
    type: str = Field(default="text", description="One of: " + ", ".join(FIELD_TYPES) + ".")
    required: bool = Field(default=False, description="Refuse a result missing this field.")
    instruction: str = Field(default="", max_length=2000, description="How to extract it.")
    options: list[str] = Field(
        default_factory=list, description="The values to choose from when type is enum."
    )


class CreateTemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=500)
    fields: list[FieldBody] = Field(default_factory=list)
    instruction: str = Field(
        default="", max_length=8000, description="Overall extraction instruction."
    )
    applies_to: str = Field(
        default="",
        max_length=500,
        description=(
            "File types this template is for, e.g. 'doc, docx, pdf'. Empty means any type. "
            "A file of another type is not given this template even if a binding points at it."
        ),
    )
    note: str = Field(default="", max_length=500, description="Why this first version exists.")


class AddVersionBody(BaseModel):
    """A new version of a template (design §7.3: editing never overwrites)."""

    model_config = ConfigDict(extra="forbid")

    fields: list[FieldBody] = Field(default_factory=list)
    instruction: str = Field(default="", max_length=8000)
    applies_to: str = Field(default="", max_length=500)
    note: str = Field(default="", max_length=500, description="What changed in this version.")


class UpdateTemplateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=500)
    status: str | None = Field(default=None, description="active or disabled.")


class BindBody(BaseModel):
    """Where a template applies (design §7.4)."""

    model_config = ConfigDict(extra="forbid")

    data_source_id: str = Field(min_length=1, description="The folder source this binds to.")
    path: str = Field(
        default="",
        max_length=1000,
        description=(
            "Path inside the source: empty for every file in it, a folder path for its "
            "contents, or one file's path. The most specific binding wins."
        ),
    )
    extension: str = Field(default="", max_length=200, description="e.g. 'pdf' or '.pdf'.")
    mime_type: str = Field(default="", max_length=200, description="Exact type or 'image/*'.")
    name_pattern: str = Field(default="", max_length=200, description="Glob on the file name.")
    match_regex: str = Field(
        default="", max_length=500, description="Regular expression, matched against the path."
    )


def _service(server: OctopServer) -> ExtractTemplateService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "knowledge services are not initialized")
    return ExtractTemplateService(server.services)


def _version_payload(row: TemplateVersionRow) -> dict[str, Any]:
    payload = asdict(row)
    payload["version_id"] = row.id
    payload["fields"] = row.fields
    payload.pop("fields_json", None)
    return payload


def _template_payload(
    row: ExtractTemplateRow,
    *,
    version: TemplateVersionRow | None,
    bindings: int = 0,
) -> dict[str, Any]:
    payload = asdict(row)
    payload["template_id"] = row.id
    payload["bindings"] = bindings
    payload["fields"] = version.fields if version else []
    payload["instruction"] = version.instruction if version else ""
    payload["applies_to"] = version.applies_to if version else ""
    return payload


def _binding_payload(row: ExtractBindingRow) -> dict[str, Any]:
    payload = asdict(row)
    payload["binding_id"] = row.id
    return payload


def _map_template_error(
    exc: Exception, *, locale: str, server: OctopServer | None = None
) -> OctopError:
    if isinstance(exc, OctopError):
        return exc
    if isinstance(exc, TemplateInUse):
        return OctopError(
            ErrorCode.EXTRACT_TEMPLATE_IN_USE,
            str(exc),
            details={"bindings": exc.bindings},
        )
    if isinstance(exc, ValueError):
        return OctopError(
            ErrorCode.EXTRACT_TEMPLATE_INVALID, str(exc), details={"reason": str(exc)}
        )
    return _map_knowledge_error(exc, locale=locale, server=server)


@router.get(
    "/extract-templates",
    summary="List extraction templates",
)
async def list_templates(
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_bases")),
) -> list[dict[str, Any]]:
    try:
        service = _service(server)
        rows = service.list_templates()
        assert server.services is not None
        versions = server.services.extract_templates_repo.current_versions()
        counts = server.services.extract_templates_repo.binding_counts()
        return [
            _template_payload(
                row,
                version=versions.get(row.id),
                bindings=counts.get(row.id, 0),
            )
            for row in rows
        ]
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/extract-templates/resolve",
    summary="Which template applies to a path",
)
async def resolve_template_for_path(
    request: Request,
    data_source_id: str = Query(description="The folder source the path is inside."),
    path: str = Query(description="Path inside that source."),
    content_type: str = Query(default="", description="Optional; derived from the extension."),
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    """Answer design §7.4 for one file, conflicts included.

    Declared before the ``/{template_id}`` routes on purpose: a path parameter
    would otherwise swallow ``resolve`` as an id.
    """
    try:
        match, resolved_type = _service(server).resolve(
            data_source_id=data_source_id, path=path, content_type=content_type
        )
        return {
            "template_id": match.template_id,
            "binding_id": match.binding_id,
            "level": match.level,
            "conflicts": list(match.conflicts),
            "content_type": resolved_type,
        }
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/extract-templates",
    status_code=status.HTTP_201_CREATED,
    summary="Create an extraction template",
)
async def create_template(
    body: CreateTemplateBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        template, version = _service(server).create(
            actor_user_id=user.id,
            name=body.name,
            description=body.description,
            fields=[field.model_dump() for field in body.fields],
            instruction=body.instruction,
            applies_to=body.applies_to,
            note=body.note,
        )
        return _template_payload(template, version=version)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get("/extract-templates/{template_id}", summary="Get an extraction template")
async def get_template(
    template_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        service = _service(server)
        template = service.get(template_id)
        version = service.current_version(template.id)
        return _template_payload(template, version=version)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.patch("/extract-templates/{template_id}", summary="Rename, re-describe, or set status")
async def update_template(
    template_id: str,
    body: UpdateTemplateBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        service = _service(server)
        template = service.update(
            template_id,
            actor_user_id=user.id,
            name=body.name,
            description=body.description,
            status=body.status,
        )
        return _template_payload(template, version=service.current_version(template.id))
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.delete(
    "/extract-templates/{template_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete an extraction template",
)
async def delete_template(
    template_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_settings")),
) -> None:
    try:
        _service(server).delete(template_id)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/extract-templates/{template_id}/versions",
    summary="List a template's versions",
)
async def list_versions(
    template_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_bases")),
) -> list[dict[str, Any]]:
    try:
        return [_version_payload(row) for row in _service(server).list_versions(template_id)]
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/extract-templates/{template_id}/versions",
    status_code=status.HTTP_201_CREATED,
    summary="Record an edit as a new version",
)
async def add_version(
    template_id: str,
    body: AddVersionBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        version = _service(server).add_version(
            template_id,
            actor_user_id=user.id,
            fields=[field.model_dump() for field in body.fields],
            instruction=body.instruction,
            applies_to=body.applies_to,
            note=body.note,
        )
        return _version_payload(version)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/extract-templates/{template_id}/bindings",
    summary="List where a template applies",
)
async def list_bindings(
    template_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_bases")),
) -> list[dict[str, Any]]:
    try:
        return [
            _binding_payload(row) for row in _service(server).list_bindings(template_id=template_id)
        ]
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/extract-templates/{template_id}/bindings",
    status_code=status.HTTP_201_CREATED,
    summary="Bind a template to a source, folder, or file",
)
async def bind_template(
    template_id: str,
    body: BindBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        row = _service(server).bind(
            template_id,
            actor_user_id=user.id,
            data_source_id=body.data_source_id,
            path=body.path,
            extension=body.extension,
            mime_type=body.mime_type,
            name_pattern=body.name_pattern,
            match_regex=body.match_regex,
        )
        return _binding_payload(row)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.delete(
    "/extract-templates/bindings/{binding_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Clear a binding",
)
async def unbind_template(
    binding_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_permission("knowledge_settings")),
) -> None:
    try:
        _service(server).unbind(binding_id)
    except Exception as exc:
        raise _map_template_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
