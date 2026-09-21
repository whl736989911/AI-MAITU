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
  /**
   * The run's step skeleton, in order — empty (or absent) means the run stays
   * single-shot, exactly as it behaved before steps existed.
   */
  steps?: FeatureStep[];
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
  /**
   * The step skeleton to write. Omitted when the editor holds no step at all:
   * an empty list and an absent list both mean "single-shot", and writing the
   * empty one would only add a key that says nothing.
   */
  steps?: FeatureStep[];
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

/**
 * How one step is executed. ``orchestrate`` — the model decomposing the step and
 * dispatching subagents itself — is a real mode: the run lets the model split the
 * step, and records what it split it into (``decomposition`` on the run's steps).
 */
export type FeatureStepMode = "agent" | "orchestrate";

/** Where the run stops around a step: nowhere, for a person, or for a check. */
export type FeatureStepGate = "auto" | "confirm" | "validate";

/** What a failed step does: stop the run, ask a person, or try again. */
export type FeatureStepOnFailure = "abort" | "escalate" | "retry";

/**
 * The typed artifact a step produces — the value later steps consume under
 * ``inputs``. ``schema`` is free text written in the artifact's own vocabulary
 * (``"table:12cols"``); the platform passes it along, it does not interpret it.
 */
export interface FeatureStepOutput {
  name: string;
  schema: string;
}

/** One step of a definition, exactly as ``feature.json`` stores it (design 7.10). */
export interface FeatureStep {
  id: string;
  name: string;
  /** Absent means the definition never declared one — not a default. */
  mode?: FeatureStepMode;
  /** Artifact names from earlier steps this step consumes. */
  inputs?: string[];
  /** Tool whitelist for this step; absent leaves the feature's own set. */
  tools?: string[];
  /**
   * Ceiling for the model's own parallel dispatch — a cap, never a plan. The
   * model decides how to split the step and how many subagents to run; this only
   * bounds how many may run at once, which is why a value here starts nothing.
   */
  max_parallel?: number | null;
  output: FeatureStepOutput;
  prompt: string;
  gate?: FeatureStepGate;
  /** Whether a person may edit the artifacts at this step's gate. */
  allow_edit?: boolean;
  on_failure?: FeatureStepOnFailure;
  /** Subagent type the step runs as; absent runs it as the feature's own agent. */
  agent_role?: string;
}

/** Run-level state. Every one of these is settled by the time the API answers. */
export type FeatureRunStatus =
  | "running"
  | "awaiting_gate"
  | "succeeded"
  | "failed"
  | "escalated";

export type FeatureStepStatus =
  | "pending"
  | "running"
  | "succeeded"
  | "failed"
  | "escalated"
  | "voided";

/** One artifact as a run reports it: its schema plus the value it holds. */
export interface FeatureRunArtifact {
  name: string;
  schema: string;
  value: unknown;
}

/**
 * One subagent a step dispatched, as the run recorded it (design 7.8).
 *
 * There is no ``depends_on`` and no output variable here, and that is deliberate:
 * the dispatch channel the model has takes only a subagent type and a task text,
 * so a dependency it never uttered would be invented. Sequencing is recorded as
 * what really happened instead — ``ordinal``, the time window, and ``slots``, the
 * number of dispatches that were running when this one started (overlapping
 * windows ran in parallel; a dispatch that started after another ended waited).
 */
export interface FeatureStepDispatch {
  /** Position this dispatch has in the record as the run reads it back. */
  ordinal: number;
  /** The subagent type the model passed, verbatim. */
  role: string;
  /** The task text the model gave that subagent — what it actually got. */
  task: string;
  status: "succeeded" | "failed";
  error: string | null;
  /** The subagent's answer; ``null`` when the run recorded none. */
  result: string | null;
  /** ``result`` was cut at the recorded cap — it is not the whole answer. */
  truncated: boolean;
  /** Epoch **seconds**. */
  started_at: number;
  /** Epoch **seconds**; ``null`` while the dispatch has not ended. */
  ended_at: number | null;
  /** This call found no free slot and had to queue for one. */
  waited: boolean;
  /** How long it queued for a slot; ``0`` on a call that still had to wait. */
  waited_ms: number;
  /** Dispatches running at the moment this one started. */
  slots: number;
}

/**
 * What one step dispatched, and the ceiling that bounded it (design 7.7/7.8).
 *
 * The split is the model's — the platform does not choose how many subagents run,
 * it caps them and records what happened. ``declared`` / ``ceiling`` / ``peak`` /
 * ``waited`` are that record: the step's own declaration, the cap actually
 * enforced, how many ran at once, and how many dispatches were held back by the
 * cap. A step that never orchestrated and ran no named subagent carries no record
 * at all (``null``), which is why the view hides the section rather than showing
 * an empty one.
 */
export interface FeatureStepDecomposition {
  /** The mode the step ran under. */
  mode: FeatureStepMode;
  /** The step's declared ``agent_role``, when it has one. */
  role: string | null;
  /** The step's own ``max_parallel``; ``null`` when the step declared none. */
  declared: number | null;
  /** The ceiling enforced on this step: its own declaration, or the platform's default. */
  ceiling: number;
  /** Subagents that really ran at once. */
  peak: number;
  /** Dispatches that had to wait for a free slot — what the ceiling actually held back. */
  waited: number;
  dispatches: FeatureStepDispatch[];
}

/** One step as a run reports it. */
export interface FeatureRunStep {
  id: string;
  name: string;
  seq: number;
  status: FeatureStepStatus;
  gate: FeatureStepGate;
  on_failure: FeatureStepOnFailure;
  mode: FeatureStepMode;
  artifacts: FeatureRunArtifact[];
  /**
   * What this step dispatched, or ``null`` when it neither orchestrated nor ran
   * as a named subagent — ``null`` is the absence of a record, not an empty one.
   */
  decomposition?: FeatureStepDecomposition | null;
  /** Epoch **seconds**; ``null`` while the step has not reached that point. */
  started_at: number | null;
  ended_at: number | null;
  error: string | null;
  attempts: number;
  /** A rewind discarded this step's result; it will be produced again. */
  voided: boolean;
}

/**
 * The gate the run is stopped at.
 *
 * ``allow_edit`` is the step's own declaration: when false, the artifacts are
 * shown but the server refuses an ``edits`` body.
 */
export interface FeaturePendingGate {
  step_id: string;
  name: string;
  gate: FeatureStepGate;
  allow_edit: boolean;
  artifacts: FeatureRunArtifact[];
}

/** ``GET /features/{id}/runs/{task_id}`` — the step-level state of one run. */
export interface FeatureRunState {
  task_id: string;
  feature_id: string;
  status: FeatureRunStatus;
  /** Id of the step the run is at; ``null`` before it starts one. */
  current_step: string | null;
  steps: FeatureRunStep[];
  pending_gate: FeaturePendingGate | null;
}

/**
 * What ``POST /features/{id}/run`` answers for a definition that declares steps:
 * the run state itself, plus whatever text the run has produced so far. A run
 * stopped at a gate carries ``output: null`` — nothing is delivered yet.
 */
export interface FeatureStepRun extends FeatureRunState {
  output: string | null;
  output_kind: FeatureOutputKind;
}

/** Edits injected at a gate or a rewind: ``{artifact name: new value}``. */
export type FeatureArtifactEdits = Record<string, unknown>;

/**
 * One write a person made to an artifact, as the audit reports it.
 *
 * ``kind`` is what separates the two things a rewind leaves behind: the
 * correction that was injected (``edit``, before → after) and the value that was
 * discarded to make room for it (``void``, before → ``null``). Both stay in the
 * trail, which is what makes "who changed what" answerable after a rerun.
 */
export interface FeatureHumanEdit {
  artifact: string;
  kind: "edit" | "void";
  /** Raw JSON values; ``null`` when the artifact did not exist before. */
  before: unknown;
  after: unknown;
  by_user_id: number | null;
  /** Epoch **seconds** (run tables store seconds; see ``epochMillis``). */
  at: number;
  source: "approve" | "rewind";
}

/** One step of the audit trail: what went in, what came out, who changed what. */
export interface FeatureRunAuditStep {
  id: string;
  name: string;
  seq: number;
  status: FeatureStepStatus;
  gate: FeatureStepGate;
  on_failure: FeatureStepOnFailure;
  mode: FeatureStepMode;
  inputs: FeatureRunArtifact[];
  artifacts: FeatureRunArtifact[];
  /** Same record the run reports; the audit reads it from the same response field. */
  decomposition?: FeatureStepDecomposition | null;
  human_edits: FeatureHumanEdit[];
  error: string | null;
  started_at: number | null;
  ended_at: number | null;
  attempts: number;
  voided: boolean;
}

/** ``GET /features/{id}/runs/{task_id}/audit`` — the run plus what a person changed. */
export interface FeatureRunAudit {
  task_id: string;
  feature_id: string;
  /** Where the run stands now, so the trail is read in the state it left. */
  status: FeatureRunStatus;
  /** The configuration the run started under (design 4). */
  snapshot: Record<string, unknown>;
  steps: FeatureRunAuditStep[];
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
    request<FeatureRunResponse | FeatureStepRun>(
      `/features/${encodeURIComponent(id)}/run`,
      {
        method: "POST",
        body: JSON.stringify({ inputs }),
      },
    ),
  /**
   * Step-level state of one run. A run that stopped at a gate, failed or was
   * rewound answers with what is true *now* — nothing here is inferred.
   */
  getFeatureRun: (id: string, taskId: string) =>
    request<FeatureStepRun>(
      `/features/${encodeURIComponent(id)}/runs/${encodeURIComponent(taskId)}`,
    ),
  /**
   * Release the gate the run is waiting at, continuing the *same* run. ``edits``
   * may only name the gated step's own artifacts, and only when that step
   * declares ``allow_edit``; anything else is refused by the server.
   */
  approveFeatureRun: (
    id: string,
    taskId: string,
    edits?: FeatureArtifactEdits,
  ) =>
    request<FeatureStepRun>(
      `/features/${encodeURIComponent(id)}/runs/${encodeURIComponent(taskId)}/approve`,
      {
        method: "POST",
        body: JSON.stringify(edits ? { edits } : {}),
      },
    ),
  /**
   * Go back to step ``toStep`` and run on from there, voiding everything it
   * produced. Passing ``edits`` makes it the *rerun with fixes*: the artifacts
   * named there are injected, and the server records before/after in the audit.
   */
  rewindFeatureRun: (
    id: string,
    taskId: string,
    toStep: string,
    edits?: FeatureArtifactEdits,
  ) =>
    request<FeatureStepRun>(
      `/features/${encodeURIComponent(id)}/runs/${encodeURIComponent(taskId)}/rewind`,
      {
        method: "POST",
        body: JSON.stringify(edits ? { to_step: toStep, edits } : { to_step: toStep }),
      },
    ),
  /** What each step took in and produced, and every human edit to an artifact. */
  getFeatureRunAudit: (id: string, taskId: string) =>
    request<FeatureRunAudit>(
      `/features/${encodeURIComponent(id)}/runs/${encodeURIComponent(taskId)}/audit`,
    ),
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
