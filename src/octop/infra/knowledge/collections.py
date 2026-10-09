"""Independent personal two-view collections over source-located knowledge segments."""

from __future__ import annotations

import builtins
import json
import logging
import os
import re
import tempfile
import threading
import unicodedata
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, Literal, cast
from urllib.parse import urlencode

from octop.i18n import tr
from octop.infra.db.repos.knowledge_collections import (
    KnowledgeCollectionRow,
    KnowledgeCollectionsRepo,
)
from octop.infra.errors import ErrorCode, OctopError
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.relpath import ancestor_dirs, normalize_kb_path
from octop.infra.knowledge.search import SearchHit, normalize_query, query_terms, snippet
from octop.infra.utils.locale import normalize_locale

_LOG = logging.getLogger(__name__)


_MATERIALIZATION_LOCKS: tuple[Any, ...] = tuple(threading.RLock() for _ in range(64))


def _materialization_lock(collection_id: str) -> Any:
    return _MATERIALIZATION_LOCKS[hash(collection_id) % len(_MATERIALIZATION_LOCKS)]


def _summary(text: str) -> str:
    """Return an explicitly extractive preview, never a generated summary."""
    sentences = re.split(r"(?<=[.!?。！？])\s+", text.strip(), maxsplit=2)
    return " ".join(sentences[:2]).strip()[:320]


def _version(document: Any) -> str:
    return str(document.content_hash or "")


def _within(target: KnowledgeCollectionRow, path: str) -> bool:
    path = normalize_kb_path(path)
    root = normalize_kb_path(target.target_path)
    if target.target_kind == "file":
        return path == root
    return not root or path == root or path.startswith(root + "/")


def _normal_text(value: str) -> str:
    return " ".join(unicodedata.normalize("NFKC", value).split())


def _normalize_keywords(values: Sequence[str]) -> builtins.list[str]:
    result: builtins.list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            raise ValueError("keywords must be text")
        normalized = _normal_text(value)
        if not normalized:
            continue
        if len(normalized) > 200:
            raise ValueError("keyword is too long")
        key = normalized.casefold()
        if key not in seen:
            seen.add(key)
            result.append(normalized)
    if len(result) > 32:
        raise ValueError("too many keywords")
    return result


def _normalize_name(value: str) -> str:
    normalized = _normal_text(value)
    if not normalized or len(normalized) > 200:
        raise ValueError("collection name must contain 1 to 200 characters")
    return normalized


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        dir=path.parent, prefix=f".{path.name}.", suffix=".tmp"
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


class KnowledgeCollectionService:
    """Persist owner-private collections and render their two Markdown views."""

    def __init__(self, services: Any) -> None:
        self._services = services
        self._repo: KnowledgeCollectionsRepo = services.knowledge_collections_repo
        self._knowledge = services.knowledge_repo

    def _base(self, kb_id: str, actor_user_id: int, is_admin: bool) -> Any:
        from octop.infra.knowledge.service import KnowledgeService

        return KnowledgeService(self._services).get_readable_base(
            kb_id, actor_user_id=actor_user_id, is_admin=is_admin
        )

    def _scope(self, kb_id: str, actor_user_id: int, is_admin: bool) -> tuple[set[str], set[str]]:
        from octop.infra.knowledge.service import KnowledgeService

        return KnowledgeService(self._services).document_read_scope(
            actor_user_id, is_admin=is_admin
        )

    @staticmethod
    def _payload(collection: KnowledgeCollectionRow, coverage: dict[str, int]) -> dict[str, Any]:
        total = coverage["total"]
        if total == 0 or coverage["pending"] == total:
            status = "pending"
        elif (
            coverage["pending"] == 0
            and coverage["failed"] == 0
            and coverage["unsupported"] == 0
            and coverage["indexed"] == total
        ):
            status = "ready"
        else:
            status = "partial"
        return {
            "id": collection.id,
            "kb_id": collection.kb_id,
            "target_document_id": collection.target_document_id,
            "target_path": collection.target_path,
            "target_kind": collection.target_kind,
            "name": collection.name,
            "keywords": list(collection.keywords),
            "status": status,
            "coverage": coverage,
            "created_at": collection.created_at,
            "updated_at": collection.updated_at,
        }

    def _sync_target(self, collection: KnowledgeCollectionRow) -> KnowledgeCollectionRow:
        target = self._knowledge.get_document(collection.target_document_id)
        if target is None or target.kb_id != collection.kb_id:
            return collection
        current_path = normalize_kb_path(target.path)
        if current_path != collection.target_path:
            updated = self._repo.update_target_path(
                collection.id, collection.owner_user_id, current_path
            )
            if updated is not None:
                return updated
        return collection

    def _eligible(
        self, collection: KnowledgeCollectionRow, actor_user_id: int, is_admin: bool
    ) -> builtins.list[Any]:
        collection = self._sync_target(collection)
        target = self._knowledge.get_document(collection.target_document_id)
        if target is None or target.kb_id != collection.kb_id:
            return []
        restricted, readable = self._scope(collection.kb_id, actor_user_id, is_admin)
        from octop.infra.knowledge.scope import may_read_document

        if not may_read_document(target.id, restricted=restricted, readable=readable):
            return []
        documents = self._knowledge.list_documents(collection.kb_id)
        return [
            document
            for document in documents
            if not document.is_dir
            and _within(collection, document.path)
            and may_read_document(document.id, restricted=restricted, readable=readable)
        ]

    def _coverage(
        self, collection: KnowledgeCollectionRow, actor_user_id: int, is_admin: bool
    ) -> dict[str, int]:
        docs = self._eligible(collection, actor_user_id, is_admin)
        counts = {
            "total": len(docs),
            "indexed": 0,
            "read": 0,
            "pending": 0,
            "failed": 0,
            "unsupported": 0,
        }
        entries = self._repo.entries(collection.id)
        read_docs: set[str] = set()
        for entry in entries:
            source = self._knowledge.get_document(entry["document_id"])
            if (
                source is not None
                and entry["read"]
                and entry["segment_id"]
                and entry["source_version"] == _version(source)
            ):
                read_docs.add(entry["document_id"])
        for document in docs:
            if document.status == "ready" and document.chunk_count:
                counts["indexed"] += 1
            elif document.status == "unsupported":
                counts["unsupported"] += 1
            elif document.status in {"failed", "password_required"}:
                counts["failed"] += 1
            else:
                counts["pending"] += 1
            if document.id in read_docs:
                counts["read"] += 1
        return counts

    def list(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> builtins.list[dict[str, Any]]:
        self._base(kb_id, actor_user_id, is_admin)
        restricted, readable = self._scope(kb_id, actor_user_id, is_admin)
        from octop.infra.knowledge.scope import may_read_document

        visible = []
        for collection in self._repo.list_for_owner(kb_id, actor_user_id):
            collection = self._sync_target(collection)
            target = self._knowledge.get_document(collection.target_document_id)
            if target is not None and not may_read_document(
                target.id, restricted=restricted, readable=readable
            ):
                continue
            visible.append(
                self._payload(collection, self._coverage(collection, actor_user_id, is_admin))
            )
        return visible

    def create(
        self,
        kb_id: str,
        document_ids: Sequence[str],
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> builtins.list[dict[str, Any]]:
        self._base(kb_id, actor_user_id, is_admin)
        docs = self._knowledge.list_documents(kb_id)
        by_id = {document.id: document for document in docs}
        restricted, readable = self._scope(kb_id, actor_user_id, is_admin)
        from octop.infra.knowledge.scope import may_read_document

        targets = []
        for doc_id in dict.fromkeys(document_ids):
            document = by_id.get(doc_id)
            if document is None or not may_read_document(
                doc_id, restricted=restricted, readable=readable
            ):
                raise LookupError("knowledge document not found")
            targets.append(document)
        collections = []
        for target in targets:
            collection = self._repo.create(
                kb_id=kb_id,
                owner_user_id=actor_user_id,
                target_document_id=target.id,
                target_path=normalize_kb_path(target.path),
                target_kind="folder" if target.is_dir else "file",
                name=_normal_text(target.filename)[:200] or target.id,
            )
            # Membership records identities only; no document body is read or parsed.
            members = [
                document.id
                for document in docs
                if not document.is_dir
                and _within(collection, document.path)
                and may_read_document(document.id, restricted=restricted, readable=readable)
            ]
            self._repo.add_members(collection.id, members)
            rendered = self._render(collection, actor_user_id, is_admin)
            collections.append(rendered["collection"])
        return collections

    def _get(
        self, kb_id: str, collection_id: str, actor_user_id: int, is_admin: bool
    ) -> KnowledgeCollectionRow:
        self._base(kb_id, actor_user_id, is_admin)
        row = self._repo.get(collection_id, actor_user_id)
        if row is None or row.kb_id != kb_id:
            raise LookupError("knowledge collection not found")
        row = self._sync_target(row)
        target = self._knowledge.get_document(row.target_document_id)
        if target is not None:
            restricted, readable = self._scope(kb_id, actor_user_id, is_admin)
            from octop.infra.knowledge.scope import may_read_document

            if not may_read_document(target.id, restricted=restricted, readable=readable):
                raise LookupError("knowledge collection not found")
        return row

    def _locale(self, actor_user_id: int) -> str:
        user = self._services.user_repo.get(actor_user_id)
        if user is None:
            return normalize_locale(None)
        from octop.infra.users.preferences import parse_preferences_json

        preferences = parse_preferences_json(user.preferences_json)
        preferred = preferences.get("locale")
        locale = preferred if isinstance(preferred, str) else user.locale
        return normalize_locale(locale)

    def _t(self, key: str, locale: str) -> str:
        return tr(f"knowledge.collections.{key}", locale)

    def _source_link(self, collection: KnowledgeCollectionRow, document_id: str) -> str:
        params = urlencode({"kb": collection.kb_id, "doc": document_id})
        return f"/knowledge-bases?{params}"

    def _entry_lines(
        self,
        collection: KnowledgeCollectionRow,
        entry: dict[str, Any],
        locale: str,
        *,
        include_text: bool,
    ) -> builtins.list[str]:
        document = self._knowledge.get_document(entry["document_id"])
        path = document.path if document else entry["document_id"]
        filename = document.filename if document else path.rsplit("/", 1)[-1]
        locator = json.dumps(entry["locator"], ensure_ascii=False, sort_keys=True)
        link = self._source_link(collection, entry["document_id"])
        lines = [
            f"## [{filename}]({link})",
            f"- {self._t('source', locale)}: {path}",
            *(
                [f"- {self._t('source', locale)}: {document.source_path}"]
                if document is not None and document.source_path and document.source_path != path
                else []
            ),
            f"- {self._t('document_id', locale)}: `{entry['document_id']}`",
            f"- {self._t('segment_id', locale)}: `{entry['segment_id']}`",
            f"- {self._t('location', locale)}: `{locator}`",
            f"- {self._t('version', locale)}: `{entry['source_version']}`",
        ]
        if entry["keyword_matches"]:
            lines.append(
                f"- {self._t('keyword_label', locale)}: {', '.join(entry['keyword_matches'])}"
            )
        if entry["metadata_matches"]:
            lines.append(
                f"- {self._t('metadata_match', locale)}: {', '.join(entry['metadata_matches'])}"
            )
        if entry["read"] and entry["segment_id"] and include_text:
            lines.append(f"- {self._t('summary', locale)}: {entry['extractive_summary']}")
        if include_text and entry["text"]:
            lines += ["", f"{self._t('excerpt', locale)}:", entry["text"]]
        elif entry["keyword_matches"]:
            lines.append(f"- {self._t('keyword_reference', locale)}: `{entry['segment_id']}`")
        lines.append("")
        return lines

    def _markdown(
        self,
        collection: KnowledgeCollectionRow,
        entries: builtins.list[dict[str, Any]],
        locale: str,
    ) -> tuple[str, str]:
        keyword_entries = [
            entry for entry in entries if entry["keyword_matches"] or entry["metadata_matches"]
        ]
        reads = [entry for entry in entries if entry["read"] and entry["segment_id"]]
        keyword_lines = [f"# {self._t('keyword_title', locale)}", ""]
        if not collection.keywords:
            keyword_lines += [self._t("keywords_empty", locale), ""]
        elif keyword_entries:
            keyword_lines += [self._t("unverified_notice", locale), ""]
        for entry in keyword_entries:
            keyword_lines += self._entry_lines(
                collection,
                entry,
                locale,
                include_text=bool(entry["keyword_matches"]),
            )
        reading_lines = [f"# {self._t('reading_title', locale)}", ""]
        if not reads:
            reading_lines += [self._t("reading_empty", locale), ""]
        else:
            reading_lines += [self._t("unverified_notice", locale), ""]
        for entry in reads:
            reading_lines += self._entry_lines(
                collection,
                entry,
                locale,
                include_text=not bool(entry["keyword_matches"]),
            )
        return (
            "\n".join(keyword_lines).rstrip() + "\n",
            "\n".join(reading_lines).rstrip() + "\n",
        )

    def _clear_materialization_unlocked(self, collection: KnowledgeCollectionRow) -> None:
        directory = (
            self._services.paths.knowledge_collections_dir
            / str(collection.owner_user_id)
            / collection.id
        )
        for name in ("keyword-index.md", "reading-index.md"):
            (directory / name).unlink(missing_ok=True)

    def _clear_materialization(self, collection: KnowledgeCollectionRow) -> None:
        with _materialization_lock(collection.id):
            self._clear_materialization_unlocked(collection)

    def _render(
        self,
        collection: KnowledgeCollectionRow,
        actor_user_id: int,
        is_admin: bool,
    ) -> dict[str, Any]:
        with _materialization_lock(collection.id):
            current = self._repo.get(collection.id, collection.owner_user_id)
            if current is None:
                self._clear_materialization_unlocked(collection)
                raise LookupError("knowledge collection not found")
            collection = self._sync_target(current)
            try:
                self._base(collection.kb_id, actor_user_id, is_admin)
            except (LookupError, PermissionError):
                self._repo.delete_entries(collection.id)
                self._clear_materialization_unlocked(collection)
                return {
                    "collection": self._payload(
                        collection,
                        {
                            "total": 0,
                            "indexed": 0,
                            "read": 0,
                            "pending": 0,
                            "failed": 0,
                            "unsupported": 0,
                        },
                    ),
                    "keyword_document": "",
                    "reading_document": "",
                    "storage_path": str(
                        self._services.paths.knowledge_collections_dir
                        / str(collection.owner_user_id)
                        / collection.id
                    ),
                }
            target = self._knowledge.get_document(collection.target_document_id)
            restricted, readable = self._scope(collection.kb_id, actor_user_id, is_admin)
            from octop.infra.knowledge.scope import may_read_document

            if (
                target is None
                or target.kb_id != collection.kb_id
                or not may_read_document(target.id, restricted=restricted, readable=readable)
            ):
                self._repo.delete_entries(collection.id)
            for entry in self._repo.entries(collection.id):
                document = self._knowledge.get_document(entry["document_id"])
                if (
                    document is None
                    or document.kb_id != collection.kb_id
                    or not _within(collection, document.path)
                    or not may_read_document(document.id, restricted=restricted, readable=readable)
                ):
                    self._repo.remove_doc_entries(collection.id, entry["document_id"])
                    continue
                if entry["source_version"] != _version(document):
                    self._repo.remove_doc_entries(collection.id, entry["document_id"])
                    continue
                if not entry["segment_id"]:
                    metadata_matches = self._metadata_matches(collection, document)
                    if metadata_matches:
                        self._repo.put_entry(
                            collection.id,
                            document_id=entry["document_id"],
                            source_version=entry["source_version"],
                            segment_id="",
                            ordinal=-1,
                            locator={"kind": "file"},
                            text="",
                            summary="",
                            read=False,
                            keyword_matches=(),
                            metadata_matches=metadata_matches,
                        )
                    else:
                        self._repo.delete_entry(
                            collection.id,
                            entry["document_id"],
                            entry["source_version"],
                            "",
                        )
                elif document.status != "ready":
                    self._repo.delete_entry(
                        collection.id,
                        entry["document_id"],
                        entry["source_version"],
                        entry["segment_id"],
                    )
            eligible_ids = {
                document.id for document in self._eligible(collection, actor_user_id, is_admin)
            }
            entries = [
                entry
                for entry in self._repo.entries(collection.id)
                if entry["document_id"] in eligible_ids
            ]
            locale = self._locale(actor_user_id)
            keyword_document, reading_document = self._markdown(collection, entries, locale)
            coverage = self._coverage(collection, actor_user_id, is_admin)
            coverage_line = (
                f"{self._t('coverage', locale)}: "
                f"{self._t('total', locale)} {coverage['total']}, "
                f"{self._t('indexed', locale)} {coverage['indexed']}, "
                f"{self._t('read', locale)} {coverage['read']}, "
                f"{self._t('pending', locale)} {coverage['pending']}, "
                f"{self._t('failed', locale)} {coverage['failed']}, "
                f"{self._t('unsupported', locale)} {coverage['unsupported']}"
            )
            keyword_document = keyword_document.replace("\n", f"\n{coverage_line}\n", 1)
            reading_document = reading_document.replace("\n", f"\n{coverage_line}\n", 1)
            directory = (
                self._services.paths.knowledge_collections_dir
                / str(collection.owner_user_id)
                / collection.id
            )
            _atomic_write(directory / "keyword-index.md", keyword_document)
            _atomic_write(directory / "reading-index.md", reading_document)
            return {
                "collection": self._payload(collection, coverage),
                "keyword_document": keyword_document,
                "reading_document": reading_document,
                "storage_path": str(directory),
            }

    def update(
        self,
        kb_id: str,
        collection_id: str,
        *,
        actor_user_id: int,
        keywords: Sequence[str],
        name: str | None = None,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        self._get(kb_id, collection_id, actor_user_id, is_admin)
        cleaned = _normalize_keywords(keywords)
        cleaned_name = _normalize_name(name) if name is not None else None
        row = self._repo.update(
            collection_id,
            actor_user_id,
            keywords=cleaned,
            name=cleaned_name,
        )
        if row is None:
            raise LookupError("knowledge collection not found")
        self._reclassify(row)
        if row.keywords:
            for document in self._eligible(row, actor_user_id, is_admin):
                self.refresh_for_document(kb_id, document.id)
        return cast(dict[str, Any], self._render(row, actor_user_id, is_admin)["collection"])

    def _reclassify(self, collection: KnowledgeCollectionRow) -> None:
        keywords = tuple(normalize_query(keyword) for keyword in collection.keywords)
        originals = collection.keywords
        for entry in self._repo.entries(collection.id):
            document = self._knowledge.get_document(entry["document_id"])
            metadata_text = (
                f"{document.filename} {document.path} {document.source_path or ''}"
                if document is not None
                else ""
            )
            content_matches = [
                original
                for normalized, original in zip(keywords, originals, strict=True)
                if normalized and normalized in normalize_query(entry["text"])
            ]
            metadata_matches = (
                [
                    original
                    for normalized, original in zip(keywords, originals, strict=True)
                    if normalized and normalized in normalize_query(metadata_text)
                ]
                if not entry["segment_id"]
                else []
            )
            if not content_matches and not metadata_matches and not entry["read"]:
                self._repo.delete_entry(
                    collection.id,
                    entry["document_id"],
                    entry["source_version"],
                    entry["segment_id"],
                )
                continue
            self._repo.put_entry(
                collection.id,
                document_id=entry["document_id"],
                source_version=entry["source_version"],
                segment_id=entry["segment_id"],
                ordinal=entry["ordinal"],
                locator=entry["locator"],
                text=entry["text"],
                summary=entry["extractive_summary"],
                read=bool(entry["read"]),
                keyword_matches=content_matches,
                metadata_matches=metadata_matches,
            )

    def generate(
        self,
        kb_id: str,
        collection_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        collection = self._get(kb_id, collection_id, actor_user_id, is_admin)
        errors: builtins.list[Exception] = []
        if collection.keywords:
            from octop.infra.knowledge.gate import assert_knowledge_usable

            assert_knowledge_usable(
                self._services.settings_repo.get,
                getattr(self._services, "provider_repo", None),
            )
            from octop.infra.knowledge.jobs import _process_current_document

            for document in self._eligible(collection, actor_user_id, is_admin):
                needs_extraction = document.status != "ready" or not document.chunk_count
                if document.status == "ready" and document.chunk_count:
                    current_segments = sum(
                        1
                        for hit in KnowledgeIndex(kb_id).iter_segments(document.id)
                        if hit.metadata.get("version") == _version(document)
                    )
                    needs_extraction = current_segments != document.chunk_count
                if document.status != "unsupported" and needs_extraction:
                    try:
                        _process_current_document(self._services, kb_id, document.id)
                    except Exception as error:
                        _LOG.exception(
                            "collection source extraction failed for document %s",
                            document.id,
                        )
                        errors.append(error)
        for document in self._eligible(collection, actor_user_id, is_admin):
            self.refresh_for_document(kb_id, document.id)
        result = self._render(collection, actor_user_id, is_admin)["collection"]
        if errors:
            if isinstance(errors[0], ValueError):
                raise OctopError.localized(ErrorCode.KNOWLEDGE_INDEX_NOT_READY) from errors[0]
            raise errors[0]
        return cast(dict[str, Any], result)

    def documents(
        self,
        kb_id: str,
        collection_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> dict[str, Any]:
        collection = self._get(kb_id, collection_id, actor_user_id, is_admin)
        return self._render(collection, actor_user_id, is_admin)

    def delete(
        self,
        kb_id: str,
        collection_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> None:
        collection = self._get(kb_id, collection_id, actor_user_id, is_admin)
        with _materialization_lock(collection.id):
            self._repo.delete(collection.id, actor_user_id)
            self._clear_materialization_unlocked(collection)

    def record_read(
        self,
        kb_id: str,
        doc_id: str,
        evidence: Mapping[str, object],
        *,
        actor_user_id: int,
        is_admin: bool = False,
    ) -> None:
        self._base(kb_id, actor_user_id, is_admin)
        if evidence.get("verified") is not True or not str(evidence.get("text") or "").strip():
            return
        document = self._knowledge.get_document(doc_id)
        if (
            document is None
            or document.kb_id != kb_id
            or str(evidence.get("version") or "") != _version(document)
        ):
            return
        segment_id = str(evidence.get("segment_id") or "")
        if not segment_id:
            return
        hit = KnowledgeIndex(kb_id).get_segment(doc_id, segment_id)
        if hit is None or hit.metadata.get("version") != _version(document):
            return
        restricted, readable = self._scope(kb_id, actor_user_id, is_admin)
        from octop.infra.knowledge.scope import may_read_document

        if not may_read_document(doc_id, restricted=restricted, readable=readable):
            return
        for collection in self._repo.collections_for_document(kb_id, doc_id):
            with _materialization_lock(collection.id):
                current_collection = self._repo.get(collection.id, collection.owner_user_id)
                if current_collection is None:
                    continue
                collection = current_collection
                if collection.owner_user_id != actor_user_id:
                    continue
                collection = self._sync_target(collection)
                if not _within(collection, document.path):
                    continue
                if not may_read_document(
                    collection.target_document_id,
                    restricted=restricted,
                    readable=readable,
                ):
                    continue
                matches = [
                    keyword
                    for keyword in collection.keywords
                    if normalize_query(keyword) in normalize_query(hit.text)
                ]
                self._repo.add_members(collection.id, [doc_id])
                self._repo.put_entry(
                    collection.id,
                    document_id=doc_id,
                    source_version=_version(document),
                    segment_id=hit.chunk_id,
                    ordinal=hit.ordinal,
                    locator=cast(Mapping[str, object], hit.metadata.get("locator", {})),
                    text=hit.text,
                    summary=_summary(hit.text),
                    read=True,
                    keyword_matches=matches,
                )
                self._render(collection, actor_user_id, is_admin)

    def _metadata_matches(
        self, collection: KnowledgeCollectionRow, document: Any
    ) -> builtins.list[str]:
        metadata = normalize_query(
            f"{document.filename} {document.path} {document.source_path or ''}"
        )
        return [keyword for keyword in collection.keywords if normalize_query(keyword) in metadata]

    def refresh_for_document(self, kb_id: str, doc_id: str) -> None:
        from octop.infra.knowledge.scope import may_read_document

        document = self._knowledge.get_document(doc_id)
        if not doc_id:
            for collection in self._repo.list_for_base(kb_id):
                try:
                    self._base(kb_id, collection.owner_user_id, False)
                except (LookupError, PermissionError):
                    self._repo.delete_entries(collection.id)
                    self._clear_materialization(collection)
                    continue
                self._render(collection, collection.owner_user_id, False)
            return
        if document is None or document.kb_id != kb_id:
            for collection in self._repo.collections_for_document(kb_id, doc_id):
                with _materialization_lock(collection.id):
                    current_collection = self._repo.get(collection.id, collection.owner_user_id)
                    if current_collection is None:
                        continue
                    collection = current_collection
                    self._repo.remove_doc_entries(collection.id, doc_id)
                    self._repo.remove_member(collection.id, doc_id)
                    try:
                        self._render(collection, collection.owner_user_id, False)
                    except (LookupError, PermissionError):
                        continue
            return
        for initial in self._repo.collections_for_document(kb_id, doc_id):
            with _materialization_lock(initial.id):
                current_collection = self._repo.get(initial.id, initial.owner_user_id)
                if current_collection is None:
                    continue
                initial = current_collection
                collection = self._sync_target(initial)
                try:
                    self._base(kb_id, collection.owner_user_id, False)
                except (LookupError, PermissionError):
                    self._repo.delete_entries(collection.id)
                    self._clear_materialization(collection)
                    continue
                target = self._knowledge.get_document(collection.target_document_id)
                restricted, readable = self._scope(kb_id, collection.owner_user_id, False)
                target_visible = (
                    target is not None
                    and target.kb_id == kb_id
                    and may_read_document(target.id, restricted=restricted, readable=readable)
                )
                if not target_visible:
                    self._repo.delete_entries(collection.id)
                    self._clear_materialization(collection)
                    continue
                if not _within(collection, document.path):
                    self._repo.remove_doc_entries(collection.id, doc_id)
                    self._repo.remove_member(collection.id, doc_id)
                    self._render(collection, collection.owner_user_id, False)
                    continue
                if not may_read_document(doc_id, restricted=restricted, readable=readable):
                    self._repo.remove_doc_entries(collection.id, doc_id)
                    self._render(collection, collection.owner_user_id, False)
                    continue
                if document.is_dir:
                    for child in self._eligible(collection, collection.owner_user_id, False):
                        for entry in self._repo.entries(collection.id):
                            if entry["document_id"] == child.id and not entry["segment_id"]:
                                matches = self._metadata_matches(collection, child)
                                if matches:
                                    self._repo.put_entry(
                                        collection.id,
                                        document_id=child.id,
                                        source_version=_version(child),
                                        segment_id="",
                                        ordinal=-1,
                                        locator={"kind": "file"},
                                        text="",
                                        summary="",
                                        read=False,
                                        keyword_matches=(),
                                        metadata_matches=matches,
                                    )
                                else:
                                    self._repo.delete_entry(
                                        collection.id, child.id, entry["source_version"], ""
                                    )
                    self._render(collection, collection.owner_user_id, False)
                    continue
                self._repo.add_members(collection.id, [doc_id])
                self._repo.remove_doc_entries(
                    collection.id, doc_id, keep_version=_version(document)
                )
                metadata_matches = self._metadata_matches(collection, document)
                if metadata_matches:
                    self._repo.put_entry(
                        collection.id,
                        document_id=doc_id,
                        source_version=_version(document),
                        segment_id="",
                        ordinal=-1,
                        locator={"kind": "file"},
                        text="",
                        summary="",
                        read=False,
                        keyword_matches=(),
                        metadata_matches=metadata_matches,
                    )
                else:
                    self._repo.delete_entry(collection.id, doc_id, _version(document), "")
                existing = {
                    (entry["source_version"], entry["segment_id"]): entry
                    for entry in self._repo.entries(collection.id)
                    if entry["document_id"] == doc_id
                }
                self._repo.delete_content_entries(collection.id, doc_id)
                if document.status == "ready" and document.chunk_count:
                    index = KnowledgeIndex(kb_id)
                    for hit in index.iter_segments(doc_id):
                        if hit.metadata.get("version") != _version(document):
                            continue
                        old = existing.get((_version(document), hit.chunk_id))
                        matches = [
                            keyword
                            for keyword in collection.keywords
                            if normalize_query(keyword) in normalize_query(hit.text)
                        ]
                        is_read = bool(old and old["read"])
                        if not matches and not is_read:
                            continue
                        self._repo.put_entry(
                            collection.id,
                            document_id=doc_id,
                            source_version=_version(document),
                            segment_id=hit.chunk_id,
                            ordinal=hit.ordinal,
                            locator=cast(Mapping[str, object], hit.metadata.get("locator", {})),
                            text=hit.text,
                            summary=(old["extractive_summary"] if old else "")
                            or (_summary(hit.text) if is_read else ""),
                            read=is_read,
                            keyword_matches=matches,
                        )
                self._render(collection, collection.owner_user_id, False)

    def _excluded_documents(
        self,
        actor_user_id: int,
        documents: builtins.list[Any],
        all_documents: builtins.list[Any],
        restricted: set[str],
        readable: set[str],
    ) -> set[str]:
        from octop.infra.knowledge.scope import may_read_document

        by_id = {document.id: document for document in all_documents}
        by_path = {document.path: document for document in all_documents if document.is_dir}
        rule_by_path: dict[str, Any] = {}
        rules = self._services.knowledge_search_rules_repo.list_for_user(user_id=actor_user_id)
        for rule in rules:
            target = by_id.get(rule.document_id)
            if target is not None and may_read_document(
                target.id, restricted=restricted, readable=readable
            ):
                rule_by_path[target.path] = rule
        excluded: set[str] = set()
        for document in documents:
            selected = None
            for folder_path in ancestor_dirs(document.path):
                folder = by_path.get(folder_path)
                if folder is None:
                    selected = None
                    break
                if folder_path in rule_by_path:
                    selected = rule_by_path[folder_path]
            selected = rule_by_path.get(document.path) or selected
            if selected is not None and selected.mode == "exclude":
                excluded.add(document.id)
        return excluded

    def search(
        self,
        kb_ids: Sequence[str],
        query: str,
        *,
        actor_user_id: int,
        limit: int = 20,
        is_admin: bool = False,
    ) -> builtins.list[SearchHit]:
        phrase = normalize_query(query)
        if not phrase or limit <= 0:
            return []
        hits: builtins.list[SearchHit] = []
        seen: set[tuple[str, str, str, str]] = set()
        for kb_id in dict.fromkeys(kb_ids):
            try:
                base = self._base(kb_id, actor_user_id, is_admin)
            except (LookupError, PermissionError):
                continue
            restricted, readable = self._scope(kb_id, actor_user_id, is_admin)
            all_documents = self._knowledge.list_documents(kb_id)
            excluded = self._excluded_documents(
                actor_user_id,
                [document for document in all_documents if not document.is_dir],
                all_documents,
                restricted,
                readable,
            )
            for initial in self._repo.list_for_owner(kb_id, actor_user_id):
                collection = self._sync_target(initial)
                permitted = {
                    document.id for document in self._eligible(collection, actor_user_id, is_admin)
                }
                self._render(collection, actor_user_id, is_admin)
                for entry in self._repo.entries(collection.id):
                    document = self._knowledge.get_document(entry["document_id"])
                    if (
                        document is None
                        or document.kb_id != kb_id
                        or document.id not in permitted
                        or document.id in excluded
                        or entry["source_version"] != _version(document)
                        or not _within(collection, document.path)
                    ):
                        continue
                    if (
                        not entry["keyword_matches"]
                        and not entry["metadata_matches"]
                        and not entry["read"]
                    ):
                        continue
                    content_matches = normalize_query(entry["text"])
                    metadata_matches = normalize_query(
                        f"{document.filename} {document.path} {document.source_path or ''}"
                    )
                    matched_keywords = [
                        keyword
                        for keyword in (*entry["keyword_matches"], *entry["metadata_matches"])
                        if phrase in normalize_query(str(keyword))
                    ]
                    metadata_hit = bool(entry["metadata_matches"]) and (
                        phrase in metadata_matches or bool(matched_keywords)
                    )
                    content_hit = bool(entry["text"]) and (
                        phrase in content_matches or bool(matched_keywords)
                    )
                    if not metadata_hit and not content_hit:
                        continue
                    segment_id = str(entry["segment_id"])
                    identity = (kb_id, document.id, entry["source_version"], segment_id)
                    if identity in seen:
                        continue
                    seen.add(identity)
                    match_kind: Literal["content", "filename"]
                    locator: dict[str, object]
                    if metadata_hit and not content_hit:
                        hit_snippet = document.filename
                        ordinal = -1
                        locator = {"kind": "file"}
                        segment_id = ""
                        match_kind = "filename"
                    else:
                        hit_snippet = snippet(entry["text"], query_terms(query))
                        ordinal = int(entry["ordinal"])
                        locator = dict(entry["locator"])
                        match_kind = "content"
                    hits.append(
                        SearchHit(
                            kb_id=kb_id,
                            base_name=base.name,
                            document_id=document.id,
                            filename=document.filename,
                            path=document.path,
                            source_path=document.source_path,
                            title=document.filename,
                            ordinal=ordinal,
                            match_kind=match_kind,
                            collection_id=collection.id,
                            collection_document=(
                                "keyword"
                                if entry["keyword_matches"] or entry["metadata_matches"]
                                else "reading"
                            ),
                            snippet=hit_snippet,
                            score=float(len(matched_keywords))
                            + (
                                1.0
                                if phrase in content_matches or phrase in metadata_matches
                                else 0.0
                            ),
                            segment_id=segment_id,
                            locator=locator,
                            version=str(entry["source_version"]),
                        )
                    )
        return sorted(hits, key=lambda hit: hit.score, reverse=True)[:limit]
