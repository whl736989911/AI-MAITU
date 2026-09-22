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
- **A folder source is validated by building it.** ``local`` / ``smb`` / ``nfs``
  name a folder the platform connects to itself (design §4). Create and update
  run the same connector construction a scan runs, so a path the denylist
  refuses, a missing share name, or a kind with no connector in this build is
  refused while the administrator is still looking at the form — not on the
  first scan, when it reads as a sync bug.
- **Secrets are write-only.** A source's connection secret goes straight into
  an encrypted blob; the row carries only whether one exists, so no payload
  builder can leak it (design §4).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from octop.infra.db.repos._base import now_ts
from octop.infra.db.repos.data_sources import (
    CONNECTION_FAILED,
    CONNECTION_OK,
    CONNECTION_UNKNOWN,
    KIND_CONNECTOR,
    KIND_LOCAL,
    KIND_NFS,
    KIND_SMB,
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
from octop.infra.knowledge.source_crypto import decrypt_source_secret, encrypt_source_secret
from octop.infra.knowledge.sources import SourceConnector, SourceError, build_connector
from octop.infra.knowledge.url_fetch import FetchedDocument, UrlFetchError, fetch_document
from octop.infra.utils.ssrf_guard import OutboundFetchError, validate_https_url

_URL_KEY = "url"
_DOCUMENT_ID_KEY = "document_id"
_CONNECTOR_ID_KEY = "connector_id"
# What each kind may carry in ``config``. A key a kind cannot honour is an
# error: silently storing it is how a source ends up configured but inert.
# A folder source allows nothing here: its connection lives in dedicated
# columns, so a stray config key would be a setting nothing reads.
_KIND_CONFIG_KEYS = {
    KIND_UPLOAD: frozenset({_DOCUMENT_ID_KEY, "path"}),
    KIND_URL: frozenset({_URL_KEY, _DOCUMENT_ID_KEY}),
    KIND_CONNECTOR: frozenset({_CONNECTOR_ID_KEY}),
    KIND_LOCAL: frozenset(),
    KIND_SMB: frozenset(),
    KIND_NFS: frozenset(),
}


@dataclass(frozen=True)
class FolderSettings:
    """Where a folder source is and how to reach it.

    One type rather than nine more keyword arguments, because the same shape is
    validated on create, read back for a connection test, and patched on update.

    ``password`` is tri-state on purpose: ``None`` means "leave the stored
    secret alone", ``""`` means "clear it", and any other value replaces it. A
    plain ``str`` could not tell an untouched secret from an emptied one, and
    collapsing those two is how an update silently drops a credential.
    """

    server: str = ""
    share: str = ""
    root_path: str = ""
    username: str = ""
    password: str | None = None
    read_only: bool = True
    include_globs: str = ""
    exclude_globs: str = ""
    scan_interval_seconds: int = 0


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
        folder: FolderSettings | None = None,
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
        settings = folder or FolderSettings()
        if kind in (KIND_LOCAL, KIND_SMB, KIND_NFS):
            self._validated_folder(kind=kind, folder=settings)
        return self._repo.create(
            knowledge_base_id=base.id,
            name=cleaned,
            kind=kind,
            config=self._validated_config(base, kind=kind, config=config or {}),
            created_by=actor_user_id,
            server=settings.server.strip(),
            share=settings.share.strip(),
            root_path=settings.root_path.strip(),
            username=settings.username.strip(),
            credentials_enc=self._encrypted_secret(settings),
            read_only=settings.read_only,
            include_globs=settings.include_globs.strip(),
            exclude_globs=settings.exclude_globs.strip(),
            scan_interval_seconds=max(0, settings.scan_interval_seconds),
        )

    def update_source(
        self,
        ds_id: str,
        *,
        actor_user_id: int,
        is_admin: bool = False,
        name: str | None = None,
        folder: FolderSettings | None = None,
    ) -> DataSourceRow:
        """Patch a source's name and connection settings.

        The candidate settings are validated by building the connector first, so
        an edit that would break the source is refused instead of stored; then
        the row is written and the secret replaced only when the caller supplied
        one (:attr:`FolderSettings.password` carries that distinction).
        """
        data_source = self._require(ds_id)
        self._knowledge.get_writable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        cleaned = name.strip() if name is not None else None
        if cleaned is not None and not cleaned:
            raise ValueError("a data source needs a name")
        settings = folder
        if settings is not None and data_source.kind in (KIND_LOCAL, KIND_SMB, KIND_NFS):
            self._validated_folder(kind=data_source.kind, folder=settings)
        self._repo.update_source_fields(
            ds_id,
            name=cleaned,
            server=settings.server.strip() if settings else None,
            share=settings.share.strip() if settings else None,
            root_path=settings.root_path.strip() if settings else None,
            username=settings.username.strip() if settings else None,
            read_only=settings.read_only if settings else None,
            include_globs=settings.include_globs.strip() if settings else None,
            exclude_globs=settings.exclude_globs.strip() if settings else None,
            scan_interval_seconds=(
                max(0, settings.scan_interval_seconds) if settings is not None else None
            ),
        )
        if settings is not None and settings.password is not None:
            self._repo.set_credentials(ds_id, self._encrypted_secret(settings))
            # A new credential makes the last test's verdict stale: the source
            # may now be reachable, or no longer be.
            self._repo.set_connection(ds_id, status=CONNECTION_UNKNOWN, error=None)
        return self._require(ds_id)

    def test_connection(self, ds_id: str, *, actor_user_id: int, is_admin: bool = False) -> str:
        """Prove a source can be reached, and record the verdict on the row.

        Audited: this is the moment the platform spends the stored credential
        against another system (design §4). The returned text is shown to the
        administrator and carries no secret.
        """
        data_source = self._require(ds_id)
        self._knowledge.get_writable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        try:
            detail = self._connector(data_source).test()
        except SourceError as exc:
            self._repo.set_connection(ds_id, status=CONNECTION_FAILED, error=str(exc))
            self._audit(actor_user_id, "knowledge.source.test.failed", ds_id, str(exc))
            raise
        self._repo.set_connection(ds_id, status=CONNECTION_OK, error=None)
        self._audit(actor_user_id, "knowledge.source.test", ds_id, detail)
        return detail

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
        if kind == KIND_CONNECTOR:
            # The credentials and settings live on the connector instance, so
            # only its id is persisted here.
            connector_id = str(config.get(_CONNECTOR_ID_KEY) or "").strip()
            if not connector_id:
                raise ValueError("a connector data source requires config.connector_id")
            return {**config, _CONNECTOR_ID_KEY: connector_id}
        # Folder kinds: every setting lives in its own column, so nothing is
        # stored here — and the key check above already refused a stray one.
        return {}

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

    # ------------------------------------------------------------------
    # Folder sources
    # ------------------------------------------------------------------

    def _connector(self, data_source: DataSourceRow) -> SourceConnector:
        """The live connector for a stored source, secret decrypted for it only."""
        secret = decrypt_source_secret(
            self._services.secret_repo, self._repo.get_credentials(data_source.id)
        )
        return build_connector(
            kind=data_source.kind,
            root_path=data_source.root_path,
            server=data_source.server,
            share=data_source.share,
            username=data_source.username,
            password=str(secret.get("password") or ""),
        )

    def _validated_folder(self, *, kind: str, folder: FolderSettings) -> None:
        """Refuse a folder source that could not work, by building it.

        The connector is the check. It applies the same path guard and field
        rules a scan would, and it is where a kind this build has no connector
        for (NFS) says so — an administrator learns that from the form rather
        than from a sync that silently indexes nothing.
        """
        build_connector(
            kind=kind,
            root_path=folder.root_path.strip(),
            server=folder.server.strip(),
            share=folder.share.strip(),
            username=folder.username.strip(),
            password=folder.password or "",
        )

    def _encrypted_secret(self, folder: FolderSettings) -> bytes | None:
        """The blob to store for a source, or ``None`` when it has no secret."""
        if not folder.password:
            return None
        return encrypt_source_secret(self._services.secret_repo, {"password": folder.password})

    def _audit(self, actor_user_id: int, action: str, target: str, payload: str) -> None:
        """Record a source operation against the acting user (design §4)."""
        user = self._services.user_repo.get(actor_user_id)
        actor = user.username if user is not None else str(actor_user_id)
        self._services.audit_repo.user_event(actor, action, target=target, payload=payload)
