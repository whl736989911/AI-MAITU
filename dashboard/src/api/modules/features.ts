import { request } from "../request";

/** Bilingual copy as declared in ``feature.json`` (``label`` / ``title`` / ``description``). */
export interface LocalizedText {
  zh: string;
  en: string;
}

/** Field types the catalog accepts inside ``input_schema``. */
export type FeatureFieldType =
  | "string"
  | "number"
  | "integer"
  | "boolean"
  | "array";

/** One input field — a subset of JSON Schema, plus the bilingual ``title``. */
export interface FeatureFieldSchema {
  type: FeatureFieldType;
  title?: LocalizedText;
  description?: LocalizedText;
  /** ``textarea`` | ``date`` | ``email``; anything else renders as a plain input. */
  format?: string;
  enum?: string[];
  /** Element schema when ``type`` is ``array``; nested arrays render as a grid. */
  items?: FeatureFieldSchema;
}

export interface FeatureInputSchema {
  type: "object";
  title?: LocalizedText;
  description?: LocalizedText;
  properties: Record<string, FeatureFieldSchema>;
  /** Property names the user must fill in. */
  required?: string[];
}

/** Per-field widget overrides plus the declared field order. */
export interface FeatureUiSchema {
  order?: string[];
  widgets?: Record<string, string>;
}

export type FeatureOutputKind = "markdown" | "json" | "text";

/** List-level feature card: no schemas, no prompt plumbing. */
export interface FeatureSummary {
  id: string;
  version: number;
  label: LocalizedText;
  description: LocalizedText;
  icon_name: string;
  /** Optional tint; the card falls back to the brand accent when null. */
  color: string | null;
  /** Organization unit key used for grouping. */
  unit: string;
  output_kind: FeatureOutputKind;
  permissions: Record<string, unknown>;
}

/** Full definition behind ``GET /api/features/{id}``. */
export interface Feature extends FeatureSummary {
  input_schema: FeatureInputSchema;
  ui_schema: FeatureUiSchema;
  user_template: string;
  system_prompt: string | null;
}

export interface FeatureUnit {
  key: string;
  count: number;
}

export interface FeatureListResponse {
  features: FeatureSummary[];
  units: FeatureUnit[];
}

export interface FeatureRunResponse {
  task_id: string;
  output: string;
  output_kind: FeatureOutputKind;
}

/** Values collected by the schema-driven form, keyed by property name. */
export type FeatureInputs = Record<string, unknown>;

/**
 * One entry of the stored draft→final edit script.
 *
 * ``keep`` / ``remove`` / ``add`` carry ``text``; ``replace`` pairs ``draft``
 * with ``final``. An untouched draft — or one edited only in whitespace —
 * yields ``[]``, which is itself a strong positive sample.
 */
export interface FeatureDiffSegment {
  op: "keep" | "remove" | "add" | "replace";
  text?: string;
  draft?: string;
  final?: string;
}

/** Rule lifecycle. Both review decisions are final — a rule is reviewed once. */
export type FeatureRuleStatus = "draft" | "approved" | "rejected";

/** One induced rule plus the provenance the review UI has to show. */
export interface FeatureRule {
  id: string;
  feature_id: string;
  rule_text: string;
  status: FeatureRuleStatus;
  /** Run ids the rule was induced from; empty when the column is unreadable. */
  source_task_ids: string[];
  /** ``"ai"`` for the extractor, ``"user:<id>"`` for a human proposal. */
  proposed_by: string;
  approved_by: number | null;
  /** Epoch seconds. */
  created_at: number;
  reviewed_at: number | null;
}

export interface FeatureRuleListResponse {
  feature_id: string;
  rules: FeatureRule[];
}

/** ``POST /features/tasks/{id}/finalize`` — the human-approved text and its diff. */
export interface FeatureFinalizedTask {
  task_id: string;
  feature_id: string;
  status: string;
  final: string;
  diff: FeatureDiffSegment[];
  /** Epoch seconds. */
  finalized_at: number | null;
}

/** One promoted case; the run's content is referenced, never copied. */
export interface FeatureCase {
  task_id: string;
  feature_id: string;
  promoted_by: number;
  /** Epoch seconds. */
  promoted_at: number;
  note: string | null;
  inputs: FeatureInputs;
  final: string;
  diff: FeatureDiffSegment[];
}

export interface FeatureCaseListResponse {
  feature_id: string;
  cases: FeatureCase[];
}

export interface FeaturePromoteResponse {
  case: FeatureCase;
}

export const featuresApi = {
  listFeatures: () => request<FeatureListResponse>("/features"),
  getFeature: (id: string) =>
    request<Feature>(`/features/${encodeURIComponent(id)}`),
  runFeature: (id: string, inputs: FeatureInputs) =>
    request<FeatureRunResponse>(`/features/${encodeURIComponent(id)}/run`, {
      method: "POST",
      body: JSON.stringify({ inputs }),
    }),
  /** Store the approved text for one run. One-way: a run finalizes once (409 on retry). */
  finalizeTask: (taskId: string, final: string) =>
    request<FeatureFinalizedTask>(
      `/features/tasks/${encodeURIComponent(taskId)}/finalize`,
      { method: "POST", body: JSON.stringify({ final }) },
    ),
  /** Hand a finalized run to the case library. Nothing is promoted automatically. */
  promoteTask: (taskId: string, note?: string) =>
    request<FeaturePromoteResponse>(
      `/features/tasks/${encodeURIComponent(taskId)}/promote`,
      {
        method: "POST",
        body: JSON.stringify({ note: note?.trim() ? note.trim() : null }),
      },
    ),
  listRules: (featureId: string) =>
    request<FeatureRuleListResponse>(
      `/features/${encodeURIComponent(featureId)}/rules`,
    ),
  /** Induce draft rules from finalized runs. 409 when there is nothing to learn from. */
  extractRules: (featureId: string) =>
    request<FeatureRuleListResponse>(
      `/features/${encodeURIComponent(featureId)}/rules/extract`,
      { method: "POST" },
    ),
  approveRule: (ruleId: string) =>
    request<FeatureRule>(
      `/features/rules/${encodeURIComponent(ruleId)}/approve`,
      { method: "POST" },
    ),
  rejectRule: (ruleId: string) =>
    request<FeatureRule>(
      `/features/rules/${encodeURIComponent(ruleId)}/reject`,
      { method: "POST" },
    ),
  listCases: (featureId: string) =>
    request<FeatureCaseListResponse>(
      `/features/${encodeURIComponent(featureId)}/cases`,
    ),
};
