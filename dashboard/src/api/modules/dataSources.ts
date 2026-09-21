import { request } from "../request";

/**
 * Ingest kinds the backend accepts (``octop.infra.db.repos.data_sources.KINDS``).
 *
 * ``upload`` replays a document already in the base; ``url`` fetches the page
 * the source points at (SSRF-guarded https, size-capped) and ingests it. Both
 * sync through the same parse → chunk → embed → index pipeline.
 *
 * ``connector`` is stored and listed for compatibility, but nothing in the
 * product pulls documents through a connector instance, so its sync answers
 * ``DATA_SOURCE_SYNC_UNSUPPORTED`` instead of silently doing nothing.
 */
export type DataSourceKind = "upload" | "url" | "connector";

/** Outcome recorded by the last sync attempt. */
export type DataSourceSyncStatus = "idle" | "running" | "ok" | "failed";

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

export interface DataSource {
  id: string;
  /** ``id`` under the spelling the payload uses; both carry the same value. */
  data_source_id: string;
  knowledge_base_id: string;
  name: string;
  kind: DataSourceKind;
  config: DataSourceConfig;
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

  /** Ingest what the source points at. Synchronous and potentially slow. */
  sync: (id: string) =>
    request<DataSource>(`/data-sources/${id}/sync`, { method: "POST" }),

  /**
   * Drop the source record. The knowledge base's documents are untouched —
   * a sync already ingested document stays indexed.
   */
  remove: (id: string) => request<void>(`/data-sources/${id}`, { method: "DELETE" }),
};
