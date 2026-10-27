/**
 * A feature's business data — objects, records, attachments and notifications.
 *
 * The schema is a document (``.maitu/feature-data/schema.json``) that a feature
 * manager writes; records live in the control plane and are read through the
 * feature's agent path like its workflow. The types below mirror the server's
 * contract (``docs/feature-data-design.md``) rather than inventing a friendlier
 * one: what a manager writes in the JSON editor is what the server validates, and
 * what a caller edits here is what the server stores.
 *
 * Two answers are not errors and are on the response instead:
 *   - ``schema: null`` — the feature declares no objects yet, which is a feature
 *     without business data rather than a broken one;
 *   - ``error`` — a schema file exists but is not a valid document; the manager's
 *     editor shows the reason so its author can fix it.
 *
 * ``values`` never carries file bytes or file names: a ``file`` field is managed
 * by the attachment endpoints, so it is excluded when a record is written and is
 * rendered from ``files`` instead. Keeping that rule here — in the module every
 * caller shares — is what stops a text input from being built for a file field.
 */

import { request, requestBlob, requestUpload } from "../request";

/** The field kinds the server accepts. */
export const FEATURE_DATA_FIELD_TYPES = [
  "text",
  "textarea",
  "number",
  "integer",
  "boolean",
  "date",
  "datetime",
  "enum",
  "file",
  "user",
  "org_unit",
  "record_ref",
  "knowledge_ref",
] as const;
export type FeatureDataFieldType = (typeof FEATURE_DATA_FIELD_TYPES)[number];

/** ``ledger`` is a list of entries; ``document`` is controlled paperwork. */
export const FEATURE_DATA_OBJECT_KINDS = ["ledger", "document"] as const;
export type FeatureDataObjectKind = (typeof FEATURE_DATA_OBJECT_KINDS)[number];

/** Who may read a record: only its creator, or everybody who can use the feature. */
export type FeatureDataVisibility = "private" | "feature";

/** A ``{"zh": …, "en": …}`` pair; the server renders whichever locale is active. */
export interface FeatureDataLocalizedText {
  zh: string;
  en: string;
}

/** One field of an object. ``key`` is what ``values`` is keyed by. */
export interface FeatureDataField {
  key: string;
  label?: FeatureDataLocalizedText | string;
  type: FeatureDataFieldType;
  required?: boolean;
  options?: string[];
  reminder_days?: number[];
  target_object?: string;
  target_agent_id?: string;
  computed?: {
    op: "add_months" | "add_years" | "add_days";
    source_field: string;
    amount_field?: string;
    amount?: number;
  };
  [key: string]: unknown;
}

/** One business object: a ledger or a controlled document. */
export interface FeatureDataObject {
  key: string;
  name?: FeatureDataLocalizedText | string;
  kind?: FeatureDataObjectKind;
  title_field?: string;
  controlled?: boolean;
  fields?: FeatureDataField[];
  reminder_recipients?: {
    creator?: boolean;
    owner?: boolean;
    responsible?: boolean;
    user_fields?: string[];
    unit_fields?: string[];
    role_keys?: string[];
  };
  [key: string]: unknown;
}

/** The whole schema document. */
export interface FeatureDataSchema {
  version?: number;
  objects?: FeatureDataObject[];
}

/** What both schema reads and writes answer. */
export interface FeatureDataSchemaResponse {
  schema: FeatureDataSchema | null;
  error: string | null;
}

/** One versioned file attached to a record, under one ``file`` field. */
export interface FeatureDataFile {
  id: string;
  field_key: string;
  /** Monotonic per ``(record, field)``; the newest version is ``current``. */
  version: number;
  filename: string;
  content_type: string;
  /** Size in bytes. */
  size: number;
  sha256: string;
  note: string;
  uploaded_by: number;
  created_at: number;
  current: boolean;
}

/** One business record, with the files attached to its ``file`` fields. */
export interface FeatureDataRecord {
  id: string;
  feature_id?: string | null;
  object_key: string;
  title: string;
  values: Record<string, unknown>;
  visibility: FeatureDataVisibility;
  revision: number;
  created_by: number;
  responsible_user_id?: number | null;
  can_write?: boolean;
  created_at: number;
  updated_at: number;
  files?: FeatureDataFile[];
  references?: Record<string, FeatureDataReferenceInfo>;
}

export interface FeatureDataReferenceInfo {
  kind: "record_ref" | "knowledge_ref";
  available: boolean;
  stale?: boolean;
  record_id?: string | null;
  agent_id?: string;
  object_key?: string;
  kb_id?: string;
  document_id?: string;
  version?: string;
  current_version?: string;
  page?: number | null;
  layout?: string | null;
  title?: string | null;
  path?: string | null;
  original_url?: string;
  preview_url?: string;
  current_original_url?: string;
  current_preview_url?: string;
}
export interface FeatureDataPrincipal {
  id: number;
  username: string;
  display_name: string | null;
  org_unit: string | null;
  org_unit_label: FeatureDataLocalizedText | string | null;
  role: string;
}

/** One page of records, newest first. */
export interface FeatureDataRecordsResponse {
  records: FeatureDataRecord[];
  total: number;
}

/** One field-level change recorded against a record. */
export interface FeatureDataChangeItem {
  field?: string;
  before?: unknown;
  after?: unknown;
  [key: string]: unknown;
}

/** One audit entry: what happened to a record, and what it changed. */
export interface FeatureDataChange {
  id: string;
  record_id?: string;
  user_id?: number;
  /** ``create`` | ``update`` | ``publish`` | ``file`` | … */
  kind?: string;
  summary?: string;
  items?: FeatureDataChangeItem[];
  created_at: number;
}

/** One in-app notification produced from a reminder. */
export interface FeatureDataNotification {
  id: string;
  reminder_id?: string;
  title: string;
  body?: string;
  /** First delivery channel; later email/IM adapters reuse this row. */
  channel?: string;
  read_at: number | null;
  created_at: number;
}

/** A page of records to read. */
export interface FeatureDataFilter {
  field: string;
  op:
    | "eq"
    | "ne"
    | "contains"
    | "gt"
    | "gte"
    | "lt"
    | "lte"
    | "in"
    | "is_empty";
  value?: unknown;
}
export interface FeatureDataRecordsQuery {
  q?: string;
  limit?: number;
  offset?: number;
  filters?: FeatureDataFilter[];
  sort_by?: string;
  sort_order?: "asc" | "desc";
}

function agentBase(agentId: string): string {
  return `/agents/${encodeURIComponent(agentId)}/feature-data`;
}

function recordsBase(agentId: string, objectKey: string): string {
  return `${agentBase(agentId)}/${encodeURIComponent(objectKey)}/records`;
}

function recordBase(agentId: string, recordId: string): string {
  return `${agentBase(agentId)}/records/${encodeURIComponent(recordId)}`;
}

/**
 * Accept either the documented envelope or a bare list.
 *
 * The history and notification endpoints answer a collection; the design does not
 * pin whether it arrives wrapped. Reading both shapes keeps the panel from
 * breaking on the one the server chose, without guessing at record fields.
 */
function listFrom<T>(answer: unknown, key: string): T[] {
  if (Array.isArray(answer)) return answer as T[];
  if (answer && typeof answer === "object") {
    const value = (answer as Record<string, unknown>)[key];
    if (Array.isArray(value)) return value as T[];
  }
  return [];
}

export const featureDataApi = {
  getSchema(agentId: string): Promise<FeatureDataSchemaResponse> {
    return request<FeatureDataSchemaResponse>(`${agentBase(agentId)}/schema`);
  },
  putSchema(
    agentId: string,
    schema: FeatureDataSchema,
  ): Promise<FeatureDataSchemaResponse> {
    return request<FeatureDataSchemaResponse>(`${agentBase(agentId)}/schema`, {
      method: "PUT",
      body: JSON.stringify({ schema }),
    });
  },
  listRecords(
    agentId: string,
    objectKey: string,
    query: FeatureDataRecordsQuery = {},
  ): Promise<FeatureDataRecordsResponse> {
    const params = new URLSearchParams();
    const q = (query.q ?? "").trim();
    if (q) params.set("q", q);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.offset !== undefined) params.set("offset", String(query.offset));
    if (query.filters?.length)
      params.set("filters", JSON.stringify(query.filters));
    if (query.sort_by) params.set("sort_by", query.sort_by);
    if (query.sort_order) params.set("sort_order", query.sort_order);
    const suffix = params.toString();
    return request<FeatureDataRecordsResponse>(
      `${recordsBase(agentId, objectKey)}${suffix ? `?${suffix}` : ""}`,
    );
  },
  listRecordReferenceOptions(
    agentId: string,
    query: {
      target_agent_id?: string;
      object_key?: string;
      q?: string;
      limit?: number;
      offset?: number;
    },
  ) {
    const params = new URLSearchParams();
    Object.entries(query).forEach(([key, value]) => {
      if (value !== undefined && value !== "") params.set(key, String(value));
    });
    const suffix = params.toString();
    return request<{
      records: {
        id: string;
        title: string;
        object_key: string;
        agent_id: string;
      }[];
      total: number;
    }>(`${agentBase(agentId)}/references/records${suffix ? `?${suffix}` : ""}`);
  },
  listPrincipals(
    agentId: string,
    query: { q?: string; limit?: number; offset?: number } = {},
  ) {
    const params = new URLSearchParams();
    if (query.q) params.set("q", query.q);
    if (query.limit !== undefined) params.set("limit", String(query.limit));
    if (query.offset !== undefined) params.set("offset", String(query.offset));
    const suffix = params.toString();
    return request<{ principals: FeatureDataPrincipal[]; total: number }>(
      `${agentBase(agentId)}/principals${suffix ? `?${suffix}` : ""}`,
    );
  },
  listFeatureAgents() {
    return request<
      {
        agent_id: string;
        kind?: string | null;
        name: string;
        can_manage?: boolean;
      }[]
    >("/agents");
  },
  handover(
    agentId: string,
    body: { from_user_id: number; to_user_id: number; object_key?: string },
  ) {
    return request<{ transferred_count: number; record_ids: string[] }>(
      `${agentBase(agentId)}/handover`,
      { method: "POST", body: JSON.stringify(body) },
    );
  },
  createRecord(
    agentId: string,
    objectKey: string,
    values: Record<string, unknown>,
  ) {
    return request<FeatureDataRecord>(recordsBase(agentId, objectKey), {
      method: "POST",
      body: JSON.stringify({ values }),
    });
  },
  getRecord(agentId: string, recordId: string) {
    return request<FeatureDataRecord>(recordBase(agentId, recordId));
  },
  updateRecord(
    agentId: string,
    recordId: string,
    values: Record<string, unknown>,
    expectedRevision: number,
  ) {
    return request<FeatureDataRecord>(recordBase(agentId, recordId), {
      method: "PATCH",
      body: JSON.stringify({ values, expected_revision: expectedRevision }),
    });
  },
  publishRecord(agentId: string, recordId: string) {
    return request<FeatureDataRecord>(
      `${recordBase(agentId, recordId)}/publish`,
      { method: "POST" },
    );
  },
  deleteRecord(agentId: string, recordId: string) {
    return request<void>(recordBase(agentId, recordId), { method: "DELETE" });
  },
  async history(agentId: string, recordId: string, limit = 20) {
    return listFrom<FeatureDataChange>(
      await request<unknown>(
        `${recordBase(agentId, recordId)}/history?limit=${limit}`,
      ),
      "changes",
    );
  },
  async files(agentId: string, recordId: string) {
    return listFrom<FeatureDataFile>(
      await request<unknown>(`${recordBase(agentId, recordId)}/files`),
      "files",
    );
  },
  uploadFile(
    agentId: string,
    recordId: string,
    fieldKey: string,
    file: File,
    note?: string,
  ) {
    const form = new FormData();
    form.append("file", file);
    const reason = (note ?? "").trim();
    if (reason) form.append("note", reason);
    return requestUpload<FeatureDataFile>(
      `${recordBase(agentId, recordId)}/files/${encodeURIComponent(fieldKey)}`,
      form,
    );
  },
  downloadFile(agentId: string, recordId: string, fileId: string) {
    return requestBlob(
      `${recordBase(agentId, recordId)}/files/${encodeURIComponent(
        fileId,
      )}/download`,
      { cache: "no-store" },
    );
  },
  async notifications(agentId: string, unreadOnly = false) {
    return listFrom<FeatureDataNotification>(
      await request<unknown>(
        `${agentBase(agentId)}/notifications${
          unreadOnly ? "?unread_only=true" : ""
        }`,
      ),
      "notifications",
    );
  },
  readNotification(agentId: string, notificationId: string) {
    return request<void>(
      `${agentBase(agentId)}/notifications/${encodeURIComponent(
        notificationId,
      )}/read`,
      { method: "POST" },
    );
  },
  completeReminder(agentId: string, reminderId: string) {
    return request<void>(
      `${agentBase(agentId)}/reminders/${encodeURIComponent(
        reminderId,
      )}/complete`,
      { method: "POST" },
    );
  },
};
