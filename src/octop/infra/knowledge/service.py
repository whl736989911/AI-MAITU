"""Knowledge-base ownership checks and document upload orchestration.

Visibility is decided by ``resource_acl`` (see ``octop.infra.sharing``); the
legacy per-base share column is only still written as a compatibility mirror.
"""

from __future__ import annotations

import logging
import unicodedata
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

from octop.config import DEFAULT_MAX_UPLOAD_MB, upload_mb_to_bytes
from octop.infra.db.repos.knowledge import KnowledgeBaseRow, KnowledgeDocumentRow
from octop.infra.knowledge.embed import embed_knowledge_texts
from octop.infra.knowledge.files import (
    delete_document_file,
    delete_knowledge_base_files,
    document_digest,
    document_path,
    file_digest,
    write_document,
)
from octop.infra.knowledge.gate import assert_knowledge_usable, get_capability
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.ocr import (
    OCR_IMAGE_SUFFIXES,
    load_ocr_config,
    optional_ocr_extractor,
)
from octop.infra.knowledge.parse import parse_document
from octop.infra.knowledge.relpath import (
    ancestor_dirs,
    normalize_kb_path,
    path_basename,
    path_parent,
)
from octop.infra.knowledge.scope import (
    may_read_document,
    may_read_knowledge_base,
    readable_documents,
)
from octop.infra.knowledge.search import (
    DEFAULT_SEARCH_K,
    SearchHit,
    normalize_query,
    query_terms,
    search_base,
    snippet,
)
from octop.infra.knowledge.sources import SourceError
from octop.infra.sharing import VISIBILITY_PRIVATE, AclEntry, can_write

logger = logging.getLogger(__name__)

MAX_DOCS_PER_KB = 100
MAX_DOCUMENT_BYTES = upload_mb_to_bytes(DEFAULT_MAX_UPLOAD_MB)
# Upper bound for the per-base max_documents field. Mirrors Field(le=10000).
MAX_KB_MAX_DOCUMENTS = 10_000
ACCESS_READ = "read"
ACCESS_WRITE = "write"


class KnowledgeAccessDenied(PermissionError):
    """A knowledge refusal that says which access was missing.

    ``PermissionError`` alone carried no such distinction, so every refusal
    reached the client as "you do not have access to this knowledge base" —
    including the one raised for an actor who was *reading* that base and only
    asked to change it. Read and edit are separate permissions here (design
    §5.1), so the level travels on the exception and the router's error mapping
    reads it instead of guessing from the message.
    """

    def __init__(self, message: str, *, access: str) -> None:
        super().__init__(message)
        self.access = access


_MAX_PREVIEW_CHARS = 200_000
_EXT_TO_CONTENT_TYPE = {
    ".txt": "text/plain",
    ".md": "text/markdown",
    ".markdown": "text/markdown",
    ".rst": "text/x-rst",
    ".html": "text/html",
    ".htm": "text/html",
    ".json": "application/json",
    ".jsonl": "application/jsonl",
    ".xml": "application/xml",
    ".yaml": "application/yaml",
    ".yml": "application/yaml",
    ".csv": "text/csv",
    ".tsv": "text/tab-separated-values",
    ".pdf": "application/pdf",
    # design §6.1: the binary Office formats are converted by LibreOffice before
    # parsing (``octop.infra.knowledge.legacy_office``), so the platform accepts
    # and indexes them like any other document.
    ".doc": "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".ppt": "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
    ".xls": "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".xlsm": "application/vnd.ms-excel.sheet.macroEnabled.12",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".webp": "image/webp",
}
_ALLOWED_CONTENT_TYPES = set(_EXT_TO_CONTENT_TYPE.values())
_TEXT_CONTENT_TYPES = {"text/plain", "text/markdown"}
_TEXT_FORMAT_TO_EXT = {"md": ".md", "txt": ".txt"}
_TEXT_FORMAT_TO_CONTENT_TYPE = {"md": "text/markdown", "txt": "text/plain"}


def _resolve_content_type(filename: str, content_type: str) -> str:
    suffix = Path(filename).suffix.lower()
    mapped = _EXT_TO_CONTENT_TYPE.get(suffix)
    if mapped is not None:
        return mapped
    ct = (content_type or "").strip().lower()
    if ct in _ALLOWED_CONTENT_TYPES:
        return ct
    if ct in {"", "application/octet-stream"}:
        return ct
    return ct


def knowledge_content_type(suffix: str) -> str | None:
    """The content type a file extension maps to (``.md`` → ``text/markdown``).

    The one type table behind uploads answers this, so a URL source cannot
    accept a type an upload would reject (or the other way round).
    """
    return _EXT_TO_CONTENT_TYPE.get(suffix.strip().lower())


def knowledge_suffix_for_content_type(content_type: str) -> str | None:
    """The stored extension for a served content type, when it is supported."""
    wanted = (content_type or "").split(";")[0].strip().lower()
    if not wanted:
        return None
    for suffix, mapped in _EXT_TO_CONTENT_TYPE.items():
        if mapped == wanted:
            return suffix
    return None


class KnowledgeService:
    """Apply ownership while keeping control-plane rows and files synchronized."""

    def __init__(self, services: Any) -> None:
        self._services = services

    def max_document_bytes(self) -> int:
        """The size ceiling one knowledge document may reach."""
        config = getattr(self._services, "config", None)
        limit = getattr(config, "max_upload_bytes", None)
        if isinstance(limit, int) and limit > 0:
            return limit
        return MAX_DOCUMENT_BYTES

    @property
    def _repo(self) -> Any:
        return self._services.knowledge_repo

    def enterprise_space(self) -> KnowledgeBaseRow:
        """The deployment's one enterprise knowledge space.

        Schema v27 makes ``knowledge_bases`` a singleton space: the migration
        seeds the row and the database refuses a second one
        (``idx_knowledge_bases_enterprise``). There is deliberately no
        ``create_base`` beside this — a user creating a knowledge base is the
        model the design replaced, so the capability is gone rather than
        discouraged, and the knowledge-base API no longer has a create route.

        Raising rather than returning ``None`` is the signal that matters: a
        deployment without its space is a broken schema, not an empty one.
        """
        space = cast(KnowledgeBaseRow | None, self._repo.get_enterprise_space())
        if space is None:
            raise RuntimeError("the enterprise knowledge space has not been seeded")
        return space

    def list_visible_bases(self, *, actor_user_id: int) -> list[KnowledgeBaseRow]:
        """Knowledge bases this actor may use, per the one access rule.

        There is deliberately no ``is_admin`` branch: ``list_all() if is_admin``
        repeated the admin bypass that ``sharing.can_access`` already applies
        first, so that branch skipped the rule set instead of going through it
        and every later rule would have had to be remembered here too. An admin
        still sees everything, because rule 1 of the rule set is that bypass.
        """
        return cast(list[KnowledgeBaseRow], self._repo.list_visible(actor_user_id))

    def update_base(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        name: str | None = None,
        description: str | None = None,
        default_open: bool | None = None,
        shared: bool | None = None,
        icon_name: str | None = None,
        max_documents: int | None = None,
        is_admin: bool = False,
    ) -> KnowledgeBaseRow:
        self.require_owner(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        if max_documents is not None and (
            max_documents < 0 or max_documents > MAX_KB_MAX_DOCUMENTS
        ):
            raise ValueError(f"max_documents must be between 0 and {MAX_KB_MAX_DOCUMENTS}")
        self._repo.update_base(
            kb_id,
            name=name,
            description=description,
            default_open=default_open,
            shared=shared,
            icon_name=icon_name,
            max_documents=max_documents,
        )
        return self._require_base(kb_id)

    def list_documents(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False, prefix: str | None = None
    ) -> list[KnowledgeDocumentRow]:
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        documents = (
            self._repo.list_documents(kb_id)
            if prefix is None
            else self._repo.list_children(kb_id, prefix)
        )
        # design §14: a file the actor may not read is absent from the listing
        # and from search — one filter, so the two cannot disagree.
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        return cast(
            list[KnowledgeDocumentRow],
            readable_documents(documents, restricted=restricted, readable=readable),
        )

    def create_folder(
        self, kb_id: str, *, actor_user_id: int, path: str, is_admin: bool = False
    ) -> KnowledgeDocumentRow:
        self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        return cast(KnowledgeDocumentRow, self._repo.ensure_folder(kb_id, path))

    def preview_document(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> dict[str, Any]:
        """Return extracted plain text for a readable knowledge document.

        The document's own title and headings come with it: they are what the
        parser already worked out (design §6.2), and a reader who is looking at
        the text wants to know what the document calls itself. Tables are left
        out on purpose — the text below already holds them, and a spreadsheet's
        structure is the whole file twice over.
        """
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        if document.is_dir:
            raise LookupError("knowledge document not found")
        self.require_document_readable(document, actor_user_id=actor_user_id, is_admin=is_admin)
        path = document_path(kb_id, doc_id, document.filename)
        parsed = parse_document(
            path,
            ocr=optional_ocr_extractor(self._services),
            # The on-disk name is the document id, so the parser has to be told
            # what the file is really called.
            source_path=document.display_path,
        )
        text = parsed.text
        if len(text) > _MAX_PREVIEW_CHARS:
            text = text[:_MAX_PREVIEW_CHARS]
        return {
            "id": document.id,
            "filename": document.filename,
            "title": parsed.title,
            "pages": parsed.pages,
            "sections": list(parsed.sections),
            "text": text,
        }

    def coverage(self, kb_id: str, *, actor_user_id: int, is_admin: bool = False) -> dict[str, int]:
        """Report indexing coverage only for documents this actor may read."""
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        documents = [
            doc
            for doc in readable_documents(
                self._repo.list_documents(kb_id), restricted=restricted, readable=readable
            )
            if not doc.is_dir
        ]
        return {
            "total": len(documents),
            "searchable": sum(doc.status == "ready" for doc in documents),
            "pending": sum(
                doc.status in {"pending", "processing", "discovered"} for doc in documents
            ),
            "indexing": sum(doc.status in {"pending", "processing"} for doc in documents),
            "failed": sum(doc.status in {"failed", "password_required"} for doc in documents),
            "unsupported": sum(doc.status == "unsupported" for doc in documents),
        }

    def read_segment(
        self,
        kb_id: str,
        doc_id: str,
        segment_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> dict[str, object]:
        """Confirm an indexed segment still describes the readable source bytes."""
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            raise LookupError("knowledge document not found")
        self.require_document_readable(document, actor_user_id=actor_user_id, is_admin=is_admin)
        hit = KnowledgeIndex(kb_id).get_segment(doc_id, segment_id)
        if hit is None:
            raise LookupError("knowledge document segment not found")
        version = str(hit.metadata.get("version") or "")
        locator = hit.metadata.get("locator")
        evidence: dict[str, object] = {
            "document_id": doc_id,
            "segment_id": segment_id,
            "filename": document.filename,
            "text": "",
            "locator": locator if isinstance(locator, dict) else {},
            "version": version,
            "verified": False,
            "reason": "stale",
        }
        if document.status != "ready" or not version or version != document.content_hash:
            return evidence
        try:
            if document.data_source_id:
                from octop.infra.knowledge.data_sources import DataSourceService
                from octop.infra.knowledge.jobs import _source_path

                source = self._services.data_sources_repo.get(document.data_source_id)
                if source is None:
                    evidence["reason"] = "source_unavailable"
                    return evidence
                connector = DataSourceService(self._services).connector(source)
                with _source_path(connector, document.source_path, document.filename) as original:
                    current = file_digest(original)
            else:
                original = document_path(kb_id, doc_id, document.filename)
                current = file_digest(original)
        except (OSError, SourceError):
            evidence["reason"] = "source_unavailable"
            return evidence
        if current != version:
            return evidence
        self.require_document_readable(document, actor_user_id=actor_user_id, is_admin=is_admin)
        evidence.update(text=hit.text, verified=True, reason=None)
        return evidence

    def list_search_rules(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> list[dict[str, object]]:
        base = self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        visible = {
            document.id: document
            for document in self._repo.list_documents(base.id)
            if may_read_document(document.id, restricted=restricted, readable=readable)
        }
        return [
            {
                "path": visible[row.document_id].path,
                "kind": "folder" if visible[row.document_id].is_dir else "file",
                "mode": row.mode,
                "keywords": list(row.keywords),
            }
            for row in self._services.knowledge_search_rules_repo.list_for_user(
                user_id=actor_user_id
            )
            if row.document_id in visible
        ]

    def set_search_rule(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        path: str,
        mode: str,
        keywords: Sequence[str],
        is_admin: bool = False,
    ) -> dict[str, object]:
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        normalized = normalize_kb_path(path)
        if not normalized or normalized != path.replace("\\", "/").strip():
            raise ValueError("invalid knowledge search path")
        if mode not in {"keyword", "hybrid", "exclude"}:
            raise ValueError("invalid knowledge search mode")
        cleaned_keywords: list[str] = []
        seen_keywords: set[str] = set()
        for raw in keywords:
            keyword = " ".join(unicodedata.normalize("NFKC", str(raw)).split())
            if not keyword:
                continue
            if len(keyword) > 200:
                raise ValueError("knowledge search keyword is too long")
            folded = keyword.casefold()
            if folded not in seen_keywords:
                seen_keywords.add(folded)
                cleaned_keywords.append(keyword)
                if len(cleaned_keywords) > 32:
                    raise ValueError("at most 32 knowledge search keywords are allowed")
        document = self._repo.get_document_by_path(kb_id, normalized)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        if document is None or not may_read_document(
            document.id, restricted=restricted, readable=readable
        ):
            raise LookupError("knowledge document not found")
        row = self._services.knowledge_search_rules_repo.set(
            document_id=document.id,
            user_id=actor_user_id,
            mode=mode,
            keywords=cleaned_keywords,
        )
        return {
            "path": normalized,
            "kind": "folder" if document.is_dir else "file",
            "mode": row.mode,
            "keywords": list(row.keywords),
        }

    def delete_search_rule(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        path: str,
        is_admin: bool = False,
    ) -> None:
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        normalized = normalize_kb_path(path)
        if not normalized or normalized != path.replace("\\", "/").strip():
            raise ValueError("invalid knowledge search path")
        document = self._repo.get_document_by_path(kb_id, normalized)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        if document is None or not may_read_document(
            document.id, restricted=restricted, readable=readable
        ):
            raise LookupError("knowledge document not found")
        self._services.knowledge_search_rules_repo.delete(
            document_id=document.id, user_id=actor_user_id
        )

    def search(
        self,
        *,
        actor_user_id: int,
        query: str,
        kb_id: str | None = None,
        limit: int = DEFAULT_SEARCH_K,
        is_admin: bool = False,
        query_vector: Sequence[float] | None = None,
        generate_query_vector: bool = False,
    ) -> list[SearchHit]:
        """Search readable filenames and indexed text under the caller's rules."""
        cleaned = (query or "").strip()
        if not cleaned or limit <= 0:
            return []
        if kb_id is not None:
            bases = [self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)]
        else:
            bases = self.list_visible_bases(actor_user_id=actor_user_id)
        terms = query_terms(cleaned)
        normalized_query = normalize_query(cleaned)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        search_vector = query_vector
        embedding_attempted = False
        user_rules = self._services.knowledge_search_rules_repo.list_for_user(user_id=actor_user_id)
        alias_rules = {
            rule.document_id
            for rule in user_rules
            if rule.mode != "exclude"
            and any(normalize_query(alias) in normalized_query for alias in rule.keywords)
        }
        hits: list[SearchHit] = []
        for base in bases:
            all_docs = self._repo.list_documents(base.id)
            documents = [
                document
                for document in readable_documents(
                    all_docs, restricted=restricted, readable=readable
                )
                if not document.is_dir
            ]
            by_path = {doc.path: doc for doc in all_docs if doc.is_dir}
            by_id = {doc.id: doc for doc in all_docs}
            rule_by_path: dict[str, Any] = {}
            for rule in user_rules:
                entry = by_id.get(rule.document_id)
                if entry is not None and may_read_document(
                    entry.id, restricted=restricted, readable=readable
                ):
                    rule_by_path[entry.path] = rule

            effective: dict[str, Any] = {}
            for document in documents:
                selected = None
                for folder_path in ancestor_dirs(document.path):
                    folder = by_path.get(folder_path)
                    if folder is None:
                        selected = None
                        break
                    if folder_path in rule_by_path:
                        selected = rule_by_path[folder_path]
                effective[document.id] = rule_by_path.get(document.path) or selected
            searchable = [
                document
                for document in documents
                if effective[document.id] is None or effective[document.id].mode != "exclude"
            ]
            ready = {doc.id: doc for doc in searchable if doc.status == "ready"}
            semantic_ids = {
                doc.id
                for doc in ready.values()
                if effective[doc.id] is None or effective[doc.id].mode != "keyword"
            }
            alias_count = 0
            for document in searchable:
                name = unicodedata.normalize(
                    "NFKC", f"{document.filename} {document.source_path or document.path}"
                ).casefold()
                matched = sum(term in name for term in terms)
                if matched:
                    hits.append(
                        SearchHit(
                            kb_id=base.id,
                            base_name=base.name,
                            document_id=document.id,
                            filename=document.filename,
                            path=document.path,
                            source_path=document.source_path,
                            title=document.title or document.filename,
                            ordinal=-1,
                            snippet=document.filename,
                            score=0.01 + matched / len(terms) * 0.001,
                            segment_id="",
                            locator={"kind": "file"},
                            version=document.content_hash,
                            match_kind="filename",
                        )
                    )
                rule = effective[document.id]
                if alias_count < limit and rule is not None and rule.document_id in alias_rules:
                    hits.append(
                        SearchHit(
                            kb_id=base.id,
                            base_name=base.name,
                            document_id=document.id,
                            filename=document.filename,
                            path=document.path,
                            source_path=document.source_path,
                            title=document.title or document.filename,
                            ordinal=-1,
                            snippet="",
                            score=0.5,
                            segment_id="",
                            locator={"kind": "rule_keyword"},
                            version=document.content_hash,
                            match_kind="rule_keyword",
                        )
                    )
                    alias_count += 1
            if (
                generate_query_vector
                and search_vector is None
                and semantic_ids
                and not embedding_attempted
            ):
                embedding_attempted = True
                capability = get_capability(
                    self._services.settings_repo.get,
                    getattr(self._services, "provider_repo", None),
                )
                if capability["selected_model"] and capability["prerequisites_ok"]:
                    try:
                        vectors = embed_knowledge_texts(self._services, [cleaned])
                        search_vector = vectors[0] if vectors else None
                    except Exception:
                        logger.warning(
                            "knowledge semantic query unavailable; using terms only", exc_info=True
                        )
            for hit, document in search_base(
                base.id,
                query=cleaned,
                ready_documents=ready,
                limit=limit,
                query_vector=search_vector,
                semantic_document_ids=semantic_ids,
            ):
                locator = hit.metadata.get("locator")
                hits.append(
                    SearchHit(
                        kb_id=base.id,
                        base_name=base.name,
                        document_id=document.id,
                        filename=document.filename,
                        path=document.path,
                        source_path=document.source_path,
                        title=document.title,
                        ordinal=hit.ordinal,
                        snippet=snippet(hit.text, terms),
                        score=hit.score,
                        segment_id=hit.chunk_id if locator else "",
                        locator=locator if isinstance(locator, dict) else {},
                        version=str(hit.metadata.get("version") or document.content_hash),
                        match_kind="content",
                    )
                )
        hits.sort(
            key=lambda row: (
                0 if row.match_kind == "content" else 1,
                -row.score,
                row.base_name,
                row.path,
                row.ordinal,
            )
        )
        return hits[:limit]

    def resolve_document_file(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> tuple[Path, str, str]:
        """Return ``(path, filename, content_type)`` for the on-disk original.

        Raises ``LookupError`` when the document row is missing and
        ``FileNotFoundError`` when the original bytes are gone from disk.
        """
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        if document.is_dir:
            raise LookupError("knowledge document not found")
        self.require_document_readable(document, actor_user_id=actor_user_id, is_admin=is_admin)
        path = document_path(kb_id, doc_id, document.filename)
        if not path.is_file():
            raise FileNotFoundError("knowledge document original file not found")
        return path, document.filename, document.content_type

    def document_has_original(self, document: KnowledgeDocumentRow) -> bool:
        """True when the uploaded original still exists on disk."""
        if document.is_dir:
            return False
        return document_path(document.kb_id, document.id, document.filename).is_file()

    def read_text_document(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> dict[str, str]:
        """Return raw UTF-8 text for an editable md/txt knowledge document."""
        self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        if document.is_dir:
            raise LookupError("knowledge document not found")
        self.require_document_readable(document, actor_user_id=actor_user_id, is_admin=is_admin)
        if document.content_type not in _TEXT_CONTENT_TYPES:
            raise ValueError("unsupported knowledge document content type: not editable text")
        raw = document_path(kb_id, doc_id, document.filename).read_bytes()
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            raise ValueError("knowledge document is not valid UTF-8 text") from exc
        return {
            "id": document.id,
            "filename": document.filename,
            "content_type": document.content_type,
            # The title the parser stored, not one recomputed here: it is the
            # same value the listing shows, and computing it twice would let the
            # two disagree.
            "title": document.title,
            "text": text,
        }

    def create_text_document(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        name: str,
        format: str,
        content: str = "",
        is_admin: bool = False,
        path: str | None = None,
    ) -> KnowledgeDocumentRow:
        """Create a markdown or plain-text document with optional draft content."""
        fmt = (format or "").strip().lower().lstrip(".")
        if fmt not in _TEXT_FORMAT_TO_EXT:
            raise ValueError(f"unsupported knowledge document content type: {format}")
        cleaned_name = (name or "").strip()
        if not cleaned_name:
            raise ValueError("invalid knowledge document filename")
        ext = _TEXT_FORMAT_TO_EXT[fmt]
        stem = Path(cleaned_name).name
        if Path(stem).suffix.lower() not in {".md", ".txt"}:
            stem = f"{stem}{ext}"
        elif Path(stem).suffix.lower() != ext:
            stem = f"{Path(stem).stem}{ext}"
        relative = f"{normalize_kb_path(path)}/{stem}" if path else stem
        relative = normalize_kb_path(relative)
        encoded = content.encode("utf-8")
        return self.upload_document(
            kb_id,
            actor_user_id=actor_user_id,
            filename=stem,
            content_type=_TEXT_FORMAT_TO_CONTENT_TYPE[fmt],
            content=encoded,
            is_admin=is_admin,
            path=relative,
        )

    def update_text_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        actor_user_id: int,
        content: str,
        is_admin: bool = False,
    ) -> KnowledgeDocumentRow:
        """Overwrite md/txt content and mark the document pending for reindex."""
        assert_knowledge_usable(
            self._services.settings_repo.get, getattr(self._services, "provider_repo", None)
        )
        self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        if document.is_dir:
            raise LookupError("knowledge document not found")
        if document.content_type not in _TEXT_CONTENT_TYPES:
            raise ValueError("unsupported knowledge document content type: not editable text")
        encoded = content.encode("utf-8")
        limit = self.max_document_bytes()
        if len(encoded) > limit:
            raise ValueError(f"knowledge document size exceeds maximum of {limit} bytes")
        write_document(kb_id, document.id, document.filename, encoded)
        self._repo.update_document(
            doc_id,
            byte_size=len(encoded),
            status="pending",
            error_message="",
            chunk_count=0,
        )
        # The old structure described the old text.
        self._repo.set_derived(doc_id, None)
        refreshed = self._repo.get_document(doc_id)
        if refreshed is None:
            raise LookupError("knowledge document not found")
        return cast(KnowledgeDocumentRow, refreshed)

    def document_read_scope(
        self, actor_user_id: int, *, is_admin: bool = False
    ) -> tuple[set[str], set[str]]:
        """``(restricted, readable)`` document ids for this actor.

        ``restricted`` is every document that carries a file-level entry of its
        own; ``readable`` is the subset the actor may read. Both come from
        ``resource_acl``'s list entry point, so this cannot answer differently
        from the single-document check below it.
        """
        return (
            set(self._repo.document_acl_entries()),
            self._repo.readable_document_ids(user_id=actor_user_id, is_admin=is_admin),
        )

    def require_document_readable(
        self, document: KnowledgeDocumentRow, *, actor_user_id: int, is_admin: bool = False
    ) -> None:
        """Refuse a document whose own file-level entry excludes the actor.

        The base has already been checked by the caller; this is the second half
        of design §5.2 — a file rule narrows what the base allows and never
        widens it.
        """
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        if not may_read_document(document.id, restricted=restricted, readable=readable):
            raise KnowledgeAccessDenied(
                "knowledge document read access is required", access=ACCESS_READ
            )

    def get_readable_base(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        # The legacy share column is a write-only mirror: a share applied
        # through the sharing pipeline never lands there, so the ACL row is the
        # only thing that may decide this.
        role, unit_keys = self._repo.scope_for_user(actor_user_id)
        if may_read_knowledge_base(
            self._repo.acl_entry(kb_id),
            user_id=actor_user_id,
            role="admin" if is_admin else role,
            unit_keys=unit_keys,
        ):
            return base
        raise KnowledgeAccessDenied("knowledge base read access is required", access=ACCESS_READ)

    def get_writable_base(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        # Write is the entry's other half: the owner and administrators always
        # have it, and a share only carries it when the entry says ``write`` —
        # which is why a ``public`` base is readable by everyone and writable
        # only by its owner.
        entry = self._repo.acl_entry(kb_id)
        if entry is None:
            entry = AclEntry(
                resource_type="knowledge_base",
                resource_id=kb_id,
                owner_user_id=base.owner_user_id,
                visibility=VISIBILITY_PRIVATE,
                unit_key=None,
                version=0,
            )
        role, unit_keys = self._repo.scope_for_user(actor_user_id)
        if can_write(
            entry,
            user_id=actor_user_id,
            role="admin" if is_admin else role,
            unit_keys=unit_keys,
        ):
            return base
        raise KnowledgeAccessDenied("knowledge base write access is required", access=ACCESS_WRITE)

    def require_owner(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        if is_admin or base.owner_user_id == actor_user_id:
            return base
        raise KnowledgeAccessDenied("knowledge base owner access is required", access=ACCESS_WRITE)

    def upload_document(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        filename: str,
        content_type: str,
        content: bytes,
        is_admin: bool = False,
        path: str | None = None,
    ) -> KnowledgeDocumentRow:
        assert_knowledge_usable(
            self._services.settings_repo.get, getattr(self._services, "provider_repo", None)
        )
        base = self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        limit = self.max_document_bytes()
        if len(content) > limit:
            raise ValueError(f"knowledge document size exceeds maximum of {limit} bytes")
        rel = normalize_kb_path(path or filename)
        name = path_basename(rel)
        if not name:
            raise ValueError("invalid knowledge document filename")
        if (
            Path(name).suffix.lower() in OCR_IMAGE_SUFFIXES
            and not load_ocr_config(self._services.settings_repo.get).enabled
        ):
            raise ValueError("knowledge OCR must be enabled for image documents")
        resolved_type = _resolve_content_type(name, content_type)
        if resolved_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"unsupported knowledge document content type: {content_type}")
        # The per-base limit lives on the KB row (schema v10). 0 = unlimited.
        document = self._repo.create_document(
            kb_id=kb_id,
            filename=name,
            path=rel,
            content_type=resolved_type,
            byte_size=len(content),
            content_hash=document_digest(content),
            max_documents=base.max_documents,
        )
        try:
            write_document(kb_id, document.id, name, content)
        except Exception:
            self._repo.delete_document(document.id)
            raise
        return cast(KnowledgeDocumentRow, document)

    def replace_document_content(
        self,
        kb_id: str,
        doc_id: str,
        *,
        actor_user_id: int,
        path: str,
        content_type: str,
        content: bytes,
        is_admin: bool = False,
    ) -> KnowledgeDocumentRow:
        """Overwrite an existing document's bytes and reset it for reprocessing.

        The counterpart of :meth:`upload_document` for a source that refetches
        the same document (a URL data source): the row keeps its id — so its
        citations and index entries stay attached — while filename, size, type,
        and status follow the new bytes. A changed extension leaves no stale
        file behind.
        """
        assert_knowledge_usable(
            self._services.settings_repo.get, getattr(self._services, "provider_repo", None)
        )
        base = self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            raise ValueError(f"knowledge document {doc_id!r} is not in this knowledge base")
        limit = self.max_document_bytes()
        if len(content) > limit:
            raise ValueError(f"knowledge document size exceeds maximum of {limit} bytes")
        rel = normalize_kb_path(path or document.path)
        name = path_basename(rel)
        if not name:
            raise ValueError("invalid knowledge document filename")
        if (
            Path(name).suffix.lower() in OCR_IMAGE_SUFFIXES
            and not load_ocr_config(self._services.settings_repo.get).enabled
        ):
            raise ValueError("knowledge OCR must be enabled for image documents")
        resolved_type = _resolve_content_type(name, content_type)
        if resolved_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"unsupported knowledge document content type: {content_type}")
        previous_filename = document.filename
        write_document(kb_id, doc_id, name, content)
        if previous_filename != name:
            delete_document_file(kb_id, doc_id, previous_filename)
        self._repo.update_document(
            doc_id,
            filename=name,
            path=rel,
            content_type=resolved_type,
            byte_size=len(content),
            content_hash=document_digest(content),
            status="pending",
            error_message="",
            chunk_count=0,
        )
        refreshed = self._repo.get_document(doc_id)
        if refreshed is None:
            raise RuntimeError(f"knowledge document update failed: {doc_id}")
        return cast(KnowledgeDocumentRow, refreshed)

        name = path_basename(rel)
        if not name:
            raise ValueError("invalid knowledge document filename")
        if (
            Path(name).suffix.lower() in OCR_IMAGE_SUFFIXES
            and not load_ocr_config(self._services.settings_repo.get).enabled
        ):
            raise ValueError("knowledge OCR must be enabled for image documents")
        resolved_type = _resolve_content_type(name, content_type)
        if resolved_type not in _ALLOWED_CONTENT_TYPES:
            raise ValueError(f"unsupported knowledge document content type: {content_type}")
        # The per-base limit lives on the KB row (schema v10). 0 = unlimited.
        document = self._repo.create_document(
            kb_id=kb_id,
            filename=name,
            path=rel,
            content_type=resolved_type,
            byte_size=len(content),
            content_hash=document_digest(content),
            max_documents=base.max_documents,
        )
        try:
            write_document(kb_id, document.id, name, content)
        except Exception:
            self._repo.delete_document(document.id)
            raise
        return cast(KnowledgeDocumentRow, document)

    def delete_document(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> None:
        self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        removed = self._repo.delete_document(doc_id)
        for row in removed:
            if row.is_dir:
                continue
            KnowledgeIndex(kb_id).delete_doc(row.id)
            delete_document_file(kb_id, row.id, row.filename)

    def reindex_document(
        self, kb_id: str, doc_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeDocumentRow:
        assert_knowledge_usable(
            self._services.settings_repo.get, getattr(self._services, "provider_repo", None)
        )
        self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        if document.is_dir:
            raise ValueError("folders cannot be reindexed")
        self._repo.update_document(doc_id, status="pending", error_message="", chunk_count=0)
        self._repo.set_derived(doc_id, None)
        refreshed = self._repo.get_document(doc_id)
        if refreshed is None:
            raise LookupError("knowledge document not found")
        return cast(KnowledgeDocumentRow, refreshed)

    def rename_document(
        self,
        kb_id: str,
        doc_id: str,
        *,
        new_name: str,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> KnowledgeDocumentRow:
        """Rename a document (file or folder), rewriting descendant paths for folders."""
        self.get_writable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        document = self._repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id:
            raise LookupError("knowledge document not found")
        cleaned = (new_name or "").strip()
        if not cleaned or "/" in cleaned or "\\" in cleaned:
            raise ValueError("invalid knowledge document name")
        new_path = normalize_kb_path(f"{path_parent(document.path)}/{cleaned}")
        if new_path == document.path:
            return cast(KnowledgeDocumentRow, document)
        if self._repo.get_document_by_path(kb_id, new_path) is not None:
            raise ValueError("a knowledge document with this name already exists")
        result = self._repo.rename_document(kb_id, doc_id, cleaned)
        if result is None:
            raise LookupError("knowledge document not found")
        return cast(KnowledgeDocumentRow, result)

    def delete_base(self, kb_id: str, *, actor_user_id: int, is_admin: bool = False) -> None:
        self.require_owner(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)
        self._repo.delete_base(kb_id)
        delete_knowledge_base_files(kb_id)

    def _require_base(self, kb_id: str) -> KnowledgeBaseRow:
        base = self._repo.get_base(kb_id)
        if base is None:
            raise LookupError("knowledge base not found")
        return cast(KnowledgeBaseRow, base)
