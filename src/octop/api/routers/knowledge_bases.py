"""HTTP API for private, shareable knowledge bases."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Collection
from contextlib import suppress
from dataclasses import asdict
from functools import partial
from typing import Any, Literal

from fastapi import (
    APIRouter,
    Depends,
    File,
    Form,
    HTTPException,
    Query,
    Request,
    UploadFile,
    status,
)
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from octop.api.common.content_disposition import content_disposition
from octop.api.common.upload_limit import read_upload_capped
from octop.api.deps import get_server, require_any_permission, require_permission
from octop.config import DEFAULT_MAX_UPLOAD_MB, MAX_MAX_UPLOAD_MB, upload_mb_to_bytes
from octop.infra.agents.providers.model_flags import (
    is_chat_eligible_model,
    is_embedding_model,
    is_onnx_local_provider,
    is_vision_model,
)
from octop.infra.agents.providers.onnx_catalog import (
    ONNX_PRESET_MODEL_IDS,
    list_onnx_catalog_models,
)
from octop.infra.agents.providers.onnx_service import (
    DOWNLOAD_MANAGER,
    OnnxServiceConfig,
    assert_catalog_model,
    ensure_local_embedding_deps_async,
    is_model_downloaded,
    probe_local_model,
    save_config,
    status_payload,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.files import document_path
from octop.infra.knowledge.gate import (
    assert_knowledge_usable,
    get_capability,
    set_feature_enabled,
)
from octop.infra.knowledge.jobs import enqueue_index_document, reindex_all_documents
from octop.infra.knowledge.legacy_office import (
    LegacyConversionFailed,
    LegacyConversionUnavailable,
)
from octop.infra.knowledge.ocr import (
    OCR_IMAGE_SUFFIXES,
    ensure_ocr_deps_async,
    get_ocr_capability,
    set_ocr_settings,
    validate_ocr_settings,
)
from octop.infra.knowledge.parse import PasswordRequiredError
from octop.infra.knowledge.processors import ARCHIVE_REASONS, ArchiveReason
from octop.infra.knowledge.search import DEFAULT_SEARCH_K, SearchHit
from octop.infra.knowledge.service import (
    ACCESS_WRITE,
    MAX_DOCS_PER_KB,
    MAX_DOCUMENT_BYTES,
    KnowledgeAccessDenied,
    KnowledgeService,
    knowledge_supported_extensions,
)
from octop.infra.server import OctopServer
from octop.infra.users.identity import User
from octop.infra.utils.locale import resolve_request_locale

router = APIRouter(prefix="/knowledge-bases")
logger = logging.getLogger(__name__)

#: Module keys that open the knowledge-base page, in the dashboard's own order.
#: The page's route gate is the same pair (``PERM.knowledgeBasesPage``,
#: ``dashboard/src/utils/permissions.ts``): ``knowledge_bases`` reaches the module
#: itself and ``knowledge_settings`` reaches it as the knowledge tab of the app
#: settings, so both render the page. Every read the page makes therefore accepts
#: either — gating them on ``knowledge_bases`` alone is what let
#: ``knowledge_settings`` render the page and then get 403 from the page's own
#: list and search. Writes are narrower and keep their single key
#: (``knowledge_bases`` for the base and its documents, ``knowledge_settings`` for
#: the embedding/OCR settings).
KNOWLEDGE_PAGE_KEYS = ("knowledge_bases", "knowledge_settings")

_TEXT_DOC_MAX_LENGTH = upload_mb_to_bytes(MAX_MAX_UPLOAD_MB)


class KnowledgeCoverage(BaseModel):
    total: int = Field(description="Readable file count, including files without searchable text.")
    searchable: int = Field(description="Readable files with a current text index.")
    pending: int = Field(description="Readable files waiting for or undergoing indexing.")
    indexing: int = Field(description="Subset of pending files actively queued or processing.")
    failed: int = Field(description="Readable files whose extraction failed or needs a password.")
    unsupported: int = Field(description="Readable files archived without searchable extraction.")


class KnowledgeSegmentEvidence(BaseModel):
    document_id: str
    segment_id: str
    filename: str
    text: str = Field(
        description="Verified original excerpt; empty when the original cannot be checked."
    )
    locator: dict[str, Any] = Field(description="Position in the original format.")
    version: str = Field(description="Content hash of the indexed original.")
    verified: bool
    reason: str | None = Field(description="stale or source_unavailable when verification failed.")


class KnowledgePreviewBlock(BaseModel):
    ordinal: int = Field(description="Zero-based position inside the document index.")
    text: str = Field(description="Segment text exactly as indexed.")
    kind: str = Field(description="Parser block kind, such as line, row, or page.")
    heading: str = Field(description="Enclosing heading when the parser recorded one.")
    locator: dict[str, Any] = Field(
        description="Source position, such as {line}, {page}, {slide} or {sheet, row}."
    )


class KnowledgeDocumentPreview(BaseModel):
    id: str = Field(description="Public document identifier.")
    filename: str
    title: str = Field(description="Document title, with a filename fallback.")
    pages: int | None = Field(description="Page count when provided by the document format.")
    sections: list[str] = Field(description="Headings extracted during indexing.")
    text: str = Field(description="Extracted text, limited to 200,000 characters.")
    blocks: list[KnowledgePreviewBlock] = Field(
        default_factory=list,
        description=(
            "Indexed segments in reading order, each with the source position it came "
            "from. Empty for previews read directly from an unindexed text file."
        ),
    )


class FeatureBody(BaseModel):
    enabled: bool = Field(description="Whether to enable the instance-wide knowledge-base feature.")
    model: str | None = Field(
        default=None,
        description="Optional embedding model ID; leave blank for lexical-only search.",
    )
    backend: str | None = Field(default=None, pattern="^(onnx|remote)$")
    provider_id: str | None = None
    ocr_enabled: bool | None = Field(
        default=None,
        description="Whether OCR is enabled for images and scanned PDFs.",
    )
    ocr_backend: str | None = Field(default=None, pattern="^(onnx|remote)$")
    ocr_model: str | None = None
    ocr_provider_id: str | None = None


class OnnxDownloadBody(BaseModel):
    model: str = Field(min_length=1, description="Catalog ONNX embedding model id to download.")


class CreateFolderBody(BaseModel):
    path: str = Field(min_length=1, max_length=500, description="Relative folder path.")


class CreateTextDocumentBody(BaseModel):
    name: str = Field(
        min_length=1, max_length=200, description="File name without or with extension."
    )
    format: str = Field(pattern="^(md|txt)$", description="Text format: md or txt.")
    content: str = Field(default="", max_length=_TEXT_DOC_MAX_LENGTH)
    path: str | None = Field(
        default=None,
        max_length=500,
        description="Optional parent folder path (without filename).",
    )
    access_scope: Literal["company", "department", "personal", "authorized"] | None = Field(
        default=None, description="Optional initial access scope; omitted selects the maximum."
    )
    access_permission: Literal["read", "write"] | None = Field(
        default=None, description="Required with access_scope."
    )
    access_unit_key: str | None = Field(
        default=None, max_length=64, description="Required for department scope."
    )


class UpdateTextDocumentBody(BaseModel):
    content: str = Field(max_length=_TEXT_DOC_MAX_LENGTH)


class MoveDocumentBody(BaseModel):
    target_folder: str = Field(
        default="",
        max_length=1024,
        description=(
            "Destination folder path relative to the knowledge base; "
            "empty string moves the document to the root."
        ),
    )


class RenameDocumentBody(BaseModel):
    new_name: str = Field(min_length=1, max_length=255, description="New document or folder name.")


class UpdateBaseBody(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    description: str | None = Field(default=None, max_length=2000)
    default_open: bool | None = None
    shared: bool | None = None
    icon_name: str | None = Field(default=None, max_length=64)
    max_documents: int | None = Field(
        default=None,
        ge=0,
        le=10_000,
        description="Per-base document limit. 0 = unlimited.",
    )


def _knowledge_service(server: OctopServer) -> KnowledgeService:
    if server.services is None:
        raise OctopError(ErrorCode.INTERNAL_ERROR, "knowledge services are not initialized")
    return KnowledgeService(server.services)


def _row_payload(row: Any, *, has_original: bool | None = None) -> dict[str, Any]:
    payload = asdict(row)
    # The stored structure (design §3.4) is a cache, not an API value: shipping
    # it raw would send every table twice — the text already carries them — so
    # the row reports the one field of it a reader asks for.
    payload.pop("derived_json", None)
    payload["document_id"] = row.id
    payload["title"] = getattr(row, "title", "")
    if has_original is None:
        if getattr(row, "is_dir", False):
            has_original = False
        else:
            has_original = document_path(row.kb_id, row.id, row.filename).is_file()
    payload["has_original"] = bool(has_original)
    archive_reason = getattr(row, "error_message", None)
    payload["archive_reason"] = (
        archive_reason
        if getattr(row, "status", None) == "archive_only" and archive_reason in ARCHIVE_REASONS
        else None
    )
    return payload


def _owner_fields(server: OctopServer, owner_user_id: int | None) -> dict[str, str | None]:
    """Owner display fields; a NULL owner is the system, which has no name.

    The enterprise space is system-owned (schema v27), so this is a real case
    and not a defensive branch: ``None`` is nobody's user id.
    """
    if owner_user_id is None:
        return {"owner_username": None, "owner_display_name": None}
    if server.services is None:
        return {"owner_username": None, "owner_display_name": None}
    user_repo = getattr(server.services, "user_repo", None)
    if user_repo is None:
        return {"owner_username": None, "owner_display_name": None}
    owner = user_repo.get(int(owner_user_id))
    if owner is None:
        return {"owner_username": None, "owner_display_name": None}
    return {
        "owner_username": owner.username,
        "owner_display_name": owner.display_name or owner.username,
    }


def _public_base_ids(server: OctopServer, kb_ids: Collection[str]) -> set[str]:
    """Ids in *kb_ids* published to everyone, from ``resource_acl``."""
    services = getattr(server, "services", None)
    repo = getattr(services, "knowledge_repo", None) if services is not None else None
    return repo.public_base_ids(kb_ids) if repo is not None else set()


def _base_payload(
    server: OctopServer, row: Any, *, public_ids: Collection[str] | None = None
) -> dict[str, Any]:
    """One knowledge base as the dashboard sees it.

    ``shared`` is the published-to-everyone flag and comes from
    ``resource_acl`` — schema v21 dropped the column the row used to carry, and
    the dashboard reads this field to render the share state.
    """
    payload = asdict(row)
    payload["knowledge_base_id"] = row.id
    if public_ids is None:
        public_ids = _public_base_ids(server, [row.id])
    payload["shared"] = row.id in public_ids
    return {**payload, **_owner_fields(server, row.owner_user_id)}


def _is_admin(user: User) -> bool:
    return bool(user.is_admin)


def _max_upload_mb(server: OctopServer) -> int:
    config = getattr(getattr(server, "services", None), "config", None)
    value = getattr(config, "max_upload_mb", None)
    if isinstance(value, int) and value > 0:
        return value
    return DEFAULT_MAX_UPLOAD_MB


def _max_upload_bytes(server: OctopServer) -> int:
    config = getattr(getattr(server, "services", None), "config", None)
    value = getattr(config, "max_upload_bytes", None)
    if isinstance(value, int) and value > 0:
        return value
    return MAX_DOCUMENT_BYTES


def _map_knowledge_error(
    exc: Exception, *, locale: str, server: OctopServer | None = None
) -> OctopError:
    if isinstance(exc, OctopError):
        return exc
    if isinstance(exc, (LookupError, FileNotFoundError)):
        return OctopError.localized(ErrorCode.KNOWLEDGE_NOT_FOUND, locale)
    if isinstance(exc, KnowledgeAccessDenied):
        # Read and edit are separate permissions (design §5.1): an actor who may
        # already open the base must not be told they have *no access* to it when
        # what they lack is the right to change it.
        return OctopError.localized(
            ErrorCode.KNOWLEDGE_WRITE_FORBIDDEN
            if exc.access == ACCESS_WRITE
            else ErrorCode.KNOWLEDGE_FORBIDDEN,
            locale,
        )
    if isinstance(exc, PermissionError):
        return OctopError.localized(ErrorCode.KNOWLEDGE_FORBIDDEN, locale)
    # Checked before the RuntimeError text heuristics below: the conversion
    # messages say "install", which would otherwise be read as an embedding
    # prerequisite and tell an administrator to configure the wrong thing.
    if isinstance(exc, PasswordRequiredError):
        return OctopError.localized(ErrorCode.KNOWLEDGE_PASSWORD_REQUIRED, locale)
    if isinstance(exc, (LegacyConversionUnavailable, LegacyConversionFailed)):
        return OctopError.localized(
            ErrorCode.KNOWLEDGE_CONVERSION_FAILED,
            locale,
            details={"reason": str(exc)},
        )
    text = str(exc).lower()
    if isinstance(exc, RuntimeError):
        if "disabled" in text:
            return OctopError.localized(ErrorCode.KNOWLEDGE_FEATURE_DISABLED, locale)
        if any(
            kw in text
            for kw in (
                "prerequisite",
                "embedding",
                "ocr",
                "install",
                "component",
                "provider",
            )
        ):
            return OctopError.localized(ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED, locale)
    if "at most 100" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_DOC_LIMIT, locale)
    if "document size exceeds" in text:
        max_mb = _max_upload_mb(server) if server is not None else DEFAULT_MAX_UPLOAD_MB
        return OctopError.localized(
            ErrorCode.KNOWLEDGE_DOC_TOO_LARGE,
            locale,
            details={"max_mb": max_mb},
            max_mb=max_mb,
        )
    if "at most" in text and "knowledge bases" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_BASE_LIMIT, locale)
    if "unsupported knowledge document content type" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_UNSUPPORTED_TYPE, locale)
    if "knowledge ocr" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED, locale)
    if "unique constraint" in text or "duplicate key" in text or "already exists" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_NAME_TAKEN, locale)
    if "invalid knowledge document name" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_NAME_INVALID, locale)
    if "prerequisite" in text or "embedding model" in text or "embedding_model" in text:
        return OctopError.localized(ErrorCode.KNOWLEDGE_PREREQUISITES_FAILED, locale)
    logger.exception("unhandled error in knowledge base router: %s", exc)
    return OctopError.localized(
        ErrorCode.INTERNAL_ERROR,
        locale,
        details={"cause": str(exc)},
    )


def _onnx_options_for_knowledge(*, include_all: bool = False) -> list[dict[str, Any]]:
    """Recommended ONNX models, or the full catalog when *include_all* is set."""
    catalog = list_onnx_catalog_models()
    if include_all:
        models = catalog
    else:
        by_id = {str(model["id"]): model for model in catalog}
        models = [by_id[model_id] for model_id in ONNX_PRESET_MODEL_IDS if model_id in by_id]
    return [
        {
            **model,
            "downloaded": is_model_downloaded(str(model["id"])),
            "recommended": bool(model.get("recommended")),
        }
        for model in models
    ]


def _enable_onnx_service(server: OctopServer, model: str) -> None:
    """Turn on the local ONNX service for a downloaded catalog model."""
    assert server.services is not None
    verified = assert_catalog_model(model)
    if not is_model_downloaded(verified):
        raise RuntimeError("ONNX embedding model is not configured or not downloaded")
    save_config(
        server.services.settings_repo.set,
        OnnxServiceConfig(enabled=True, model=verified),
    )


def _require_usable(server: OctopServer, request: Request) -> None:
    assert server.services is not None
    try:
        assert_knowledge_usable(server.services.settings_repo.get, server.services.provider_repo)
    except (RuntimeError, ValueError) as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


def _capability_payload(server: OctopServer) -> dict[str, Any]:
    assert server.services is not None
    payload = get_capability(server.services.settings_repo.get, server.services.provider_repo)
    payload["ocr"] = get_ocr_capability(
        server.services.settings_repo.get, server.services.provider_repo
    )
    payload["limits"] = {
        "max_docs_per_kb": MAX_DOCS_PER_KB,
        "max_document_bytes": _max_upload_bytes(server),
    }
    return payload


class ProcessorCapability(BaseModel):
    id: str
    available: bool
    extensions: list[str]
    reason: ArchiveReason | None = None


class ProcessorCapabilities(BaseModel):
    archive_only: bool
    processors: list[ProcessorCapability]


@router.get(
    "/processors",
    response_model=ProcessorCapabilities,
    summary="List file processor capabilities",
)
async def processor_capabilities(
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    assert server.services is not None
    from octop.infra.knowledge.legacy_office import find_libreoffice
    from octop.infra.knowledge.processors.libredwg import capability

    supported = set(knowledge_supported_extensions())
    legacy_extensions = {".doc", ".ppt"}
    image_extensions = set(OCR_IMAGE_SUFFIXES)
    legacy_available = find_libreoffice() is not None
    ocr_available = get_ocr_capability(
        server.services.settings_repo.get, server.services.provider_repo
    )["usable"]
    cad = capability()
    return {
        "archive_only": True,
        "processors": [
            {
                "id": "built_in",
                "available": True,
                "extensions": sorted(supported - legacy_extensions - image_extensions),
                "reason": None,
            },
            {
                "id": "libreoffice",
                "available": legacy_available,
                "extensions": sorted(legacy_extensions),
                "reason": None if legacy_available else "legacy_office_unavailable",
            },
            {
                "id": "ocr",
                "available": bool(ocr_available),
                "extensions": sorted(image_extensions),
                "reason": None if ocr_available else "ocr_disabled",
            },
            {
                "id": cad["id"],
                "available": cad["available"],
                "extensions": [".dwg"],
                "reason": None if cad["available"] else "cad_unavailable",
            },
        ],
    }


@router.get("/capability", summary="Get knowledge-base feature capability")
async def capability(
    server: OctopServer = Depends(get_server),
    _user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    return _capability_payload(server)


@router.get("/embedding-options", summary="List knowledge embedding backend options")
async def embedding_options(
    all_onnx: bool = Query(
        False,
        description="Include the full local ONNX catalog instead of the three recommended models",
    ),
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    assert server.services is not None
    remote = []
    for provider in server.services.provider_repo.list_all():
        if is_onnx_local_provider(
            provider.name, provider_api_key=getattr(provider, "api_key", None)
        ):
            continue
        models = [
            {"id": str(model["id"]), "name": str(model.get("name") or model["id"])}
            for model in provider.get_models()
            if str(model.get("id") or "").strip()
            and is_embedding_model(
                model,
                provider_name=provider.name,
                provider_api_key=getattr(provider, "api_key", None),
            )
        ]
        if models:
            remote.append(
                {"provider_id": str(provider.id), "provider_name": provider.name, "models": models}
            )
    return {
        "onnx": _onnx_options_for_knowledge(include_all=all_onnx is True),
        "remote": remote,
    }


@router.get("/ocr-options", summary="List knowledge OCR backend options")
async def ocr_options(
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    assert server.services is not None
    remote = []
    for provider in server.services.provider_repo.list_all():
        if not provider.enabled or not provider.api_key or not provider.base_url:
            continue
        models = [
            {"id": str(model["id"]), "name": str(model.get("name") or model["id"])}
            for model in provider.get_models()
            if str(model.get("id") or "").strip()
            and is_chat_eligible_model(
                model,
                provider_name=provider.name,
                provider_api_key=provider.api_key,
            )
            and is_vision_model(model)
        ]
        if models:
            remote.append(
                {"provider_id": str(provider.id), "provider_name": provider.name, "models": models}
            )
    return {
        "local": {"id": "rapidocr", "name": "RapidOCR (ONNX)"},
        "remote": remote,
    }


@router.put("/feature", summary="Enable or disable knowledge bases")
async def put_feature(
    body: FeatureBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    _admin: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    assert server.services is not None
    previous = get_capability(server.services.settings_repo.get, server.services.provider_repo)
    previous_ocr = get_ocr_capability(
        server.services.settings_repo.get, server.services.provider_repo
    )
    selected_backend = (body.backend or "onnx").strip().lower()
    if body.enabled and body.model and selected_backend == "onnx":
        try:
            await ensure_local_embedding_deps_async(allow_install=True)
        except RuntimeError as exc:
            raise _map_knowledge_error(
                exc, locale=resolve_request_locale(request), server=server
            ) from exc
    if body.ocr_enabled:
        try:
            await ensure_ocr_deps_async(backend=(body.ocr_backend or "onnx").strip().lower())
            validate_ocr_settings(
                enabled=True,
                backend=body.ocr_backend,
                model=body.ocr_model,
                provider_id=body.ocr_provider_id,
                provider_repo=server.services.provider_repo,
            )
        except RuntimeError as exc:
            raise _map_knowledge_error(
                exc, locale=resolve_request_locale(request), server=server
            ) from exc
        except ValueError as exc:
            raise _map_knowledge_error(
                exc, locale=resolve_request_locale(request), server=server
            ) from exc
    try:
        set_feature_enabled(
            server.services.settings_repo.get,
            server.services.settings_repo.set,
            enabled=body.enabled,
            backend=body.backend,
            model=body.model,
            provider_id=body.provider_id,
            provider_repo=server.services.provider_repo,
        )
        if body.ocr_enabled is not None:
            set_ocr_settings(
                server.services.settings_repo.set,
                enabled=body.ocr_enabled,
                backend=body.ocr_backend,
                model=body.ocr_model,
                provider_id=body.ocr_provider_id,
                provider_repo=server.services.provider_repo,
            )
    except (RuntimeError, ValueError) as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
    capability = _capability_payload(server)
    if body.enabled and selected_backend == "onnx":
        selected_model = str(capability.get("selected_model") or "").strip()
        if selected_model:
            with suppress(RuntimeError, ValueError):
                _enable_onnx_service(server, selected_model)
    ocr_changed = any(
        previous_ocr.get(key) != capability["ocr"].get(key)
        for key in ("enabled", "backend", "model", "provider_id")
    )
    if body.enabled and (
        not previous["feature_enabled"]
        or previous["selected_model"] != capability["selected_model"]
        or previous["backend"] != capability["backend"]
        or previous["provider_id"] != capability["provider_id"]
        or ocr_changed
    ):
        reindex_all_documents(server.services, capability["selected_model"])
    return capability


@router.post("/onnx-download", summary="Download a local ONNX embedding model")
async def start_onnx_download(
    body: OnnxDownloadBody,
    request: Request,
    _: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        state = await DOWNLOAD_MANAGER.start_download(body.model.strip())
    except (RuntimeError, ValueError) as exc:
        raise _map_knowledge_error(exc, locale=resolve_request_locale(request)) from exc
    return state.to_dict()


@router.get("/onnx-download-status", summary="Poll ONNX embedding model download progress")
async def onnx_download_status(
    _: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    return DOWNLOAD_MANAGER.state.to_dict()


@router.post("/onnx-activate", summary="Enable local ONNX service for a downloaded model")
async def activate_onnx_service(
    body: OnnxDownloadBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    _: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    try:
        await ensure_local_embedding_deps_async(allow_install=True)
        _enable_onnx_service(server, body.model.strip())
    except (RuntimeError, ValueError) as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
    assert server.services is not None
    return status_payload(server.services.settings_repo.get, DOWNLOAD_MANAGER.state)


@router.post("/onnx-test", summary="Probe the selected local ONNX embedding model")
async def test_onnx_model(
    body: OnnxDownloadBody,
    _: User = Depends(require_permission("knowledge_settings")),
) -> dict[str, Any]:
    """Verify the model actually embeds, on-device.

    Mirrors ``/onnx-models/test`` for the knowledge-settings role: that one is
    gated on ``onnx_models``, which a knowledge-settings admin need not hold.
    """
    return await probe_local_model(body.model.strip())


@router.get("", summary="List visible knowledge bases")
async def list_bases(
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> list[dict[str, Any]]:
    bases = _knowledge_service(server).list_visible_bases(actor_user_id=user.id)
    public_ids = _public_base_ids(server, [base.id for base in bases])
    return [_base_payload(server, base, public_ids=public_ids) for base in bases]


@router.get("/enterprise", summary="Get the enterprise knowledge space")
async def enterprise_space(
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    """The deployment's one logical knowledge base (design §1, §11).

    There is no create route beside this one on purpose: a user creating a
    knowledge base is the model the design replaced. The space is read-checked
    like any other base, so which callers may see it stays a rule rather than a
    property of this route.
    """
    service = _knowledge_service(server)
    try:
        space = service.enterprise_space()
        return _base_payload(
            server,
            service.get_readable_base(space.id, actor_user_id=user.id, is_admin=_is_admin(user)),
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get("/default-open", summary="List current user's default-open knowledge-base IDs")
async def default_open_bases(
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, list[str]]:
    bases = _knowledge_service(server).list_visible_bases(actor_user_id=user.id)
    return {
        "knowledge_base_ids": [
            base.id
            for base in bases
            if base.default_open and int(base.owner_user_id) == int(user.id)
        ]
    }


@router.get("/{kb_id}", summary="Get a visible knowledge base")
async def get_base(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    try:
        return _base_payload(
            server,
            _knowledge_service(server).get_readable_base(
                kb_id, actor_user_id=user.id, is_admin=_is_admin(user)
            ),
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.patch("/{kb_id}", summary="Update knowledge-base settings")
async def update_base(
    kb_id: str,
    body: UpdateBaseBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        return _base_payload(
            server,
            _knowledge_service(server).update_base(
                kb_id,
                actor_user_id=user.id,
                name=body.name.strip() if body.name is not None else None,
                description=body.description.strip() if body.description is not None else None,
                default_open=body.default_open,
                shared=body.shared,
                icon_name=body.icon_name.strip() if body.icon_name is not None else None,
                max_documents=body.max_documents,
                is_admin=_is_admin(user),
            ),
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.delete(
    "/{kb_id}", status_code=status.HTTP_204_NO_CONTENT, summary="Delete a knowledge base"
)
async def delete_base(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> None:
    try:
        _knowledge_service(server).delete_base(
            kb_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get("/{kb_id}/documents", summary="List knowledge-base documents")
async def list_documents(
    kb_id: str,
    request: Request,
    prefix: str | None = Query(
        default=None,
        description="When set, only immediate children of this relative folder path.",
    ),
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> list[dict[str, Any]]:
    try:
        return [
            _row_payload(document)
            for document in _knowledge_service(server).list_documents(
                kb_id, actor_user_id=user.id, is_admin=_is_admin(user), prefix=prefix
            )
        ]
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/{kb_id}/folders",
    status_code=status.HTTP_201_CREATED,
    summary="Create a knowledge-base folder",
)
async def create_folder(
    kb_id: str,
    body: CreateFolderBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        folder = _knowledge_service(server).create_folder(
            kb_id, actor_user_id=user.id, path=body.path, is_admin=_is_admin(user)
        )
        return _row_payload(folder)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/{kb_id}/documents/text",
    status_code=status.HTTP_201_CREATED,
    summary="Create a markdown or plain-text knowledge document",
)
async def create_text_document(
    kb_id: str,
    body: CreateTextDocumentBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        _require_usable(server, request)
        document = _knowledge_service(server).create_text_document(
            kb_id,
            actor_user_id=user.id,
            name=body.name,
            format=body.format,
            content=body.content,
            is_admin=_is_admin(user),
            path=body.path,
            access_scope=body.access_scope,
            access_permission=body.access_permission,
            access_unit_key=body.access_unit_key,
        )
        assert server.services is not None
        enqueue_index_document(server.services, kb_id, document.id)
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(exc, locale=locale, server=server) from exc


@router.post(
    "/{kb_id}/documents", status_code=status.HTTP_201_CREATED, summary="Upload a knowledge document"
)
async def upload_document(
    kb_id: str,
    request: Request,
    upload: UploadFile = File(
        ...,
        description=(
            "Original file of any format, including archive-only formats. Available processors "
            "extract searchable content; files without an enabled processor remain archive-only "
            "and downloadable under their ACL. DOC/PPT use optional LibreOffice, DWG uses "
            "optional LibreDWG, and images require enabled OCR for indexing. See /processors."
        ),
    ),
    path: str | None = Form(
        default=None,
        description="Optional relative path including filename (for nested folders).",
    ),
    access_scope: str | None = Form(
        default=None,
        description="Optional initial access scope: company | department | personal | authorized.",
    ),
    access_permission: str | None = Form(
        default=None,
        description="Required with access_scope: read | write.",
    ),
    access_unit_key: str | None = Form(
        default=None,
        description="Required for department scope: the offered unit key.",
    ),
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        _require_usable(server, request)
        content = await read_upload_capped(
            upload,
            max_bytes=_max_upload_bytes(server),
            code=ErrorCode.KNOWLEDGE_DOC_TOO_LARGE,
        )
        document = _knowledge_service(server).upload_document(
            kb_id,
            actor_user_id=user.id,
            filename=upload.filename or "",
            content_type=upload.content_type or "",
            content=content,
            is_admin=_is_admin(user),
            path=path or upload.filename or "",
            access_scope=access_scope,
            access_permission=access_permission,
            access_unit_key=access_unit_key,
        )
        assert server.services is not None
        enqueue_index_document(server.services, kb_id, document.id)
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(exc, locale=locale, server=server) from exc


@router.get(
    "/{kb_id}/coverage",
    response_model=KnowledgeCoverage,
    summary="Get readable file and searchable text coverage",
)
async def knowledge_coverage(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> KnowledgeCoverage:
    try:
        service = _knowledge_service(server)
        data = await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                service.coverage,
                kb_id,
                actor_user_id=user.id,
                is_admin=_is_admin(user),
            ),
        )
        return KnowledgeCoverage(**data)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/{kb_id}/documents/{doc_id}/segments/{segment_id}",
    response_model=KnowledgeSegmentEvidence,
    summary="Read a source-located excerpt and verify the original version",
)
async def read_knowledge_segment(
    kb_id: str,
    doc_id: str,
    segment_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> KnowledgeSegmentEvidence:
    """Recheck file ACL and source bytes; stale or offline originals yield no text."""
    try:
        service = _knowledge_service(server)
        data = await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                service.read_segment,
                kb_id,
                doc_id,
                segment_id,
                actor_user_id=user.id,
                is_admin=_is_admin(user),
            ),
        )
        return KnowledgeSegmentEvidence.model_validate(data)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/{kb_id}/search",
    response_model=list[SearchHit],
    summary="Search authorized file names and existing located document text",
)
async def search_documents(
    kb_id: str,
    request: Request,
    q: str = Query(min_length=1, max_length=200, description="What to look for."),
    limit: int = Query(
        default=DEFAULT_SEARCH_K, ge=1, le=50, description="Maximum hits to return."
    ),
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> list[SearchHit]:
    """Find authorized files and indexed excerpts without personal search policies."""
    try:
        service = _knowledge_service(server)
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                service.search,
                kb_id=kb_id,
                actor_user_id=user.id,
                query=q,
                limit=limit,
                is_admin=_is_admin(user),
                generate_query_vector=True,
            ),
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/{kb_id}/documents/{doc_id}/preview",
    summary="Preview extracted document text",
    response_model=KnowledgeDocumentPreview,
    responses={
        409: {
            "description": (
                "KNOWLEDGE_INDEX_NOT_READY: the current document index is unfinished, "
                "unavailable, or stale; this request does not run parsing or OCR."
            )
        }
    },
    description=(
        "Returns bounded text from the completed index without repeating OCR. "
        "Unindexed markdown/text files can be previewed directly; other documents "
        "return 409 until their current version is indexed."
    ),
)
async def preview_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    try:
        return await asyncio.get_running_loop().run_in_executor(
            None,
            partial(
                _knowledge_service(server).preview_document,
                kb_id,
                doc_id,
                actor_user_id=user.id,
                is_admin=_is_admin(user),
            ),
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.get(
    "/{kb_id}/documents/{doc_id}/file",
    summary="Download or inline-open an authorized original from storage or a data source",
    response_class=FileResponse,
)
async def download_document_file(
    kb_id: str,
    doc_id: str,
    request: Request,
    disposition: str = Query(
        "attachment",
        pattern="^(attachment|inline)$",
        description="Content-Disposition type: attachment (download) or inline (preview).",
    ),
    version: str | None = Query(
        None,
        min_length=64,
        max_length=64,
        description=(
            "Optional SHA-256 of the required original bytes. The actual file is "
            "verified and snapshotted; a changed version returns HTTP 409."
        ),
    ),
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> FileResponse:
    from octop.infra.knowledge.source_access import KnowledgeSourceService

    assert server.services is not None
    source = KnowledgeSourceService(server.services)
    original = (
        source.open_original_version(
            kb_id,
            doc_id,
            expected_version=version,
            actor_user_id=user.id,
            is_admin=_is_admin(user),
        )
        if version is not None
        else source.open_original(kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user))
    )
    try:
        metadata = source.describe_document(
            kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        path = await asyncio.get_running_loop().run_in_executor(None, original.__enter__)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
    return FileResponse(
        path=path,
        media_type=metadata["content_type"] or "application/octet-stream",
        headers={
            "Content-Disposition": content_disposition(
                metadata["filename"], disposition=disposition
            )
        },
        background=BackgroundTask(original.__exit__, None, None, None),
    )


@router.get(
    "/{kb_id}/documents/{doc_id}/render",
    summary="Render a CAD drawing to an inline PNG preview",
    response_class=FileResponse,
    description=(
        "Converts a DWG/DXF original with the configured external converter and returns a "
        "PNG for inline display. The image is cached per document content version, so an "
        "updated original renders again. A host without the converter answers 415 and a "
        "failed conversion answers 502; the original download and the text preview keep "
        "working in both cases."
    ),
)
async def render_document_preview(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> FileResponse:
    from octop.infra.knowledge import cad_preview
    from octop.infra.knowledge.source_access import KnowledgeSourceService

    assert server.services is not None
    source = KnowledgeSourceService(server.services)
    original = source.open_original(kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user))
    loop = asyncio.get_running_loop()
    try:
        metadata = source.describe_document(
            kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        if not cad_preview.supports(str(metadata.get("filename") or "")):
            raise HTTPException(status_code=415, detail="this document has no CAD preview")
        row = server.services.knowledge_repo.get_document(doc_id)
        path = await loop.run_in_executor(None, original.__enter__)
        png = await loop.run_in_executor(
            None,
            partial(
                cad_preview.render_preview,
                kb_id=kb_id,
                doc_id=doc_id,
                original=path,
                content_hash=(row.content_hash if row is not None else ""),
                filename=str(metadata.get("filename") or ""),
            ),
        )
    except cad_preview.CadPreviewUnavailable as exc:
        raise HTTPException(status_code=415, detail=str(exc)) from exc
    except cad_preview.CadPreviewFailed as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
    finally:
        original.__exit__(None, None, None)
    return FileResponse(
        path=png,
        media_type="image/png",
        headers={
            "Content-Disposition": content_disposition(
                f"{metadata.get('filename') or 'drawing'}.png", disposition="inline"
            ),
            "Cache-Control": "private, max-age=300",
        },
    )


@router.get(
    "/{kb_id}/documents/{doc_id}/content",
    summary="Read raw editable text for a markdown or plain-text document",
)
async def get_text_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_any_permission(*KNOWLEDGE_PAGE_KEYS)),
) -> dict[str, Any]:
    try:
        return _knowledge_service(server).read_text_document(
            kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.put(
    "/{kb_id}/documents/{doc_id}/content",
    summary="Update markdown or plain-text document content and reindex",
)
async def update_text_document(
    kb_id: str,
    doc_id: str,
    body: UpdateTextDocumentBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    locale = resolve_request_locale(request)
    try:
        _require_usable(server, request)
        document = _knowledge_service(server).update_text_document(
            kb_id,
            doc_id,
            actor_user_id=user.id,
            content=body.content,
            is_admin=_is_admin(user),
        )
        assert server.services is not None
        enqueue_index_document(server.services, kb_id, doc_id)
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(exc, locale=locale, server=server) from exc


@router.post(
    "/{kb_id}/documents/{doc_id}/rename",
    summary="Rename a document or folder",
)
async def rename_document(
    kb_id: str,
    doc_id: str,
    body: RenameDocumentBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        document = _knowledge_service(server).rename_document(
            kb_id,
            doc_id,
            actor_user_id=user.id,
            new_name=body.new_name.strip(),
            is_admin=_is_admin(user),
        )
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/{kb_id}/documents/{doc_id}/move",
    summary="Move a document or folder into another folder",
    description=(
        "Moves a file, or a folder with its whole subtree, under the given folder "
        "(empty string = root). Refused when the target folder is missing, not "
        "writable, already contains a document with this name, or lies inside the "
        "folder being moved."
    ),
)
async def move_document(
    kb_id: str,
    doc_id: str,
    body: MoveDocumentBody,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        document = _knowledge_service(server).move_document(
            kb_id,
            doc_id,
            target_folder=body.target_folder,
            actor_user_id=user.id,
            is_admin=_is_admin(user),
        )
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.delete(
    "/{kb_id}/documents/{doc_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete a document",
)
async def delete_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> None:
    try:
        _knowledge_service(server).delete_document(
            kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post(
    "/{kb_id}/documents/{doc_id}/reindex",
    summary="Rebuild the index for one knowledge document",
)
async def reindex_document(
    kb_id: str,
    doc_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, Any]:
    try:
        _require_usable(server, request)
        document = _knowledge_service(server).reindex_document(
            kb_id, doc_id, actor_user_id=user.id, is_admin=_is_admin(user)
        )
        assert server.services is not None
        enqueue_index_document(server.services, kb_id, doc_id)
        return _row_payload(document)
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc


@router.post("/{kb_id}/reindex", summary="Reindex all documents in a knowledge base")
async def reindex_base(
    kb_id: str,
    request: Request,
    server: OctopServer = Depends(get_server),
    user: User = Depends(require_permission("knowledge_bases")),
) -> dict[str, int]:
    try:
        _require_usable(server, request)
        service = _knowledge_service(server)
        service.get_writable_base(kb_id, actor_user_id=user.id, is_admin=_is_admin(user))
        documents = [
            document
            for document in service.list_documents(
                kb_id, actor_user_id=user.id, is_admin=_is_admin(user)
            )
            if not document.is_dir
        ]
        # Preflight every selected document before resetting any status so a
        # partially writable base cannot be left half reindexed.
        for document in documents:
            service.require_document_writable(document, actor_user_id=user.id)
        assert server.services is not None
        for document in documents:
            service.reindex_document(
                kb_id, document.id, actor_user_id=user.id, is_admin=_is_admin(user)
            )
            enqueue_index_document(server.services, kb_id, document.id)
        return {"enqueued": len(documents)}
    except Exception as exc:
        raise _map_knowledge_error(
            exc, locale=resolve_request_locale(request), server=server
        ) from exc
