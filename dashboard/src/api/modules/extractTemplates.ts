import { request } from "../request";

/**
 * Extraction templates (design §7).
 *
 * A template is an enterprise resource, not a knowledge base's: the routes take
 * no base id, and it is bound to places *inside* sources (§7.4). Editing one
 * writes a new version rather than overwriting it, which is why
 * {@link ExtractTemplateVersion} is its own object — a result keeps naming the
 * version that produced it.
 *
 * ``appliesTo`` is the template's own statement of the file types it is for. A
 * file of another type is not given the template even when a binding points at
 * it, so a contract template cannot be handed a spreadsheet.
 */

/** Field types the backend accepts (``octop.infra.knowledge.template_match``). */
export type ExtractFieldType =
  | "text"
  | "string[]"
  | "date"
  | "number"
  | "amount"
  | "boolean"
  | "enum";

export const EXTRACT_FIELD_TYPES: ExtractFieldType[] = [
  "text",
  "string[]",
  "date",
  "number",
  "amount",
  "boolean",
  "enum",
];

export interface ExtractField {
  name: string;
  type: ExtractFieldType;
  required: boolean;
  instruction: string;
  /** Values to choose from when ``type`` is ``enum``. */
  options?: string[];
}

export interface ExtractTemplate {
  id: string;
  template_id: string;
  name: string;
  description: string;
  status: "active" | "disabled";
  current_version: number;
  /** How many places this template is bound to; a bound template is not deleted. */
  bindings: number;
  fields: ExtractField[];
  instruction: string;
  applies_to: string;
  created_at: number;
  updated_at: number;
}

export interface ExtractTemplateVersion {
  id: string;
  version_id: string;
  template_id: string;
  version: number;
  fields: ExtractField[];
  instruction: string;
  applies_to: string;
  note: string;
  created_at: number;
}

export interface ExtractBinding {
  id: string;
  binding_id: string;
  template_id: string;
  data_source_id: string;
  /** Empty is the whole source; a folder path covers its contents; one file's path is that file. */
  path: string;
  extension: string;
  mime_type: string;
  name_pattern: string;
  match_regex: string;
}

export interface ExtractMatch {
  template_id: string | null;
  binding_id: string | null;
  level: "" | "file" | "folder" | "source";
  /** Template ids that tie at the winning level: the design reports this rather than picking one. */
  conflicts: string[];
  content_type: string;
}

/** One stored extraction: what a template produced for a document (design §7.3). */
export interface ExtractResult {
  id: string;
  result_id: string;
  document_id: string;
  template_id: string;
  /** The template version that produced this, which never changes under it. */
  template_version: number;
  status: "pending" | "processing" | "succeeded" | "failed";
  fields: Record<string, unknown>;
  error: string | null;
  model: string;
  parser_version: string;
  content_hash: string;
  created_at: number;
  updated_at: number;
  /** The file it came from, or null when that document is gone. */
  document: {
    filename: string;
    path: string;
    source_path: string;
  } | null;
}

export interface RunScopeBody {
  kb_id: string;
  /** Folder to limit the run to; empty means the whole knowledge base. */
  path?: string;
  /** Retry only what failed. */
  only_failed?: boolean;
  /** Skip documents whose result already succeeded at the current version. */
  only_stale?: boolean;
  limit?: number;
}

export interface RunScopeCounts {
  matched: number;
  succeeded: number;
  failed: number;
  skipped: number;
}

export interface TemplateBody {
  name: string;
  description?: string;
  fields: ExtractField[];
  instruction?: string;
  applies_to?: string;
  note?: string;
}

export interface BindingBody {
  data_source_id: string;
  path?: string;
  extension?: string;
  mime_type?: string;
  name_pattern?: string;
  match_regex?: string;
}

export const extractTemplatesApi = {
  list: () => request<ExtractTemplate[]>("/extract-templates"),

  get: (templateId: string) =>
    request<ExtractTemplate>(`/extract-templates/${templateId}`),

  create: (body: TemplateBody) =>
    request<ExtractTemplate>("/extract-templates", {
      method: "POST",
      body: JSON.stringify(body),
    }),

  update: (
    templateId: string,
    body: {
      name?: string;
      description?: string;
      status?: "active" | "disabled";
    },
  ) =>
    request<ExtractTemplate>(`/extract-templates/${templateId}`, {
      method: "PATCH",
      body: JSON.stringify(body),
    }),

  remove: (templateId: string) =>
    request<void>(`/extract-templates/${templateId}`, { method: "DELETE" }),

  listVersions: (templateId: string) =>
    request<ExtractTemplateVersion[]>(
      `/extract-templates/${templateId}/versions`,
    ),

  addVersion: (
    templateId: string,
    body: {
      fields: ExtractField[];
      instruction?: string;
      applies_to?: string;
      note?: string;
    },
  ) =>
    request<ExtractTemplateVersion>(
      `/extract-templates/${templateId}/versions`,
      { method: "POST", body: JSON.stringify(body) },
    ),

  listBindings: (templateId: string) =>
    request<ExtractBinding[]>(`/extract-templates/${templateId}/bindings`),

  bind: (templateId: string, body: BindingBody) =>
    request<ExtractBinding>(`/extract-templates/${templateId}/bindings`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  unbind: (bindingId: string) =>
    request<void>(`/extract-templates/bindings/${bindingId}`, {
      method: "DELETE",
    }),

  /** Run a template over a scope of documents (design §8.4). */
  run: (templateId: string, body: RunScopeBody) =>
    request<RunScopeCounts>(`/extract-templates/${templateId}/run`, {
      method: "POST",
      body: JSON.stringify(body),
    }),

  /** What a template has produced, with the file each result came from. */
  listResults: (templateId: string) =>
    request<ExtractResult[]>(`/extract-templates/${templateId}/results`),

  /** Which template reads this path — the conflict check of design §7.4. */
  resolve: (dataSourceId: string, path: string) =>
    request<ExtractMatch>(
      `/extract-templates/resolve?${new URLSearchParams({
        data_source_id: dataSourceId,
        path,
      }).toString()}`,
    ),
};
