"""Data sources on a knowledge base — objects, ingest, and sync outcomes.

Three decisions shape this module:

- **Visibility is inherited.** A data source has no ``resource_acl`` row: it is
  reachable exactly when its knowledge base is, so reads go through
  :meth:`KnowledgeService.get_readable_base` — the one check that resolves the
  ``knowledge_base`` ACL entry through ``sharing.can_access``.
- **Write access is owner or admin.** Creating, deleting, and syncing all go
  through :meth:`KnowledgeService.get_writable_base`, so "may see it" never
  silently becomes "may change it".
- **Ingest reuses the document pipeline.** ``kind='upload'`` syncs a knowledge
  document through ``knowledge.jobs.process_document`` — the same
  parse → chunk → embed → index chain the upload endpoint runs. ``url`` and
  ``connector`` sources are stored objects with an explicit entry point that
  refuses: a sync that silently does nothing is worse than an error.
"""

from __future__ import annotations

from typing import Any

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.data_sources import (
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
from octop.infra.knowledge.service import KnowledgeService

_URL_KEY = "url"
_CONNECTOR_ID_KEY = "connector_id"


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

    def __init__(self, cause: BaseException) -> None:
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
        if data_source.kind != KIND_UPLOAD:
            error = DataSourceSyncUnsupported(data_source.kind)
            self._repo.mark_sync(ds_id, status=SYNC_FAILED, error=str(error))
            raise error
        try:
            document = self._upload_target(base.id, data_source.config)
        except ValueError as exc:
            # The document it was created against is gone: report it as the
            # failed sync it is instead of leaving a stale 'ok'.
            self._repo.mark_sync(ds_id, status=SYNC_FAILED, error=str(exc))
            raise
        self._repo.mark_sync(ds_id, status=SYNC_RUNNING)
        try:
            process_document(self._services, base.id, document.id)
        except Exception as exc:
            self._repo.mark_sync(ds_id, status=SYNC_FAILED, error=str(exc))
            raise DataSourceSyncFailed(exc) from exc
        self._repo.mark_sync(ds_id, status=SYNC_OK, error=None, synced_at=now_ts())
        return self._require(ds_id)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _validated_config(
        self, base: KnowledgeBaseRow, *, kind: str, config: dict[str, Any]
    ) -> dict[str, Any]:
        """Normalise the stored config, rejecting a source that could never sync."""
        if kind == KIND_UPLOAD:
            document = self._upload_target(base.id, config)
            return {**config, "document_id": document.id, "path": document.path}
        if kind == KIND_URL:
            url = str(config.get(_URL_KEY) or "").strip()
            if not url:
                raise ValueError("a url data source requires config.url")
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
