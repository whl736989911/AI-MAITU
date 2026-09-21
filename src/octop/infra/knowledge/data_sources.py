"""Data sources on a knowledge base — objects, ingest, and sync outcomes.

Four decisions shape this module:

- **Visibility is inherited.** A data source has no ``resource_acl`` row: it is
  reachable exactly when its knowledge base is, so reads go through
  :meth:`KnowledgeService.get_readable_base` — the one check that resolves the
  ``knowledge_base`` ACL entry through ``sharing.can_access``.
- **Write access is owner or admin.** Creating, deleting, and syncing all go
  through :meth:`KnowledgeService.get_writable_base`, so "may see it" never
  silently becomes "may change it".
- **Ingest reuses the document pipeline.** ``kind='upload'`` syncs a knowledge
  document through ``knowledge.jobs.process_document`` — the same
  parse → chunk → embed → index chain the upload endpoint runs. ``kind='url'``
  fetches the page through the SSRF guard, stores it as a knowledge document
  (the bytes ``upload_document`` accepts), and runs that same chain.
- **A kind with no ingest refuses.** ``kind='connector'`` points at an MCP
  connector instance, which carries credentials, not content: nothing in the
  product can pull documents through one, so its sync raises
  :class:`DataSourceSyncUnsupported` instead of reporting a success it never
  performed. The dashboard does not offer creating one either.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.data_sources import (
    KIND_CONNECTOR,
    KIND_UPLOAD,
    KIND_URL,
    KINDS,
    SYNC_FAILED,
    SYNC_OK,
    SYNC_RUNNING,
    DataSourceRepo,
    DataSourceRow,
)
from octop.infra.db.repos.knowledge import KnowledgeBaseRow, KnowledgeDocumentRow, KnowledgeRepo
from octop.infra.knowledge.jobs import process_document
from octop.infra.knowledge.relpath import path_parent
from octop.infra.knowledge.service import KnowledgeService
from octop.infra.knowledge.url_fetch import FetchedDocument, UrlFetchError, fetch_document
from octop.infra.utils.ssrf_guard import OutboundFetchError, validate_https_url

_URL_KEY = "url"
_DOCUMENT_ID_KEY = "document_id"
_CONNECTOR_ID_KEY = "connector_id"
# What each kind may carry in ``config``. A key a kind cannot honour is an
# error: silently storing it is how a source ends up configured but inert.
_KIND_CONFIG_KEYS = {
    KIND_UPLOAD: frozenset({_DOCUMENT_ID_KEY, "path"}),
    KIND_URL: frozenset({_URL_KEY, _DOCUMENT_ID_KEY}),
    KIND_CONNECTOR: frozenset({_CONNECTOR_ID_KEY}),
}


class DataSourceSyncUnsupported(RuntimeError):
    """Raised for a kind whose ingest is not implemented yet.

    Carries the kind so the HTTP layer can name it; the row is left with
    ``sync_status='failed'`` so the refusal is visible after the fact too.
    """

    def __init__(self, kind: str) -> None:
        self.kind = kind
        super().__init__(f"sync is not supported for data source kind {kind!r} yet")


class DataSourceSyncFailed(RuntimeError):
    """A sync attempt ran and failed; :attr:`cause` is the underlying error."""

    def __init__(self, cause: Exception) -> None:
        self.cause = cause
        super().__init__(str(cause))


class DataSourceService:
    """Create, list, delete, and sync the sources attached to knowledge bases."""

    def __init__(self, services: Any) -> None:
        self._services = services
        self._repo: DataSourceRepo = services.data_sources_repo
        self._knowledge_repo: KnowledgeRepo = services.knowledge_repo
        self._knowledge = KnowledgeService(services)

    # ------------------------------------------------------------------
    # Reads
    # ------------------------------------------------------------------

    def list_for_base(
        self, kb_id: str, *, actor_user_id: int, is_admin: bool = False
    ) -> list[DataSourceRow]:
        base = self._knowledge.get_readable_base(
            kb_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        return self._repo.list_for_base(base.id)

    def get(self, ds_id: str, *, actor_user_id: int, is_admin: bool = False) -> DataSourceRow:
        data_source = self._require(ds_id)
        self._knowledge.get_readable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        return data_source

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    def create(
        self,
        kb_id: str,
        *,
        actor_user_id: int,
        name: str,
        kind: str,
        config: dict[str, Any] | None = None,
        is_admin: bool = False,
    ) -> DataSourceRow:
        base = self._knowledge.get_writable_base(
            kb_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        cleaned = (name or "").strip()
        if not cleaned:
            raise ValueError("a data source needs a name")
        if kind not in KINDS:
            raise ValueError(f"unknown data source kind {kind!r}; expected one of {KINDS}")
        return self._repo.create(
            knowledge_base_id=base.id,
            name=cleaned,
            kind=kind,
            config=self._validated_config(base, kind=kind, config=config or {}),
            created_by=actor_user_id,
        )

    def delete(self, ds_id: str, *, actor_user_id: int, is_admin: bool = False) -> None:
        data_source = self._require(ds_id)
        self._knowledge.get_writable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        self._repo.delete(ds_id)

    def sync(self, ds_id: str, *, actor_user_id: int, is_admin: bool = False) -> DataSourceRow:
        """Ingest what the source points at, and record the outcome on the row."""
        data_source = self._require(ds_id)
        base = self._knowledge.get_writable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        if data_source.kind == KIND_UPLOAD:
            return self._sync_upload(data_source, base)
        if data_source.kind == KIND_URL:
            return self._sync_url(data_source, base, actor_user_id=actor_user_id, is_admin=is_admin)
        # connector: nothing in the product pulls documents through a connector
        # instance (it carries credentials, not content), so say so.
        error = DataSourceSyncUnsupported(data_source.kind)
        self._repo.mark_sync(ds_id, status=SYNC_FAILED, error=str(error))
        raise error

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _sync_upload(self, data_source: DataSourceRow, base: KnowledgeBaseRow) -> DataSourceRow:
        """Ingest the knowledge document an ``upload`` source points at."""
        try:
            document = self._upload_target(base.id, data_source.config)
        except ValueError as exc:
            # The document it was created against is gone: report it as the
            # failed sync it is instead of leaving a stale 'ok'.
            self._repo.mark_sync(data_source.id, status=SYNC_FAILED, error=str(exc))
            raise
        return self._index(data_source, base, document)

    def _sync_url(
        self,
        data_source: DataSourceRow,
        base: KnowledgeBaseRow,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> DataSourceRow:
        """Fetch the source's URL and ingest it as one knowledge document.

        The document is written only after a guarded fetch succeeded, so a
        failed sync never leaves an empty document behind; and it is reused on
        the next sync, so refreshing a page does not add a copy per attempt.
        """
        try:
            url = str(data_source.config.get(_URL_KEY) or "").strip()
            if not url:
                raise ValueError("a url data source requires config.url")
            fetched = fetch_document(
                url,
                name=data_source.name,
                max_bytes=self._knowledge.max_document_bytes(),
            )
        except (OutboundFetchError, UrlFetchError, ValueError) as exc:
            self._repo.mark_sync(data_source.id, status=SYNC_FAILED, error=str(exc))
            raise DataSourceSyncFailed(exc) from exc
        try:
            document = self._store_fetched(
                base,
                data_source,
                fetched,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
            )
            if document.id != str(data_source.config.get(_DOCUMENT_ID_KEY) or "").strip():
                # Remember which document this source writes into, so the next
                # sync refreshes it instead of creating a second copy.
                self._repo.update_config(
                    data_source.id, {**data_source.config, _DOCUMENT_ID_KEY: document.id}
                )
        except Exception as exc:
            self._repo.mark_sync(data_source.id, status=SYNC_FAILED, error=str(exc))
            raise DataSourceSyncFailed(exc) from exc
        return self._index(data_source, base, document)

    def _index(
        self, data_source: DataSourceRow, base: KnowledgeBaseRow, document: KnowledgeDocumentRow
    ) -> DataSourceRow:
        """Run the shared parse → chunk → embed → index chain and record it."""
        self._repo.mark_sync(data_source.id, status=SYNC_RUNNING)
        try:
            process_document(self._services, base.id, document.id)
        except Exception as exc:
            self._repo.mark_sync(data_source.id, status=SYNC_FAILED, error=str(exc))
            raise DataSourceSyncFailed(exc) from exc
        self._repo.mark_sync(data_source.id, status=SYNC_OK, error=None, synced_at=now_ts())
        return self._require(data_source.id)

    def _store_fetched(
        self,
        base: KnowledgeBaseRow,
        data_source: DataSourceRow,
        fetched: FetchedDocument,
        *,
        actor_user_id: int,
        is_admin: bool,
    ) -> KnowledgeDocumentRow:
        """Put fetched bytes into the document this source owns, creating it once."""
        existing = self._ingested_document(base.id, data_source.config)
        if existing is not None:
            return self._knowledge.replace_document_content(
                base.id,
                existing.id,
                actor_user_id=actor_user_id,
                is_admin=is_admin,
                path=self._free_path(
                    base.id,
                    fetched.filename,
                    folder=path_parent(existing.path),
                    ignore_doc_id=existing.id,
                ),
                content_type=fetched.content_type,
                content=fetched.content,
            )
        return self._knowledge.upload_document(
            base.id,
            actor_user_id=actor_user_id,
            is_admin=is_admin,
            filename=fetched.filename,
            content_type=fetched.content_type,
            content=fetched.content,
            path=self._free_path(base.id, fetched.filename, folder="", ignore_doc_id=None),
        )

    def _ingested_document(self, kb_id: str, config: dict[str, Any]) -> KnowledgeDocumentRow | None:
        """The document a previous sync of this source wrote, if it still exists."""
        doc_id = str(config.get(_DOCUMENT_ID_KEY) or "").strip()
        if not doc_id:
            return None
        document = self._knowledge_repo.get_document(doc_id)
        if document is None or document.kb_id != kb_id or document.is_dir:
            return None
        return document

    def _free_path(
        self, kb_id: str, filename: str, *, folder: str, ignore_doc_id: str | None
    ) -> str:
        """A path for a fetched document that no other document already holds."""
        stem = Path(filename).stem
        suffix = Path(filename).suffix
        name = filename
        index = 2
        while True:
            candidate = f"{folder}/{name}" if folder else name
            existing = self._knowledge_repo.get_document_by_path(kb_id, candidate)
            if existing is None or existing.id == ignore_doc_id:
                return candidate
            name = f"{stem} ({index}){suffix}"
            index += 1

    def _validated_config(
        self, base: KnowledgeBaseRow, *, kind: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalise the stored config, rejecting a source that could never sync."""
        unsupported = sorted(set(config) - _KIND_CONFIG_KEYS[kind])
        if unsupported:
            raise ValueError(f"data source kind {kind!r} does not accept config.{unsupported[0]}")
        if kind == KIND_UPLOAD:
            document = self._upload_target(base.id, config)
            return {**config, _DOCUMENT_ID_KEY: document.id, "path": document.path}
        if kind == KIND_URL:
            url = str(config.get(_URL_KEY) or "").strip()
            if not url:
                raise ValueError("a url data source requires config.url")
            # SSRF: a target the fetch would have to refuse is not stored as if
            # it could work. DNS-level checks happen again on every sync.
            validate_https_url(url, field=_URL_KEY)
            if _DOCUMENT_ID_KEY in config and self._ingested_document(base.id, config) is None:
                raise ValueError(
                    f"knowledge document {config.get(_DOCUMENT_ID_KEY)!r} is not in "
                    "this knowledge base"
                )
            return {**config, _URL_KEY: url}
        # connector: the credentials and settings live on the connector instance,
        # so only its id is persisted here.
        connector_id = str(config.get(_CONNECTOR_ID_KEY) or "").strip()
        if not connector_id:
            raise ValueError("a connector data source requires config.connector_id")
        return {**config, _CONNECTOR_ID_KEY: connector_id}

    def _upload_target(self, kb_id: str, config: dict[str, Any]) -> KnowledgeDocumentRow:
        """The knowledge document an ``upload`` source ingests, or ``ValueError``."""
        doc_id = str(config.get("document_id") or "").strip()
        if doc_id:
            document = self._knowledge_repo.get_document(doc_id)
            if document is None or document.kb_id != kb_id or document.is_dir:
                raise ValueError(f"knowledge document {doc_id!r} is not in this knowledge base")
            return document
        path = str(config.get("path") or "").strip()
        if not path:
            raise ValueError("an upload data source requires config.document_id or config.path")
        document = self._knowledge_repo.get_document_by_path(kb_id, path)
        if document is None or document.is_dir:
            raise ValueError(f"knowledge document {path!r} is not in this knowledge base")
        return document

    def _require(self, ds_id: str) -> DataSourceRow:
        data_source = self._repo.get(ds_id)
        if data_source is None:
            raise LookupError(f"data source {ds_id!r} not found")
        return data_source
