import { request } from "../request";

/**
 * Ingest kinds the backend accepts (``octop.infra.db.repos.data_sources.KINDS``).
 *
 * ``upload`` replays a document already in the base; ``url`` fetches the page
 * the source points at (SSRF-guarded https, size-capped) and ingests it. Both
 * sync through the same parse → chunk → embed → index pipeline.
 *
 * ``local`` / ``smb`` / ``nfs`` name a folder the platform connects to itself
 * (design §4) and keep their settings in {@link DataSourceFolder}. ``nfs`` has
 * no connector in this build: the backend refuses it when the source is
 * created, rather than storing one that could never run.
 *
 * ``connector`` is stored and listed for compatibility, but nothing in the
 * product pulls documents through a connector instance, so its sync answers
 * ``DATA_SOURCE_SYNC_UNSUPPORTED`` instead of silently doing nothing.
 */
export type DataSourceKind =
  | "upload"
  | "url"
  | "connector"
  | "local"
  | "smb"
  | "nfs";

/** Kinds born from the folder-connection surface rather than the upload form. */
export const FOLDER_KINDS: DataSourceKind[] = ["local", "smb", "nfs"];

/** Outcome recorded by the last sync attempt. */
export type DataSourceSyncStatus = "idle" | "running" | "ok" | "failed";

/** Outcome recorded by the last connection test. */
export type DataSourceConnectionStatus = "unknown" | "ok" | "failed";

/** Kind-specific settings, already normalised by the backend on create. */
export interface DataSourceConfig {
  /** ``upload``: the knowledge document this source ingests. */
  document_id?: string;
  /** ``upload``: that document's path inside the knowledge base. */
  path?: string;
  /** ``url``: the page this source fetches on every sync. */
  url?: string;
  /** ``connector``: the connector instance this source points at. */
  connector_id?: string;
  [key: string]: unknown;
}

/**
 * Where a folder source is and how to reach it.
 *
 * ``password`` is write-only: the backend stores it encrypted and never returns
 * it. Omit it to leave an existing secret unchanged, or send ``""`` to clear
 * it — the two mean different things and the form must not collapse them.
 */
export interface DataSourceFolder {
  /** ``local``: an absolute path on the server. ``smb``: a path inside the share. */
  root_path: string;
  server: string;
  share: string;
  /** Include the domain as ``DOMAIN\\user`` for SMB. */
  username: string;
  password?: string | null;
  read_only: boolean;
  include_globs: string;
  exclude_globs: string;
  scan_interval_seconds: number;
}

export interface DataSource {
  id: string;
  /** ``id`` under the spelling the payload uses; both carry the same value. */
  data_source_id: string;
  knowledge_base_id: string;
  name: string;
  kind: DataSourceKind;
  config: DataSourceConfig;
  /** Folder sources: connection settings. Empty for the content kinds. */
  server: string;
  share: string;
  root_path: string;
  username: string;
  /** Whether a secret is stored. The secret itself is never returned. */
  has_credentials: boolean;
  read_only: boolean;
  include_globs: string;
  exclude_globs: string;
  scan_interval_seconds: number;
  connection_status: DataSourceConnectionStatus;
  /** Why the last connection test failed; ``null`` once one succeeds. */
  connection_error: string | null;
  last_scan_at: number | null;
  last_scan_ok_at: number | null;
  created_by: number | null;
  sync_status: DataSourceSyncStatus;
  /** Why the last attempt failed; ``null`` unless ``sync_status`` is ``failed``. */
  sync_error: string | null;
  /** Epoch ms of the last successful sync; ``null`` until one succeeds. */
  last_synced_at: number | null;
  created_at: number;
  updated_at: number;
}

export interface CreateDataSourceBody {
  name: string;
  kind: DataSourceKind;
  config?: DataSourceConfig;
  /** Required for ``local`` / ``smb`` / ``nfs``; refused for the others. */
  folder?: DataSourceFolder;
}

export interface UpdateDataSourceBody {
  name?: string;
  folder?: DataSourceFolder;
}

/** What a connection test saw, shown to the administrator. Carries no secret. */
export interface DataSourceTestResult {
  ok: boolean;
  detail: string;
}

export const dataSourcesApi = {
  list: (kbId: string) =>
    request<DataSource[]>(`/knowledge-bases/${kbId}/data-sources`),

  create: (kbId: string, body: CreateDataSourceBody) =>
    request<DataSource>(`/knowledge-bases/${kbId}/data-sources`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  get: (id: string) => request<DataSource>(`/data-sources/${id}`),

  update: (id: string, body: UpdateDataSourceBody) =>
    request<DataSource>(`/data-sources/${id}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  /**
   * Reach the source and report what it saw, indexing nothing.
   *
   * Separate from sync on purpose: an administrator wants to know whether a
   * share is reachable before committing a scan of it.
   */
  test: (id: string) =>
    request<DataSourceTestResult>(`/data-sources/${id}/test`, {
      method: "POST",
    }),

  /** Ingest what the source points at. Synchronous and potentially slow. */
  sync: (id: string) =>
    request<DataSource>(`/data-sources/${id}/sync`, { method: "POST" }),

  /**
   * Drop the source record. The knowledge base's documents are untouched —
   * a sync already ingested document stays indexed.
   */
  remove: (id: string) =>
    request<void>(`/data-sources/${id}`, { method: "DELETE" }),
};
