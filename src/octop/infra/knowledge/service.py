"""Knowledge-base ownership checks and document upload orchestration.

Visibility is decided by ``resource_acl`` (see ``octop.infra.sharing``); the
legacy per-base share column is only still written as a compatibility mirror.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, cast

from octop.config import DEFAULT_MAX_UPLOAD_MB, upload_mb_to_bytes
from octop.infra.db.repos.knowledge import KnowledgeBaseRow, KnowledgeDocumentRow
from octop.infra.knowledge.files import (
    delete_document_file,
    delete_knowledge_base_files,
    document_digest,
    document_path,
    write_document,
)
from octop.infra.knowledge.gate import assert_knowledge_usable
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.ocr import (
    OCR_IMAGE_SUFFIXES,
    load_ocr_config,
    optional_ocr_extractor,
)
from octop.infra.knowledge.parse import parse_document
from octop.infra.knowledge.relpath import normalize_kb_path, path_basename, path_parent
from octop.infra.knowledge.scope import (
    may_read_document,
    may_read_knowledge_base,
    readable_documents,
)
from octop.infra.knowledge.search import (
    DEFAULT_SEARCH_K,
    SearchHit,
    query_terms,
    search_base,
    snippet,
)
from octop.infra.sharing import user_scope

MAX_DOCS_PER_KB = 100
MAX_DOCUMENT_BYTES = upload_mb_to_bytes(DEFAULT_MAX_UPLOAD_MB)
# Upper bound for the per-base max_documents field. Mirrors Field(le=10000).
MAX_KB_MAX_DOCUMENTS = 10_000
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

    def search(
        self,
        *,
        actor_user_id: int,
        query: str,
        kb_id: str | None = None,
        limit: int = DEFAULT_SEARCH_K,
        is_admin: bool = False,
    ) -> list[SearchHit]:
        """Keyword and full-text search over the knowledge an actor may read.

        The scope is the same one chat retrieval applies — the bases the actor
        can read (``list_visible`` resolves the ACL, admin bypass included) and
        only their ``ready`` documents — so search cannot surface what a citation
        or a download would refuse (design §14), and the two paths cannot drift
        apart. An indexing, failed, or deleted-pending file is not searchable.

        Passing *kb_id* narrows the search to one base and checks read access to
        it; leaving it out searches every base the actor can read.
        """
        cleaned = (query or "").strip()
        if not cleaned or limit <= 0:
            return []
        if kb_id is not None:
            bases = [self.get_readable_base(kb_id, actor_user_id=actor_user_id, is_admin=is_admin)]
        else:
            bases = self.list_visible_bases(actor_user_id=actor_user_id)
        terms = query_terms(cleaned)
        restricted, readable = self.document_read_scope(actor_user_id, is_admin=is_admin)
        hits: list[SearchHit] = []
        for base in bases:
            ready = {
                document.id: document
                for document in readable_documents(
                    self._repo.list_documents(base.id), restricted=restricted, readable=readable
                )
                if document.status == "ready" and not document.is_dir
            }
            for hit, document in search_base(
                base.id, query=cleaned, ready_documents=ready, limit=limit
            ):
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
                    )
                )
        hits.sort(key=lambda row: (-row.score, row.base_name, row.path, row.ordinal))
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
            raise PermissionError("knowledge document read access is required")

    def get_readable_base(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        # The legacy share column is a write-only mirror: a share applied
        # through the sharing pipeline never lands there, so the ACL row is the
        # only thing that may decide this.
        role, unit_key = user_scope(self._services.user_repo.get(actor_user_id))
        if may_read_knowledge_base(
            self._repo.acl_entry(kb_id),
            user_id=actor_user_id,
            role="admin" if is_admin else role,
            unit_key=unit_key,
        ):
            return base
        raise PermissionError("knowledge base read access is required")

    def get_writable_base(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        if is_admin or base.owner_user_id == actor_user_id:
            return base
        raise PermissionError("knowledge base write access is required")

    def require_owner(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> KnowledgeBaseRow:
        base = self._require_base(kb_id)
        if is_admin or base.owner_user_id == actor_user_id:
            return base
        raise PermissionError("knowledge base owner access is required")

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
