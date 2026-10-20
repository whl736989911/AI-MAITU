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
  /** Bilingual label; a plain string is accepted for hand-written schemas. */
  label?: FeatureDataLocalizedText | string;
  type: FeatureDataFieldType;
  required?: boolean;
  /** ``enum`` only — the choices, in the order they are offered. */
  options?: string[];
  /** ``date`` / ``datetime`` only — how many days before the date to remind. */
  reminder_days?: number[];
}

/** One business object: a ledger or a controlled document. */
export interface FeatureDataObject {
  /** ``^[a-z][a-z0-9_]{0,63}$``; the path segment records are addressed under. */
  key: string;
  name?: FeatureDataLocalizedText | string;
  kind?: FeatureDataObjectKind;
  /** The field whose value becomes the record's title. */
  title_field?: string;
  /** ``true`` when the versions of this document are controlled. */
  controlled?: boolean;
  fields?: FeatureDataField[];
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
  /** The feature's public id, when the server carries it on the row. */
  feature_id?: string | null;
  object_key: string;
  title: string;
  /** Field values keyed by field key. ``file`` fields never appear here. */
  values: Record<string, unknown>;
  visibility: FeatureDataVisibility;
  /** Optimistic lock: a write must send the revision it saw. */
  revision: number;
  created_by: number;
  created_at: number;
  updated_at: number;
  files?: FeatureDataFile[];
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
export interface FeatureDataRecordsQuery {
  q?: string;
  limit?: number;
  offset?: number;
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
  /** Read the schema. Readable by anyone who may reach the feature. */
  getSchema(agentId: string): Promise<FeatureDataSchemaResponse> {
    return request<FeatureDataSchemaResponse>(`${agentBase(agentId)}/schema`);
  },

  /** Store the schema (feature managers only). A refusal writes nothing. */
  putSchema(
    agentId: string,
    schema: FeatureDataSchema,
  ): Promise<FeatureDataSchemaResponse> {
    return request<FeatureDataSchemaResponse>(`${agentBase(agentId)}/schema`, {
      method: "PUT",
      body: JSON.stringify({ schema }),
    });
  },

  /** Visible records of one object, newest first. */
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
    const suffix = params.toString();
    return request<FeatureDataRecordsResponse>(
      `${recordsBase(agentId, objectKey)}${suffix ? `?${suffix}` : ""}`,
    );
  },

  /** Create a record. File fields are uploaded separately, after it exists. */
  createRecord(
    agentId: string,
    objectKey: string,
    values: Record<string, unknown>,
  ): Promise<FeatureDataRecord> {
    return request<FeatureDataRecord>(recordsBase(agentId, objectKey), {
      method: "POST",
      body: JSON.stringify({ values }),
    });
  },

  /** One record and its attachments. */
  getRecord(agentId: string, recordId: string): Promise<FeatureDataRecord> {
    return request<FeatureDataRecord>(recordBase(agentId, recordId));
  },

  /**
   * Update field values. ``expected_revision`` is the revision the editor read:
   * a mismatch is a real refusal (409), never a silent overwrite.
   */
  updateRecord(
    agentId: string,
    recordId: string,
    values: Record<string, unknown>,
    expectedRevision: number,
  ): Promise<FeatureDataRecord> {
    return request<FeatureDataRecord>(recordBase(agentId, recordId), {
      method: "PATCH",
      body: JSON.stringify({
        values,
        expected_revision: expectedRevision,
      }),
    });
  },

  /** Publish a private record to everybody who can use the feature. */
  publishRecord(agentId: string, recordId: string): Promise<FeatureDataRecord> {
    return request<FeatureDataRecord>(
      `${recordBase(agentId, recordId)}/publish`,
      {
        method: "POST",
      },
    );
  },

  /** Soft-delete a record (its files stay for audit). */
  deleteRecord(agentId: string, recordId: string): Promise<void> {
    return request<void>(recordBase(agentId, recordId), { method: "DELETE" });
  },

  /** The record's audit trail, newest first. */
  async history(
    agentId: string,
    recordId: string,
    limit = 20,
  ): Promise<FeatureDataChange[]> {
    const answer = await request<unknown>(
      `${recordBase(agentId, recordId)}/history?limit=${limit}`,
    );
    return listFrom<FeatureDataChange>(answer, "changes");
  },

  /** Attachments and their versions, newest version first per field. */
  async files(agentId: string, recordId: string): Promise<FeatureDataFile[]> {
    const answer = await request<unknown>(
      `${recordBase(agentId, recordId)}/files`,
    );
    return listFrom<FeatureDataFile>(answer, "files");
  },

  /**
   * Upload a new version for one ``file`` field.
   *
   * Multipart, not JSON: the browser owns the boundary, and ``note`` is optional so
   * an upload that only replaces bytes does not need a reason.
   */
  uploadFile(
    agentId: string,
    recordId: string,
    fieldKey: string,
    file: File,
    note?: string,
  ): Promise<FeatureDataFile> {
    const form = new FormData();
    form.append("file", file);
    const reason = (note ?? "").trim();
    if (reason) form.append("note", reason);
    return requestUpload<FeatureDataFile>(
      `${recordBase(agentId, recordId)}/files/${encodeURIComponent(fieldKey)}`,
      form,
    );
  },

  /** Download one file version as a blob (caller decides what to do with it). */
  downloadFile(
    agentId: string,
    recordId: string,
    fileId: string,
  ): Promise<Blob> {
    return requestBlob(
      `${recordBase(agentId, recordId)}/files/${encodeURIComponent(
        fileId,
      )}/download`,
      { cache: "no-store" },
    );
  },

  /** The caller's own in-app notifications; unread only when asked. */
  async notifications(
    agentId: string,
    unreadOnly = false,
  ): Promise<FeatureDataNotification[]> {
    const suffix = unreadOnly ? "?unread_only=true" : "";
    const answer = await request<unknown>(
      `${agentBase(agentId)}/notifications${suffix}`,
    );
    return listFrom<FeatureDataNotification>(answer, "notifications");
  },

  /** Mark one notification as read. */
  readNotification(agentId: string, notificationId: string): Promise<void> {
    return request<void>(
      `${agentBase(agentId)}/notifications/${encodeURIComponent(
        notificationId,
      )}/read`,
      { method: "POST" },
    );
  },

  /** Complete (dismiss) the reminder a notification came from. */
  completeReminder(agentId: string, reminderId: string): Promise<void> {
    return request<void>(
      `${agentBase(agentId)}/reminders/${encodeURIComponent(
        reminderId,
      )}/complete`,
      { method: "POST" },
    );
  },
};
