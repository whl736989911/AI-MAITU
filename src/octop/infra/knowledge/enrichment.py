"""AI document enrichment: summaries, keywords and field suggestions.

The model receives a bounded preview plus the base's metadata schema and
returns JSON. Nothing it produces is written to the document: a suggestion row
is stored as ``pending`` and a human applies or rejects it. Applying revalidates
values against the current schema and document hash, so a suggestion cannot
outlive the content it describes.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, cast

from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.files import file_digest
from octop.infra.knowledge.metadata import validate_suggested_values
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.utils.ulid import new_ulid

logger = logging.getLogger(__name__)

_MAX_CONTEXT_CHARS = 12_000
_MAX_SUMMARY_CHARS = 500
_MAX_KEYWORDS = 12
_MAX_KEYWORD_CHARS = 80
_LLM_TIMEOUT_SECONDS = 120.0

_SYSTEM_PROMPT = (
    "You organize documents for a knowledge base. Read the provided text and "
    "return one JSON object only, with exactly these keys: "
    '{"summary": string, "keywords": [string], "fields": {field_key: value}}. '
    "The summary is at most 400 characters and describes the document, not the "
    "request. Keywords are 3-8 short terms. For fields, use only the provided "
    "field keys and respect their type: text, number, date (YYYY-MM-DD), or one "
    "of the listed options for an enum. Omit a field when the text does not "
    "support a confident value; never invent values. Do not add commentary."
)


class KnowledgeEnrichmentService:
    def __init__(self, services: Any, agent_registry: Any = None) -> None:
        self._services = services
        self._repo = services.knowledge_repo
        self._agent_registry = agent_registry or getattr(services, "agent_registry", None)

    def enrich_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
        llm: Any | None = None,
    ) -> dict[str, Any]:
        knowledge = KnowledgeService(self._services)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            raise LookupError("knowledge document not found")
        knowledge.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        knowledge.require_document_writable(document, actor_user_id=actor_user_id)
        preview = knowledge.preview_document(
            kb_id, doc_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        schema = knowledge.get_metadata_schema(
            kb_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        if llm is None:
            llm, model_ref = self._resolve_llm()
        else:
            model_ref = "test"
        if llm is None:
            raise OctopError(
                ErrorCode.MODEL_UNAVAILABLE, "no chat model is configured for enrichment"
            )
        payload = self._call_model(llm, preview, schema)
        current = self._repo.get_document(doc_id)
        if (
            current is None
            or current.kb_id != kb_id
            or current.content_hash != document.content_hash
            or current.status != document.status
        ):
            raise OctopError(
                ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED,
                "the document changed while the suggestion was being generated",
            )
        knowledge.require_document_writable(current, actor_user_id=actor_user_id)
        summary = str(payload.get("summary") or "").strip()[:_MAX_SUMMARY_CHARS]
        raw_keywords = payload.get("keywords")
        keywords = []
        if isinstance(raw_keywords, list):
            for item in raw_keywords:
                keyword = str(item).strip()[:_MAX_KEYWORD_CHARS]
                if keyword and keyword not in keywords:
                    keywords.append(keyword)
                if len(keywords) >= _MAX_KEYWORDS:
                    break
        fields = validate_suggested_values(schema, payload.get("fields"))
        return cast(
            dict[str, Any],
            self._repo.create_suggestion(
                suggestion_id=new_ulid(),
                document_id=doc_id,
                kb_id=kb_id,
                content_hash=document.content_hash,
                summary=summary,
                keywords=keywords,
                fields=fields,
                model=model_ref,
            ),
        )

    def list_suggestions(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        document_id: str | None = None,
        status: str | None = None,
        limit: int = 50,
        is_admin: bool = False,
    ) -> list[dict[str, Any]]:
        knowledge = KnowledgeService(self._services)
        knowledge.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        documents = knowledge.list_documents(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        allowed = {row.id for row in documents if not row.is_dir}
        return cast(
            list[dict[str, Any]],
            self._repo.list_suggestions(
                kb_id,
                document_id=document_id,
                status=status,
                allowed_document_ids=allowed,
                limit=limit,
            ),
        )

    def apply_suggestion(
        self,
        kb_id: str,
        suggestion_id: str,
        *,
        actor_user_id: int,
        fields: dict[str, Any] | None = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        suggestion = self._require_suggestion(kb_id, suggestion_id)
        knowledge = KnowledgeService(self._services)
        document = self._repo.get_document(suggestion["document_id"])
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        knowledge.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        knowledge.require_document_writable(document, actor_user_id=actor_user_id)
        if document.status != "ready":
            raise OctopError(
                ErrorCode.KNOWLEDGE_INDEX_NOT_READY,
                "the document changed and is waiting for reindexing",
            )
        if (
            suggestion["content_hash"]
            and document.content_hash
            and suggestion["content_hash"] != document.content_hash
        ):
            raise OctopError(
                ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED,
                "the document changed after this suggestion was generated",
            )
        if suggestion["content_hash"]:
            from octop.infra.knowledge.source_access import KnowledgeSourceService

            with KnowledgeSourceService(self._services).open_original(
                kb_id, document.id, actor_user_id=actor_user_id, is_admin=is_admin
            ) as original:
                if file_digest(original) != suggestion["content_hash"]:
                    raise OctopError(
                        ErrorCode.KNOWLEDGE_SOURCE_VERSION_CHANGED,
                        "the original changed after this suggestion was generated",
                    )
        schema = knowledge.get_metadata_schema(
            kb_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        chosen = fields if fields is not None else suggestion["fields"]
        schema_keys = {str(field["key"]) for field in schema}
        unknown = sorted(str(key) for key in chosen if str(key) not in schema_keys)
        if unknown:
            raise OctopError(
                ErrorCode.KNOWLEDGE_METADATA_INVALID,
                f"unknown metadata field(s): {', '.join(unknown)}",
            )
        reviewed = {
            str(key): value
            for key, value in chosen.items()
            if str(key) in schema_keys and value is not None and value != "" and value != []
        }
        values = validate_suggested_values(schema, reviewed)
        if len(values) != len(reviewed):
            raise OctopError(
                ErrorCode.KNOWLEDGE_METADATA_INVALID,
                "one or more reviewed field values are no longer valid for this schema",
            )
        if values:
            knowledge.update_document_metadata(
                kb_id,
                document.id,
                actor_user_id=actor_user_id,
                metadata=values,
                is_admin=is_admin,
            )
        self._repo.set_suggestion_status(suggestion_id, "applied")
        updated = self._repo.get_suggestion(suggestion_id)
        return cast(dict[str, Any], updated or suggestion)

    def reject_suggestion(
        self,
        kb_id: str,
        suggestion_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        suggestion = self._require_suggestion(kb_id, suggestion_id)
        knowledge = KnowledgeService(self._services)
        document = self._repo.get_document(suggestion["document_id"])
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        knowledge.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        knowledge.require_document_writable(document, actor_user_id=actor_user_id)
        self._repo.set_suggestion_status(suggestion_id, "rejected")
        return cast(dict[str, Any], self._repo.get_suggestion(suggestion_id) or suggestion)

    def _require_suggestion(self, kb_id: str, suggestion_id: str) -> dict[str, Any]:
        suggestion = self._repo.get_suggestion(suggestion_id)
        if suggestion is None or suggestion["kb_id"] != kb_id:
            raise LookupError("knowledge suggestion not found")
        if suggestion["status"] != "pending":
            raise OctopError(
                ErrorCode.KNOWLEDGE_REQUEST_INVALID,
                f"suggestion is {suggestion['status']!r}, not 'pending'",
            )
        return cast(dict[str, Any], suggestion)

    def _call_model(
        self, llm: Any, preview: dict[str, Any], schema: list[dict[str, Any]]
    ) -> dict[str, Any]:
        from langchain_core.messages import HumanMessage, SystemMessage

        from octop.infra.agents.experts.manifest_generator import parse_model_json
        from octop.infra.agents.middleware.platform_identity import render_platform_identity
        from octop.infra.utils.llm_text import ainvoke_text

        context = {
            "title": preview.get("title") or "",
            "filename": preview.get("filename") or "",
            "text": str(preview.get("text") or "")[:_MAX_CONTEXT_CHARS],
            "fields": [
                {
                    "key": field["key"],
                    "label": field.get("label") or field["key"],
                    "type": field["type"],
                    **(
                        {"options": field.get("options") or []}
                        if field.get("type") == "enum"
                        else {}
                    ),
                }
                for field in schema
            ],
        }
        messages = [
            SystemMessage(content=render_platform_identity(_SYSTEM_PROMPT, None)),
            HumanMessage(content=json.dumps(context, ensure_ascii=False)),
        ]
        try:
            raw = asyncio.run(ainvoke_text(llm, messages, timeout=_LLM_TIMEOUT_SECONDS))
            payload = parse_model_json(raw)
        except Exception as exc:
            logger.warning("knowledge enrichment model call failed", exc_info=True)
            raise OctopError(
                ErrorCode.KNOWLEDGE_ENRICHMENT_FAILED,
                "the model did not return a usable enrichment result",
            ) from exc
        return payload

    def _resolve_llm(self) -> tuple[Any | None, str]:
        registry = self._agent_registry
        if registry is None:
            return None, ""
        harness = getattr(registry, "harness_manager", None)
        factory = getattr(harness, "shared_factory", None) if harness is not None else None
        providers = getattr(registry, "providers", None)
        if factory is None or providers is None:
            return None, ""
        try:
            active_name, active_model = self._services.settings_repo.get_active_model()
        except Exception:
            active_name, active_model = "", ""
        active_ref = f"{active_name}/{active_model}" if active_name and active_model else ""
        candidates = [
            ref
            for ref in (active_ref, providers.resolve_first_model_ref())
            if ref and providers.is_model_ref_usable(ref)
        ]
        if not candidates:
            return None, ""
        try:
            return factory.get_chat_model(candidates[0]), candidates[0]
        except Exception:
            logger.warning("enrichment model %s unavailable", candidates[0], exc_info=True)
            return None, ""


__all__ = ["KnowledgeEnrichmentService"]
