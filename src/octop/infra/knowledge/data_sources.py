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

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, NoReturn

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
from octop.infra.db.repos.knowledge_sync_runs import (
    RUN_FAILED,
    RUN_OK,
    TRIGGER_MANUAL,
    KnowledgeSyncRunRepo,
    SyncRunRow,
)
from octop.infra.knowledge.index import KnowledgeIndex
from octop.infra.knowledge.jobs import process_document, process_source_file
from octop.infra.knowledge.parse import failure_status
from octop.infra.knowledge.relpath import path_basename, path_parent
from octop.infra.knowledge.service import KnowledgeService, knowledge_content_type
from octop.infra.knowledge.source_crypto import decrypt_source_secret, encrypt_source_secret
from octop.infra.knowledge.sources import (
    ScanPlan,
    SourceConnector,
    SourceEntry,
    SourceError,
    build_connector,
    filter_entries,
    is_folder_kind,
    plan_scan,
)
from octop.infra.knowledge.url_fetch import FetchedDocument, UrlFetchError, fetch_document
from octop.infra.utils.ssrf_guard import OutboundFetchError, validate_https_url

logger = logging.getLogger(__name__)

_PLATFORM_CONTENT_TYPE = "application/octet-stream"
"""What a source file whose extension this build cannot parse is stored as.

The row keeps the file's real size and modification time, so the index knows the
source holds it even though nothing was extracted from it (design §6.2's
"保存元数据并标记不支持").
"""

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


def _summary(counts: dict[str, int]) -> str:
    """The audit line a finished scan leaves: what it did, in one string.

    Ordered as a reader would ask, and stable so the lines can be compared
    between runs without parsing them.
    """
    return " ".join(f"{field}={counts.get(field, 0)}" for field in _AUDIT_FIELDS)


_AUDIT_FIELDS = ("scanned", "added", "updated", "removed", "deferred", "failed")


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
        self._runs: KnowledgeSyncRunRepo = services.knowledge_sync_runs_repo
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
        base = self._knowledge.get_writable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        if is_folder_kind(data_source.kind):
            self._delete_source_files(base.id, ds_id)
        self._repo.delete(ds_id)

    def _delete_source_files(self, kb_id: str, data_source_id: str) -> None:
        """Drop a folder source's index along with the source.

        The rows are removed one by one rather than left to the schema's cascade,
        because ``delete_document`` is what keeps ``doc_count`` and the chunk
        index in step. The cascade stays as the guarantee that nothing is
        orphaned if a source is ever deleted some other way.
        """
        rows = self._knowledge_repo.list_source_files(data_source_id)
        index = KnowledgeIndex(kb_id)
        for row in [entry for entry in rows if not entry.is_dir]:
            index.delete_doc(row.id)
            self._knowledge_repo.delete_document(row.id)
        # Folders after their files: a folder row's own delete walks descendants,
        # and doing it first would leave ``doc_count`` counting files twice.
        for row in [entry for entry in rows if entry.is_dir]:
            self._knowledge_repo.delete_document(row.id)

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
        if is_folder_kind(data_source.kind):
            return self._sync_folder(data_source, base, actor_user_id=actor_user_id)
        # connector: nothing in the product pulls documents through a connector
        # instance (it carries credentials, not content), so say so.
        error = DataSourceSyncUnsupported(data_source.kind)
        self._repo.mark_sync(ds_id, status=SYNC_FAILED, error=str(error))
        raise error

    def list_runs(
        self, ds_id: str, *, actor_user_id: int, is_admin: bool = False, limit: int = 20
    ) -> list[SyncRunRow]:
        """A source's scan history, newest first (design §8.4)."""
        data_source = self._require(ds_id)
        self._knowledge.get_readable_base(
            data_source.knowledge_base_id, actor_user_id=actor_user_id, is_admin=is_admin
        )
        return self._runs.list_for_source(ds_id, limit=limit)

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

    def _sync_folder(
        self, data_source: DataSourceRow, base: KnowledgeBaseRow, *, actor_user_id: int
    ) -> DataSourceRow:
        """Walk a folder source, apply the change set, and record the run.

        A walk that fails aborts the scan before anything is decided: without a
        listing there is no evidence any file is gone, and reading a dropped
        connection as "the folder was emptied" is the failure design §8.3 names.
        The previous results — and every document already indexed — stay as they
        were, and the source is marked so an administrator can see why.
        """
        run = self._runs.start(data_source_id=data_source.id, trigger=TRIGGER_MANUAL)
        self._repo.mark_sync(data_source.id, status=SYNC_RUNNING)
        try:
            connector = self._connector(data_source)
            # The source's own include/exclude rules, applied here so a setting
            # an administrator typed always does something (design §3.2).
            entries = filter_entries(
                connector.walk(),
                include_globs=data_source.include_globs,
                exclude_globs=data_source.exclude_globs,
            )
        except SourceError as exc:
            self._fail_scan(data_source, run.id, exc, actor_user_id=actor_user_id)
        now = now_ts()
        # Folders are kept out of the plan: a directory carries no content, and
        # the source's own root folder has no listing entry to match, so leaving
        # it in would make every scan try to remove it and the next one re-create
        # it. The tree still gets its folder rows from ``create_document``.
        indexed = [
            row for row in self._knowledge_repo.list_source_files(data_source.id) if not row.is_dir
        ]
        plan = plan_scan(entries, [row.scan_row() for row in indexed], now=now)
        counts = self._apply_plan(base, data_source, connector, plan, indexed=indexed, now=now)
        self._repo.mark_sync(data_source.id, status=SYNC_OK, error=None, synced_at=now)
        self._repo.mark_scan(data_source.id, at=now, ok=True)
        self._runs.finish(run.id, status=RUN_OK, counts=counts)
        self._audit(actor_user_id, "knowledge.source.scan", data_source.id, _summary(counts))
        return self._require(data_source.id)

    def _fail_scan(
        self, data_source: DataSourceRow, run_id: str, exc: Exception, *, actor_user_id: int
    ) -> NoReturn:
        """Record an aborted scan and raise it. Never returns."""
        self._repo.mark_sync(data_source.id, status=SYNC_FAILED, error=str(exc))
        # ``ok=False`` on purpose: an aborted scan must not read as the newest
        # consistent view of the source.
        self._repo.mark_scan(data_source.id, at=now_ts(), ok=False)
        self._runs.finish(run_id, status=RUN_FAILED, error=str(exc))
        self._audit(actor_user_id, "knowledge.source.scan.failed", data_source.id, str(exc))
        raise DataSourceSyncFailed(exc) from exc

    def _apply_plan(
        self,
        base: KnowledgeBaseRow,
        data_source: DataSourceRow,
        connector: SourceConnector,
        plan: ScanPlan,
        *,
        indexed: list[KnowledgeDocumentRow],
        now: int,
    ) -> dict[str, int]:
        """Carry out a plan and return the counters the run records."""
        counts = dict(plan.counts)
        counts["failed"] = 0
        by_path = {row.source_path: row for row in indexed}

        # 1. Observations first. A file still moving is remembered here so the
        #    next scan can tell that it stopped — this is the debounce's state.
        for path, (size, modified_at) in plan.observations.items():
            row = by_path.get(path)
            if row is not None:
                self._knowledge_repo.record_observation(
                    row.id, size=size, modified_at=modified_at, at=now
                )
                continue
            created = self._create_source_row(
                base,
                data_source,
                path,
                byte_size=size,
                status="discovered",
            )
            if created is None:
                counts["failed"] += 1
                continue
            # The observation has to land on the row this scan just made, or the
            # next scan finds nothing to compare against and defers it forever.
            self._knowledge_repo.record_observation(
                created.id, size=size, modified_at=modified_at, at=now
            )

        # 2. A file that came back clears its pending deletion before anything
        #    else, so a share that reconnected leaves no trace of the scare.
        for entry in (*plan.added, *plan.updated, *plan.unchanged):
            row = by_path.get(entry.path)
            if row is not None and row.delete_pending_since is not None:
                self._knowledge_repo.clear_delete_pending(row.id)

        # 3. Index what settled.
        for entry in (*plan.added, *plan.updated):
            row = by_path.get(entry.path)
            if row is None:
                row = self._create_source_row(
                    base,
                    data_source,
                    entry.path,
                    byte_size=entry.size,
                    status="pending",
                )
                if row is None:
                    counts["failed"] += 1
                    continue
            if not self._index_source_file(base, data_source, connector, row, entry):
                counts["failed"] += 1

        # 4. Missing files: start the window, then act on the ones that ran out.
        for missing in plan.absent:
            self._knowledge_repo.mark_delete_pending(missing.document_id, since=now)
        for doc_id in plan.removed:
            self._remove_source_file(base.id, doc_id)

        return counts

    def _create_source_row(
        self,
        base: KnowledgeBaseRow,
        data_source: DataSourceRow,
        path: str,
        *,
        byte_size: int,
        status: str,
    ) -> KnowledgeDocumentRow | None:
        """Add one source file to the index, or ``None`` when the base is full.

        ``source_size`` / ``source_modified_at`` stay at their empty values: they
        mean "the identity this file had when it was last *processed*", and only
        :meth:`mark_source_processed` fills them. The size this scan saw goes
        through ``record_observation`` instead, which is what keeps the two
        meanings apart — otherwise a merely discovered row would look processed.

        The stored path is prefixed with the source's id, which is what keeps
        two sources that both contain ``readme.md`` apart and what keeps the
        path stable if the source is renamed. ``source_path`` carries the path
        inside the source, which is what the scan and the UI show.
        """
        try:
            return self._knowledge_repo.create_document(
                kb_id=base.id,
                filename=path_basename(path),
                content_type=knowledge_content_type(Path(path).suffix) or _PLATFORM_CONTENT_TYPE,
                byte_size=byte_size,
                status=status,
                path=f"{data_source.id}/{path}",
                data_source_id=data_source.id,
                source_path=path,
            )
        except ValueError as exc:
            # The base refuses new documents (its limit): a scan must report it
            # rather than abort the whole folder over one file.
            self._services.audit_repo.system_event(
                "knowledge.source.scan.skipped",
                target=data_source.id,
                payload=f"{path}: {exc}",
            )
            return None

    def _index_source_file(
        self,
        base: KnowledgeBaseRow,
        data_source: DataSourceRow,
        connector: SourceConnector,
        document: KnowledgeDocumentRow,
        entry: SourceEntry,
    ) -> bool:
        """Index one settled file. ``False`` when it could not be indexed.

        A file this build cannot parse is marked ``unsupported`` and keeps its
        metadata: design §6's matrix ends at "保存元数据并标记不支持", and
        refusing it outright would leave no trace that the folder holds it.
        """
        if knowledge_content_type(Path(entry.path).suffix) is None:
            self._knowledge_repo.update_document(
                document.id, status="unsupported", error_message="", byte_size=entry.size
            )
            self._knowledge_repo.mark_source_processed(
                document.id, size=entry.size, modified_at=entry.modified_at
            )
            return True
        try:
            process_source_file(
                self._services,
                base.id,
                document.id,
                connector=connector,
                source_path=entry.path,
            )
        except Exception as exc:
            # One file failing must not stop the source (design §8.3). The
            # reason goes on the row so §8.4's "查看失败原因" has something to
            # show: ``process_source_file`` records what failed *inside* the
            # pipeline, but a failure before it starts — the knowledge feature
            # being off, embedding prerequisites unmet — would otherwise leave
            # the row looking merely discovered. Writing it again is idempotent,
            # and ``failure_status`` keeps a locked file's ``password_required``
            # state instead of flattening it into a plain failure (§6.1).
            self._knowledge_repo.update_document(
                document.id,
                status=failure_status(exc),
                error_message=str(exc),
                chunk_count=0,
            )
            logger.warning(
                "knowledge source %s: indexing %s failed: %s",
                data_source.id,
                entry.path,
                exc,
            )
            # ``mark_source_processed`` is deliberately skipped so the next scan
            # retries the file once whatever stopped it is fixed.
            return False
        self._knowledge_repo.mark_source_processed(
            document.id, size=entry.size, modified_at=entry.modified_at
        )
        return True

    def _remove_source_file(self, kb_id: str, doc_id: str) -> None:
        """Drop a confirmed-missing file and the chunks it was indexed into."""
        KnowledgeIndex(kb_id).delete_doc(doc_id)
        self._knowledge_repo.delete_document(doc_id)

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
