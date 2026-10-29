"""Authorized, source-located knowledge tools for agent turns."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
from typing import Annotated, Any, Literal

from langchain_core.tools import StructuredTool
from langgraph.config import get_config
from pydantic import Field

from octop.i18n import tr
from octop.infra.knowledge.collections import KnowledgeCollectionService
from octop.infra.knowledge.source_access import KnowledgeSourceService

logger = logging.getLogger(__name__)


def _tool_ctx() -> tuple[int, list[str], bool, dict[str, Any]]:
    configurable = get_config().get("configurable") or {}
    user_raw = configurable.get("user")
    if user_raw is None:
        raise ValueError("missing configurable.user")
    raw_ids = configurable.get("knowledge_base_ids")
    kb_ids = (
        list(dict.fromkeys(str(item).strip() for item in raw_ids if str(item).strip()))
        if isinstance(raw_ids, list)
        else []
    )
    return int(user_raw), kb_ids, bool(configurable.get("user_is_admin")), configurable


def _as_json(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, default=str)


def _related_collections(collections: list[dict[str, Any]], path: str) -> list[dict[str, Any]]:
    normalized = path.strip("/")
    related = []
    for collection in collections:
        target = str(collection.get("target_path") or "").strip("/")
        kind = str(collection.get("target_kind") or "")
        if (kind == "file" and normalized == target) or (
            kind == "folder"
            and (not target or normalized == target or normalized.startswith(target + "/"))
        ):
            related.append(collection)
    return related


def _required_collection_context(
    configurable: dict[str, Any],
    collections: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    seen = configurable.get("knowledge_collection_reads")
    seen_versions = (
        {str(row.get("id") or ""): row.get("updated_at") for row in seen if isinstance(row, dict)}
        if isinstance(seen, list)
        else {}
    )
    seen_ids = (
        {str(row) for row in seen if not isinstance(row, dict)} if isinstance(seen, list) else set()
    )
    return [
        item
        for item in collections
        if str(item.get("id") or "") not in seen_ids
        and (
            str(item.get("id") or "") not in seen_versions
            or seen_versions[str(item.get("id") or "")] != item.get("updated_at")
        )
    ]


def _collection_summaries(
    collections: list[dict[str, Any]],
    paths: list[str],
) -> list[dict[str, Any]]:
    normalized_paths = {path.strip("/") for path in paths}
    summaries = []
    for collection in collections:
        target = str(collection.get("target_path") or "").strip("/")
        kind = str(collection.get("target_kind") or "")
        related = (
            target in normalized_paths
            if kind == "file"
            else any(
                not target or path == target or path.startswith(target + "/")
                for path in normalized_paths
            )
        )
        if related:
            summaries.append(
                {
                    "id": collection.get("id"),
                    "name": collection.get("name"),
                    "target_path": collection.get("target_path"),
                }
            )
    return summaries


_CITATION_LIMIT = 8


def _kb_display_name(services: Any, kb_id: str) -> str:
    try:
        base = services.knowledge_repo.get_base(kb_id)
    except Exception:
        return ""
    return str(getattr(base, "name", "") or "")


def _with_citations(
    payload: str,
    *,
    kb_id: str,
    kb_name: str,
    documents: list[dict[str, Any]],
) -> str:
    """Append the citation marker the dashboard parses into source chips.

    The chat UI shows "from the knowledge base" links for every document an
    answer was built on, so each tool that surfaces documents reports them
    here. The marker is a machine-readable trailer: display text stays intact.
    """
    rows: list[dict[str, str]] = []
    seen: set[str] = set()
    for document in documents:
        doc_id = str(document.get("document_id") or "").strip()
        if not doc_id or document.get("is_dir") or doc_id in seen:
            continue
        seen.add(doc_id)
        rows.append(
            {
                "kb_id": kb_id,
                "kb_name": kb_name,
                "doc_id": doc_id,
                "filename": str(document.get("filename") or ""),
                "path": str(document.get("path") or ""),
            }
        )
        if len(rows) >= _CITATION_LIMIT:
            break
    if not rows:
        return payload
    return f"{payload}\n<!--octop-kb-citations:{json.dumps(rows, ensure_ascii=False)}-->"


def build_knowledge_tools(services: Any, *, workspace: Any) -> list[StructuredTool]:
    """Build source tools using shared services, actor-scoped runtime config, and workspace."""
    source_service = KnowledgeSourceService(services)
    collection_service = KnowledgeCollectionService(services)

    async def browse_knowledge(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        path: Annotated[
            str, Field(description="Optional directory path relative to the knowledge base.")
        ] = "",
    ) -> str:
        try:
            user_id, kb_ids, is_admin, _ = _tool_ctx()
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope", "documents": []})
            result = await asyncio.to_thread(
                source_service.list_documents,
                kb_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                path=path,
            )
            collections = await asyncio.to_thread(
                collection_service.list, kb_id, actor_user_id=user_id, is_admin=is_admin
            )
            paths = [path, *(str(item.get("path") or "") for item in result["documents"])]
            result["related_collections"] = _collection_summaries(collections, paths)
            return _with_citations(
                _as_json(result),
                kb_id=kb_id,
                kb_name=_kb_display_name(services, kb_id),
                documents=list(result.get("documents") or []),
            )
        except Exception:
            logger.warning("knowledge source browse failed", exc_info=True)
            return _as_json({"status": "unavailable", "documents": []})

    async def search_knowledge_paths(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        query: Annotated[str, Field(description="Filename or relative-path substring.")],
    ) -> str:
        try:
            user_id, kb_ids, is_admin, _ = _tool_ctx()
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope", "documents": []})
            result = await asyncio.to_thread(
                source_service.list_documents,
                kb_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                path="",
            )
            docs = result.get("documents", []) if isinstance(result, dict) else []
            needle = query.strip().casefold()
            matches = (
                [
                    document
                    for document in docs
                    if needle in str(document.get("path") or "").casefold()
                    or needle in str(document.get("filename") or "").casefold()
                ]
                if needle
                else []
            )
            collections = await asyncio.to_thread(
                collection_service.list, kb_id, actor_user_id=user_id, is_admin=is_admin
            )
            related = _collection_summaries(
                collections, [str(item.get("path") or "") for item in matches]
            )
            return _with_citations(
                _as_json(
                    {
                        "status": "found" if matches else "no_match",
                        "documents": matches,
                        "related_collections": related,
                    }
                ),
                kb_id=kb_id,
                kb_name=_kb_display_name(services, kb_id),
                documents=matches,
            )
        except Exception:
            logger.warning("knowledge path search failed", exc_info=True)
            return _as_json({"status": "unavailable", "documents": []})

    async def read_knowledge_collection(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        collection_id: Annotated[str, Field(description="Collection ID from browse_knowledge.")],
    ) -> str:
        try:
            user_id, kb_ids, is_admin, configurable = _tool_ctx()
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope"})
            docs = await asyncio.to_thread(
                collection_service.documents,
                kb_id,
                collection_id,
                actor_user_id=user_id,
                is_admin=is_admin,
            )
            if not isinstance(docs.get("topic_document"), str) or not isinstance(
                docs.get("reading_document"), str
            ):
                return _as_json({"status": "collection_context_unavailable"})
            result = {
                "collection": docs.get("collection"),
                "topic_document": docs["topic_document"],
                "reading_document": docs["reading_document"],
            }
            seen = configurable.get("knowledge_collection_reads")
            if not isinstance(seen, list):
                return _as_json({"status": "collection_context_unavailable"})
            read_rows = list(seen)
            read_rows = [
                row
                for row in read_rows
                if (row.get("id") if isinstance(row, dict) else row) != collection_id
            ]
            metadata = result.get("collection")
            read_rows.append(
                {
                    "id": collection_id,
                    "updated_at": metadata.get("updated_at")
                    if isinstance(metadata, dict)
                    else None,
                }
            )
            seen[:] = read_rows
            return _as_json(result)
        except Exception:
            logger.warning("knowledge collection read failed", exc_info=True)
            return _as_json({"status": "unavailable"})

    async def read_knowledge_document(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        document_id: Annotated[str, Field(description="Document ID from browse_knowledge.")],
        start: Annotated[int, Field(description="First line/page to read.", ge=1)] = 1,
        end: Annotated[int | None, Field(description="Last line/page, inclusive.")] = None,
        mode: Annotated[
            Literal["text", "image", "metadata"],
            Field(
                description=(
                    "Read text, return images multimodally, or compute only the actual "
                    "source SHA-256 and byte size without parsing."
                )
            ),
        ] = "text",
        sheet: Annotated[str | None, Field(description="Optional spreadsheet sheet name.")] = None,
    ) -> str | list[dict[str, Any]]:
        try:
            user_id, kb_ids, is_admin, configurable = _tool_ctx()
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope"})
            document_listing = await asyncio.to_thread(
                source_service.list_documents,
                kb_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                path="",
            )
            documents = document_listing.get("documents", [])
            document = next(
                (item for item in documents if item.get("document_id") == document_id), None
            )
            if document is None:
                return _as_json({"status": "not_found"})
            path = str(document.get("path") or "")
            collections = await asyncio.to_thread(
                collection_service.list, kb_id, actor_user_id=user_id, is_admin=is_admin
            )
            required = _required_collection_context(
                configurable, _related_collections(collections, path)
            )
            if required:
                return _as_json(
                    {
                        "status": "collection_context_required",
                        "collection_ids": [str(item.get("id")) for item in required],
                    }
                )
            result = await asyncio.to_thread(
                source_service.read_document,
                kb_id,
                document_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                start=start,
                end=end,
                mode=mode,
                sheet=sheet,
            )
            if result.get("status") == "image_ready":
                metadata = {k: v for k, v in result.items() if k != "images"}
                metadata["image_locators"] = [
                    image.get("locator") for image in result.get("images", [])
                ]
                blocks: list[dict[str, Any]] = [{"type": "text", "text": _as_json(metadata)}]
                from octop.infra.gateway.media.attachment_hints import VISION_MAX_BYTES

                for image in result.get("images", []):
                    encoded = str(image.get("data_base64") or "")
                    raw = base64.b64decode(encoded, validate=True)
                    if len(raw) > VISION_MAX_BYTES:
                        blocks.append(
                            {
                                "type": "text",
                                "text": "Image exceeds the supported vision size; export the original instead.",
                            }
                        )
                        continue
                    mime = str(image.get("mime_type") or "image/png")
                    blocks.append(
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{encoded}"},
                        }
                    )
                return blocks
            return _with_citations(
                _as_json(result),
                kb_id=kb_id,
                kb_name=_kb_display_name(services, kb_id),
                documents=[document],
            )
        except Exception:
            logger.warning("knowledge source read failed", exc_info=True)
            return _as_json({"status": "unavailable"})

    async def export_knowledge_original(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        document_id: Annotated[str, Field(description="Document ID from browse_knowledge.")],
        expected_version: Annotated[
            str,
            Field(
                description="Exact SHA-256 source version from browse or metadata read.",
                min_length=64,
                max_length=64,
                pattern="^[0-9a-f]{64}$",
            ),
        ],
    ) -> str:
        try:
            try:
                user_id, kb_ids, is_admin, configurable = _tool_ctx()
            except ValueError:
                return _as_json(
                    {
                        "status": "needs_shared_export_confirmation",
                        "message": tr("knowledge.source_export.trusted_context_required", "en"),
                    }
                )
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope"})
            document_listing = await asyncio.to_thread(
                source_service.list_documents,
                kb_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                path="",
            )
            documents = document_listing.get("documents", [])
            document = next(
                (item for item in documents if item.get("document_id") == document_id), None
            )
            if document is None:
                return _as_json({"status": "not_found"})
            requires_consent = True
            approval_ready = False
            try:
                registry = getattr(services, "agent_registry", None)
                checker = getattr(registry, "requires_shared_workspace_export_consent", None)
                agent_id = str(getattr(services, "agent_id", "") or "")
                if callable(checker) and agent_id:
                    requires_consent = bool(await asyncio.to_thread(checker, agent_id, user_id))
                if requires_consent:
                    readiness = getattr(registry, "knowledge_export_approval_ready", None)
                    approval_ready = callable(readiness) and bool(
                        await asyncio.to_thread(readiness, agent_id)
                    )
            except Exception:
                logger.warning("shared knowledge export scope lookup failed", exc_info=True)
            thread_id = str(configurable.get("thread_id") or "")
            if requires_consent and (
                not bool(getattr(services, "shared_export_approval_enforced", False))
                or not thread_id
                or not approval_ready
                # Nested subagent graphs do not inherit the parent's HITL policy.
                or "|" in str(configurable.get("checkpoint_ns") or "")
            ):
                return _as_json(
                    {
                        "status": "needs_shared_export_confirmation",
                        "message": tr(
                            "knowledge.source_export.confirmation_required",
                            str(configurable.get("locale") or "en"),
                        ),
                        "document_id": document_id,
                        "version": expected_version,
                    }
                )
            collections = await asyncio.to_thread(
                collection_service.list, kb_id, actor_user_id=user_id, is_admin=is_admin
            )
            required = _required_collection_context(
                configurable,
                _related_collections(collections, str(document.get("path") or "")),
            )
            if required:
                return _as_json(
                    {
                        "status": "collection_context_required",
                        "collection_ids": [str(item.get("id")) for item in required],
                    }
                )
            result = await source_service.export_document(
                kb_id,
                document_id,
                workspace=workspace,
                actor_user_id=user_id,
                is_admin=is_admin,
                expected_version=expected_version,
            )
            if requires_consent:
                result["sharing_warning"] = tr(
                    "knowledge.source_export.copy_notice",
                    str(configurable.get("locale") or "en"),
                )
            return _as_json(result)
        except (LookupError, PermissionError):
            return _as_json({"status": "not_found"})
        except ValueError as exc:
            if "version" in str(exc).casefold() or "changed" in str(exc).casefold():
                return _as_json({"status": "source_version_changed"})
            logger.warning("knowledge original export validation failed", exc_info=True)
            return _as_json({"status": "unavailable"})
        except Exception:
            logger.warning("knowledge original export failed", exc_info=True)
            return _as_json({"status": "unavailable"})

    async def record_knowledge_observation(
        kb_id: Annotated[str, Field(description="Selected knowledge-base ID.")],
        document_id: Annotated[str, Field(description="Image source document ID.")],
        evidence_id: Annotated[
            str, Field(description="Source-issued evidence ID from the image read.")
        ],
        version: Annotated[str, Field(description="Exact source version from the image read.")],
        locator: Annotated[
            dict[str, Any], Field(description="Exact image/page locator from that read.")
        ],
        observation: Annotated[
            str,
            Field(
                description="Your visual interpretation, clearly distinct from source transcription.",
                min_length=1,
                max_length=20_000,
            ),
        ],
    ) -> str:
        try:
            user_id, kb_ids, is_admin, configurable = _tool_ctx()
            if kb_id not in kb_ids:
                return _as_json({"status": "out_of_scope"})
            documents = await asyncio.to_thread(
                source_service.list_documents,
                kb_id,
                actor_user_id=user_id,
                is_admin=is_admin,
                path="",
            )
            document = next(
                (
                    item
                    for item in documents.get("documents", [])
                    if item.get("document_id") == document_id
                ),
                None,
            )
            if document is None:
                return _as_json({"status": "not_found"})
            collections = await asyncio.to_thread(
                collection_service.list, kb_id, actor_user_id=user_id, is_admin=is_admin
            )
            required = _required_collection_context(
                configurable,
                _related_collections(collections, str(document.get("path") or "")),
            )
            if required:
                return _as_json(
                    {
                        "status": "collection_context_required",
                        "collection_ids": [str(item.get("id")) for item in required],
                    }
                )
            receipt = await asyncio.to_thread(
                collection_service.record_source_observation,
                kb_id,
                document_id,
                {"text": observation},
                evidence_id=evidence_id,
                version=version,
                locator=locator,
                actor_user_id=user_id,
                is_admin=is_admin,
            )
            return _as_json(
                {
                    "status": "interpretation_recorded",
                    "label": "interpretation_not_source_transcription",
                    "receipt": receipt,
                }
            )
        except (LookupError, PermissionError, ValueError):
            return _as_json({"status": "evidence_not_valid"})
        except Exception:
            logger.warning("knowledge visual interpretation record failed", exc_info=True)
            return _as_json({"status": "unavailable"})

    return [
        StructuredTool.from_function(
            coroutine=browse_knowledge,
            name="browse_knowledge",
            description=(
                "Browse authorized directories and files in an attached knowledge base. "
                "Paths are relative to that base."
            ),
        ),
        StructuredTool.from_function(
            coroutine=search_knowledge_paths,
            name="search_knowledge_paths",
            description=(
                "Find authorized knowledge documents by a path or filename substring; "
                "returns locations only."
            ),
        ),
        StructuredTool.from_function(
            coroutine=read_knowledge_collection,
            name="read_knowledge_collection",
            description=(
                "Read both topic and reading Markdown documents for a related collection "
                "before opening its source files."
            ),
        ),
        StructuredTool.from_function(
            coroutine=read_knowledge_document,
            name="read_knowledge_document",
            description=(
                "Read source text with exact locator/version, line range, PDF pages, or "
                "spreadsheet sheet; image mode returns actual multimodal content. Metadata "
                "mode computes live source SHA-256 and byte size without parsing. "
                "Do not cite stale, denied, or incomplete sources."
            ),
        ),
        StructuredTool.from_function(
            coroutine=export_knowledge_original,
            name="export_knowledge_original",
            description=(
                "Export the exact authorized original source version to the agent workspace "
                "after reading related collection context. When this agent workspace is "
                "shared, every other caller of the agent can read the exported copy and "
                "the source knowledge-base ACL no longer protects it; this exact export "
                "requires human approval in the current thread. Export is not a verified read."
            ),
        ),
        StructuredTool.from_function(
            coroutine=record_knowledge_observation,
            name="record_knowledge_observation",
            description=(
                "After visually inspecting a delivered image, optionally record a concise "
                "interpretation as interpretation, never source transcription. Supply the "
                "source evidence ID, current version, and exact image/page locator from the "
                "image-read result; stale, arbitrary, or non-image evidence is rejected."
            ),
        ),
    ]
