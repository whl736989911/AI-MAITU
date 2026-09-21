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
  /**
   * The feature's capability layer: what every run of it may use (design 5.1).
   * ``null`` means the definition declares none at all — not an empty layer —
   * and such a run keeps whatever the caller's own agent has.
   */
  agent: FeatureAgent | null;
}

/**
 * Capability layer of one feature's own agent.
 *
 * Every key is optional by contract: an absent key *inherits* the caller's
 * agent, while a present list is a scope — and an empty list is the explicit
 * "none of them". That difference is why this is not all ``?: string[]`` with
 * ``?? []`` defaults anywhere: dropping ``[]`` would silently widen the run.
 */
export interface FeatureAgent {
  /** ``<provider>/<model>`` the run must use; absent inherits the agent's default. */
  model?: string | null;
  temperature?: number | null;
  top_p?: number | null;
  max_tokens?: number | null;
  max_iters?: number | null;
  max_input_length?: number | null;
  /** Built-in tools this run switches off. */
  tools_disabled?: string[] | null;
  /** Skills the run may use; ``[]`` disables all of them. */
  skills?: string[] | null;
  /** Subagent types the run may dispatch; ``[]`` hides ``task`` entirely. */
  subagents?: string[] | null;
  /** Connectors to mount, with the *caller's* credentials (design 5.2). */
  mcp_servers?: string[] | null;
  /** Knowledge bases the run may read, intersected with the caller's visibility. */
  knowledge_base_ids?: string[] | null;
}

/** One connectable MCP server, as the caller's own instances allow. */
export interface FeatureConnectorChoice {
  name: string;
  label: string;
}

export interface FeatureKnowledgeBaseChoice {
  id: string;
  name: string;
}

export interface FeatureModelChoice {
  ref: string;
  label: string;
}

export interface FeatureToolChoice {
  name: string;
  category: string;
}

/**
 * Everything a definition's ``agent`` node may name, resolved for the *caller*
 * behind ``GET /api/features/_capabilities``.
 *
 * Separate from ``FeatureMeta`` because skills and subagents can only be read
 * off a live agent: this call needs one, and the editor makes it when the
 * capability block is opened rather than when the drawer opens.
 */
export interface FeatureCapabilities {
  models: FeatureModelChoice[];
  /** Built-in tools a feature may switch off (never the always-on ones). */
  tools: FeatureToolChoice[];
  skills: string[];
  subagents: string[];
  mcp_servers: FeatureConnectorChoice[];
  knowledge_bases: FeatureKnowledgeBaseChoice[];
}

export interface FeatureUnit {
  key: string;
  count: number;
}

/** Definition-format metadata behind ``GET /api/features/_meta`` — the settings editor's choices. */
export interface FeatureMeta {
  /** Org-unit keys a feature may be filed under. */
  units: string[];
  /** Selectable ``icon_name`` values (kebab-case lucide names). */
  icons: string[];
  /** Allowed ``output.kind`` values. */
  output_kinds: FeatureOutputKind[];
  /** Features shipped with the app: readable everywhere, never editable. */
  bundled_ids: string[];
}

/**
 * ``prompt`` as the write endpoints take it. The system prompt travels as its
 * *content*: the file name is the server's business (it fixes ``PROMPT.md`` and
 * writes ``prompt.system_file`` itself).
 */
export interface FeaturePromptBody {
  user_template: string;
  /** Body of the feature's ``PROMPT.md``; ``null`` when it has none. */
  system_prompt: string | null;
}

/** Access rules as written. An omitted list declares no restriction at all. */
export interface FeaturePermissionsBody {
  allow_units?: string[];
  allow_roles?: string[];
}

/** Request body of ``POST /features`` and ``PUT /features/{id}``. */
export interface FeatureDefinitionBody {
  id: string;
  label: LocalizedText;
  description: LocalizedText;
  icon_name: string;
  /** ``null`` falls back to the brand accent. */
  color: string | null;
  unit: string;
  input_schema: FeatureInputSchema;
  ui_schema: FeatureUiSchema;
  prompt: FeaturePromptBody;
  output: { kind: FeatureOutputKind };
  permissions: FeaturePermissionsBody;
  /**
   * The capability layer, as authored. ``null`` writes no layer at all and a
   * node without keys is dropped — the server omits what was never declared
   * rather than storing nulls that would read back as deliberate choices.
   */
  agent: FeatureAgent | null;
}

/** Both write endpoints answer with the id they wrote. */
export interface FeatureWriteResponse {
  feature_id: string;
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

/**
 * Which layer a rule lives in. Injection reads all three at once, narrowest
 * first: ``personal`` → ``unit`` → ``global``, so the closer rule wins.
 */
export type FeatureRuleScope = "personal" | "unit" | "global";

/**
 * The layer as the server reports it. The three names above are the layers this
 * build knows how to group by; anything else is a stored value it cannot name —
 * ``"unknown"`` from the server, a layer written by an older or newer build, or
 * a payload that carries no layer at all. Such a rule must still be rendered
 * (the rules panel files it under its unfiled heading), never dropped.
 */
export type FeatureRuleScopeValue = FeatureRuleScope | (string & {});

/** One induced rule plus the provenance the review UI has to show. */
export interface FeatureRule {
  id: string;
  feature_id: string;
  rule_text: string;
  status: FeatureRuleStatus;
  /**
   * The layer this rule belongs to; decide who may review it, never trust it to.
   * The server always sends one — ``"unknown"`` when it cannot name the stored
   * layer — but it is typed optional because a payload can arrive without it, and
   * a rule whose layer cannot be read has to render (unfiled), not disappear.
   */
  scope?: FeatureRuleScopeValue | null;
  /** Owner of a personal rule; ``null`` for unit and global rules. */
  owner_user_id: number | null;
  /** Org unit a unit rule speaks for; ``null`` for personal and global rules. */
  unit_key: string | null;
  /** Display name of ``unit_key`` when the server could resolve it. */
  unit_label?: string | null;
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

/** ``POST /features/rules/{id}/submit`` — the draft opened in the target scope. */
export interface FeatureRuleSubmitResponse {
  rule: FeatureRule;
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
  /** Definition-format metadata for the settings editor. */
  getFeatureMeta: () => request<FeatureMeta>("/features/_meta"),
  /**
   * The capability choices that need the caller's own agent. Costs an agent
   * start, so the editor asks for it only once its capability block is opened.
   */
  getFeatureCapabilities: () =>
    request<FeatureCapabilities>("/features/_capabilities"),
  getFeature: (id: string) =>
    request<Feature>(`/features/${encodeURIComponent(id)}`),
  /** Create one feature; the id is the caller's (409 when it is already taken). */
  createFeature: (body: FeatureDefinitionBody) =>
    request<FeatureWriteResponse>("/features", {
      method: "POST",
      body: JSON.stringify(body),
    }),
  /** Overwrite one definition; the body's id must match the path. */
  updateFeature: (id: string, body: FeatureDefinitionBody) =>
    request<FeatureWriteResponse>(`/features/${encodeURIComponent(id)}`, {
      method: "PUT",
      body: JSON.stringify(body),
    }),
  /** Delete a user feature — bundled ones are refused (403). */
  deleteFeature: (id: string) =>
    request<void>(`/features/${encodeURIComponent(id)}`, { method: "DELETE" }),
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
  /**
   * Induce draft rules from finalized runs, reading only the requested layer's
   * evidence: ``personal`` your own runs, ``unit`` your unit's members,
   * ``global`` every run. 409 when there is nothing to learn from.
   */
  extractRules: (featureId: string, scope: FeatureRuleScope) =>
    request<FeatureRuleListResponse>(
      `/features/${encodeURIComponent(featureId)}/rules/extract?scope=${scope}`,
      { method: "POST" },
    ),
  /**
   * Ask to lift a personal rule into ``target_scope``. The personal rule stays
   * as it is — a copy waits for review in the wider layer.
   */
  submitRule: (
    ruleId: string,
    targetScope: Exclude<FeatureRuleScope, "personal">,
    reason?: string,
  ) =>
    request<FeatureRuleSubmitResponse>(
      `/features/rules/${encodeURIComponent(ruleId)}/submit`,
      {
        method: "POST",
        body: JSON.stringify({
          target_scope: targetScope,
          reason: reason?.trim() ? reason.trim() : null,
        }),
      },
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
