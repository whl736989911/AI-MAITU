/**
 * Rendering rules for a feature's business data — pure, so they can be argued with.
 *
 * Three decisions live here rather than in a component, because every view makes
 * the same one:
 *
 *   - **A label follows the reader's language.** A schema may carry a
 *     ``{"zh": …, "en": …}`` pair or one plain string; the same lookup serves both,
 *     so the object list, the table header and the editor agree.
 *   - **A file field is never a text value.** ``file`` fields are managed by the
 *     attachment endpoints, so they are excluded from an editable form and from the
 *     payload a save sends — the one way a stray filename could otherwise land in
 *     ``values`` and be treated as content.
 *   - **What is shown is not what is stored.** Numbers may arrive as strings from
 *     an input; the display helper formats for reading, and the save helper sends
 *     the raw value the server's validator understands.
 */

import type {
  FeatureDataField,
  FeatureDataFile,
  FeatureDataLocalizedText,
  FeatureDataObject,
  FeatureDataRecord,
  FeatureDataSchema,
} from "../../../api/modules/featureData";
import { FEATURE_DATA_FIELD_TYPES } from "../../../api/modules/featureData";

/** One language's text from a ``{"zh": …, "en": …}`` pair, else the plain string. */
export function localizedText(
  value: FeatureDataLocalizedText | string | undefined | null,
  language: string,
  fallback = "",
): string {
  if (typeof value === "string") {
    const trimmed = value.trim();
    return trimmed || fallback;
  }
  if (!value || typeof value !== "object") return fallback;
  const preferred = language.toLowerCase().startsWith("zh") ? "zh" : "en";
  const other = preferred === "zh" ? "en" : "zh";
  const first = value[preferred];
  const second = value[other];
  if (typeof first === "string" && first.trim()) return first.trim();
  if (typeof second === "string" && second.trim()) return second.trim();
  return fallback;
}

/** The objects a schema declares, in declaration order (a malformed one is empty). */
export function schemaObjects(
  schema: FeatureDataSchema | null | undefined,
): FeatureDataObject[] {
  const objects = schema?.objects;
  if (!Array.isArray(objects)) return [];
  return objects.filter(
    (object): object is FeatureDataObject =>
      !!object && typeof object === "object" && typeof object.key === "string",
  );
}

/** The object with this key, or ``null``. */
export function findObject(
  schema: FeatureDataSchema | null | undefined,
  key: string | null | undefined,
): FeatureDataObject | null {
  if (!key) return null;
  return schemaObjects(schema).find((object) => object.key === key) ?? null;
}

/** What the object is called to a reader. */
export function objectLabel(
  object: FeatureDataObject | null | undefined,
  language: string,
): string {
  if (!object) return "";
  return localizedText(object.name, language, object.key);
}

/** What one field is called to a reader (its key when it has no label). */
export function fieldLabel(field: FeatureDataField, language: string): string {
  return localizedText(field.label, language, field.key);
}

/** The fields of an object, in declaration order. */
export function objectFields(
  object: FeatureDataObject | null | undefined,
): FeatureDataField[] {
  const fields = object?.fields;
  if (!Array.isArray(fields)) return [];
  return fields.filter(
    (field): field is FeatureDataField =>
      !!field && typeof field === "object" && typeof field.key === "string",
  );
}

/** Whether a field's value is managed by the attachment endpoints. */
export function isFileField(field: FeatureDataField): boolean {
  return field.type === "file";
}

/** The fields a value form edits: everything except the file-managed ones. */
export function valueFields(
  object: FeatureDataObject | null | undefined,
): FeatureDataField[] {
  return objectFields(object).filter((field) => !isFileField(field));
}

/** The file-managed fields, each with its attachment versions. */
export function fileFields(
  object: FeatureDataObject | null | undefined,
): FeatureDataField[] {
  return objectFields(object).filter(isFileField);
}

/** The field whose value titles a record, else ``""``. */
export function titleField(
  object: FeatureDataObject | null | undefined,
): string {
  const configured = (object?.title_field ?? "").trim();
  if (configured) return configured;
  const first = objectFields(object)[0];
  return first?.key ?? "";
}

/** A record's title, falling back to its id so a row is never blank. */
export function recordTitle(record: FeatureDataRecord): string {
  const title = (record.title ?? "").trim();
  return title || record.id;
}

/** ``true`` for a controlled document object. */
export function isControlled(
  object: FeatureDataObject | null | undefined,
): boolean {
  return object?.controlled === true && object.kind === "document";
}

/**
 * The payload a save sends: only this object's editable fields, never a file one.
 *
 * ``undefined`` values are dropped so a save does not clear fields it was not
 * asked about; a blank string is kept, because clearing a value is a real edit.
 */
export function buildValuesPayload(
  object: FeatureDataObject | null | undefined,
  values: Record<string, unknown>,
): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const field of valueFields(object)) {
    if (field.computed) continue;
    const value = values[field.key];
    if (value === undefined) continue;
    out[field.key] = value;
  }
  return out;
}

/** Whether a required editable field has no answer yet. */
function missingRequired(field: FeatureDataField, value: unknown): boolean {
  if (!field.required) return false;
  if (field.type === "knowledge_ref") {
    if (value === null || typeof value !== "object") return true;
    const reference = value as Record<string, unknown>;
    return (
      typeof reference.kb_id !== "string" ||
      !reference.kb_id ||
      typeof reference.document_id !== "string" ||
      !reference.document_id
    );
  }
  if (typeof value === "boolean") return false;
  if (typeof value === "number") return Number.isNaN(value);
  if (Array.isArray(value)) return value.length === 0;
  return value === undefined || value === null || String(value).trim() === "";
}

/**
 * The required editable fields left blank, as their keys.
 *
 * File fields are not checked here: an attachment is uploaded from its own
 * control, and the server owns whether a required document must already have one.
 */
export function missingRequiredFields(
  object: FeatureDataObject | null | undefined,
  values: Record<string, unknown>,
): string[] {
  const missing: string[] = [];
  for (const field of valueFields(object)) {
    if (field.computed) continue;
    if (missingRequired(field, values[field.key])) missing.push(field.key);
  }
  return missing;
}

/** Group attachments by the field they belong to, newest version first. */
export function filesByField(
  files: FeatureDataFile[] | undefined,
): Record<string, FeatureDataFile[]> {
  const grouped: Record<string, FeatureDataFile[]> = {};
  for (const file of files ?? []) {
    if (!file || typeof file.field_key !== "string") continue;
    (grouped[file.field_key] ??= []).push(file);
  }
  for (const versions of Object.values(grouped)) {
    versions.sort((a, b) => b.version - a.version);
  }
  return grouped;
}

/** The attachment a field shows as current (the newest version marked so). */
export function currentFile(
  versions: FeatureDataFile[] | undefined,
): FeatureDataFile | null {
  if (!versions || versions.length === 0) return null;
  return versions.find((file) => file.current) ?? versions[0];
}

/** A value as a reader sees it; file fields are never rendered from ``values``. */
export function displayFieldValue(
  field: FeatureDataField,
  value: unknown,
  booleanLabels: { yes: string; no: string } = { yes: "true", no: "false" },
): string {
  if (isFileField(field)) return "";
  if (value === undefined || value === null || value === "") return "";
  if (typeof value === "boolean")
    return value ? booleanLabels.yes : booleanLabels.no;
  if (Array.isArray(value)) {
    return value.map((item) => String(item)).join(", ");
  }
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  if (field.type === "enum" && typeof value === "string") {
    return value;
  }
  return String(value);
}

/** Human-readable byte size for an attachment row. */
export function formatFileSize(size: number): string {
  if (!Number.isFinite(size) || size <= 0) return "0 B";
  const units = ["B", "KB", "MB", "GB"];
  let value = size;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit += 1;
  }
  const rounded =
    unit === 0 ? String(value) : value.toFixed(1).replace(/\.0$/, "");
  return `${rounded} ${units[unit]}`;
}

/** The schema as the JSON editor shows it — stable key order, readable indent. */
export function schemaToJson(
  schema: FeatureDataSchema | null | undefined,
): string {
  return JSON.stringify(schema ?? emptySchema(), null, 2);
}

/** A schema with no objects: what an editor starts from when none is stored. */
export function emptySchema(): FeatureDataSchema {
  return { version: 1, objects: [] };
}

export type SchemaReadProblem = "invalidJson" | "invalidShape" | null;

/** The result of reading the JSON editor's text back into a schema. */
export interface SchemaRead {
  schema: FeatureDataSchema | null;
  problem: SchemaReadProblem;
}

/**
 * Read the JSON editor's text as a schema, or say which rule it broke.
 *
 * Only the shape the editor can check on its own is checked — a document with an
 * ``objects`` array. Field-level rules belong to the server, whose refusal lists
 * every problem at once; duplicating them here would be a second rulebook that
 * drifts from the one that actually writes.
 */
export function readSchemaJson(text: string): SchemaRead {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text);
  } catch {
    return { schema: null, problem: "invalidJson" };
  }
  if (!parsed || typeof parsed !== "object" || Array.isArray(parsed)) {
    return { schema: null, problem: "invalidShape" };
  }
  const objects = (parsed as { objects?: unknown }).objects;
  if (!Array.isArray(objects)) {
    return { schema: null, problem: "invalidShape" };
  }
  return { schema: parsed as FeatureDataSchema, problem: null };
}

/** Whether a field type is one this build knows (a schema may be newer). */
export function isKnownFieldType(type: string): boolean {
  return (FEATURE_DATA_FIELD_TYPES as readonly string[]).includes(type);
}
