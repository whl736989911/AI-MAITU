/**
 * Definition ⇄ form-value mapping for the feature settings editor.
 *
 * The editor holds one row per input field, each carrying its own schema node,
 * and rebuilds the whole document on save: ``input_schema.properties``,
 * ``input_schema.required`` and ``ui_schema.order`` are all derived from the
 * row order in one pass. Deriving them together is the point — an ``order``
 * that outlives the properties it names renders the run form in the wrong
 * sequence, and nothing else in the stack would notice.
 *
 * Everything here is pure: no React, no API calls, so the mapping can be
 * exercised directly (see ``featureSettings.test.ts``).
 */

import type {
  Feature,
  FeatureAgent,
  FeatureDefinitionBody,
  FeatureFieldSchema,
  FeatureFieldType,
  FeatureInputSchema,
  FeatureMeta,
  FeatureOutputKind,
  FeaturePermissionsBody,
  FeatureStep,
  FeatureStepGate,
  FeatureStepMode,
  FeatureStepOnFailure,
  FeatureUiSchema,
} from "../../../api/modules/features";

/** Types the picker offers, in display order. */
export const FEATURE_FIELD_TYPES: FeatureFieldType[] = [
  "string",
  "number",
  "integer",
  "boolean",
  "array",
];

/** ``input_schema.format`` values the catalog accepts. */
export const FEATURE_FIELD_FORMATS = ["textarea", "date", "email"] as const;
export type FeatureFieldFormat = (typeof FEATURE_FIELD_FORMATS)[number];

/**
 * How deep ``items`` may nest. The run form renders array → array → scalar as a
 * grid of cells and has no widget below that, so the editor stops there too.
 */
export const MAX_SCHEMA_DEPTH = 2;

/** Wildcard unit key: every organization unit. */
export const ALL_UNITS_KEY = "*";

/** Role keys the picker suggests; anything else can still be typed in. */
export const FEATURE_ROLE_OPTIONS = ["member", "admin"] as const;

/** Template a brand-new feature starts from — the placeholders are the contract. */
export const DEFAULT_USER_TEMPLATE = "{{inputs}}";

/** What the run form shows for a feature with no system prompt. */
export const EMPTY_SYSTEM_PROMPT = "";

/** One row of the field editor. */
export interface FeatureFieldRow {
  /** Property name — the key the prompt receives under ``{{inputs_json}}``. */
  name: string;
  required: boolean;
  schema: FeatureFieldSchema;
}

/** The settings form's values, flattened for antd's ``Form``. */
export interface FeatureFormValues {
  id: string;
  labelZh: string;
  labelEn: string;
  descriptionZh: string;
  descriptionEn: string;
  iconName: string;
  /** Empty string means "no colour" (the card falls back to the brand accent). */
  color: string;
  unit: string;
  outputKind: FeatureOutputKind;
  systemPrompt: string;
  userTemplate: string;
  allowUnits: string[];
  allowRoles: string[];
  fields: FeatureFieldRow[];
  /**
   * Capability layer, as the shared expert fields bind it.
   *
   * ``agentModel`` uses the expert picker's sentinel (``""`` = inherit the
   * caller's agent, the expert's "auto"), the runtime knobs are ``null`` when
   * unset, and each scope list is ``undefined`` when the feature declares
   * nothing — which is a different fact from ``[]``, "explicitly none".
   */
  agentModel: string;
  max_iters: number | null;
  max_input_length: number | null;
  temperature: number | null;
  top_p: number | null;
  max_tokens: number | null;
  knowledge_base_ids?: string[];
  mcp_servers?: string[];
  toolsDisabled?: string[];
  skills?: string[];
  subagents?: string[];
  /**
   * The run's step skeleton, in order (design 7.10). Held as the whole step
   * object: the editor binds the fields it owns and a save writes back what it
   * did not touch, so a definition never loses a key this build cannot name.
   */
  steps: FeatureStep[];
}

/** The scope lists of the capability block, as ``feature.json`` names them. */
export const FEATURE_SCOPE_KEYS = [
  "skills",
  "subagents",
  "mcp_servers",
  "knowledge_base_ids",
] as const;
export type FeatureScopeKey = (typeof FEATURE_SCOPE_KEYS)[number];

/** Runtime knobs the capability block shares with the expert drawers. */
export const FEATURE_RUNTIME_KEYS = [
  "max_iters",
  "max_input_length",
  "temperature",
  "top_p",
  "max_tokens",
] as const;

/** Bilingual copy that carries nothing but whitespace is not copy. */
function hasCopy(text: { zh?: string; en?: string } | undefined): boolean {
  if (!text) return false;
  return Boolean((text.zh ?? "").trim() || (text.en ?? "").trim());
}

/** Read a string list out of a loosely-typed bag (``permissions``). */
function stringList(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : [];
}

/** Drop blanks and duplicates from a user-edited list, keeping its order. */
function cleanList(values: readonly string[] | undefined): string[] {
  const out: string[] = [];
  for (const value of values ?? []) {
    const trimmed = value.trim();
    if (trimmed && !out.includes(trimmed)) out.push(trimmed);
  }
  return out;
}

/**
 * Feature ids become directory names under the writable feature root, and the
 * backend store accepts exactly this shape (``store._ID_RE``): a lower-case slug
 * that cannot carry a separator, a dot or a drive letter. The editor holds the
 * same line so a typo is caught before the round trip.
 */
const FEATURE_ID_PATTERN = /^[a-z0-9][a-z0-9_-]{0,63}$/;

export function isValidFeatureId(id: string): boolean {
  return FEATURE_ID_PATTERN.test(id);
}

/** Types offered at a nesting level: scalars at the deepest one (see ``MAX_SCHEMA_DEPTH``). */
export function fieldTypesForDepth(depth: number): FeatureFieldType[] {
  return depth >= MAX_SCHEMA_DEPTH
    ? FEATURE_FIELD_TYPES.filter((type) => type !== "array")
    : FEATURE_FIELD_TYPES;
}

/**
 * Keep only what the catalog accepts for this node: bilingual copy, ``items``
 * on arrays, ``format``/``enum`` on strings, and no empty lists.
 */
export function normalizeSchema(schema: FeatureFieldSchema): FeatureFieldSchema {
  const next: FeatureFieldSchema = { type: schema.type };
  if (hasCopy(schema.title)) next.title = schema.title;
  if (hasCopy(schema.description)) next.description = schema.description;
  if (schema.type === "string") {
    if (schema.format) next.format = schema.format;
    const values = cleanList(schema.enum);
    if (values.length > 0) next.enum = values;
  }
  if (schema.type === "array") {
    // ``items`` is mandatory on an array, and the editor shows a plain text
    // element when it is missing — the written document has to agree with it.
    next.items = normalizeSchema(schema.items ?? emptySchemaNode());
  }
  return next;
}

/**
 * Rows whose bilingual copy is half-filled. The catalog requires *both* locales
 * whenever a field carries a title or description, so half a pair cannot be
 * written; naming the rows here keeps that off the server's refusal list.
 */
export function incompleteCopyFields(fields: FeatureFieldRow[]): string[] {
  const halfFilled = (text: { zh?: string; en?: string } | undefined) =>
    Boolean((text?.zh ?? "").trim()) !== Boolean((text?.en ?? "").trim());
  return fields
    .filter(
      (row) => halfFilled(row.schema.title) || halfFilled(row.schema.description),
    )
    .map((row) => row.name.trim());
}

/** Carry bilingual copy across a change of the type-specific keys. */
function withCopy(
  schema: FeatureFieldSchema,
  next: FeatureFieldSchema,
): FeatureFieldSchema {
  return {
    ...next,
    ...(schema.title ? { title: schema.title } : {}),
    ...(schema.description ? { description: schema.description } : {}),
  };
}

/** A new schema node — a plain single-line text input unless told otherwise. */
export function emptySchemaNode(
  type: FeatureFieldType = "string",
): FeatureFieldSchema {
  return { type };
}

/** A new field row: unnamed, optional, plain text. */
export function emptyFieldRow(): FeatureFieldRow {
  return { name: "", required: false, schema: emptySchemaNode() };
}

/**
 * Rebuild a node for a new type. Switching a scalar to ``array`` demotes it to
 * the element type — "string → array" is how one writes "array of string" —
 * and a type change never carries the old type's keys along.
 */
export function schemaForType(
  schema: FeatureFieldSchema,
  type: FeatureFieldType,
): FeatureFieldSchema {
  if (type === schema.type) return schema;
  if (type === "array") {
    return withCopy(schema, {
      type,
      items: schema.items ?? emptySchemaNode(schema.type),
    });
  }
  return withCopy(schema, { type });
}

/** ``ui_schema.order`` first (that is what the run form renders by), then the rest. */
function orderedPropertyNames(
  schema: FeatureInputSchema,
  uiSchema: FeatureUiSchema | undefined,
): string[] {
  const names: string[] = [];
  for (const name of [...(uiSchema?.order ?? []), ...Object.keys(schema.properties)]) {
    if (name in schema.properties && !names.includes(name)) names.push(name);
  }
  return names;
}

/** Property schema → one editor row per property, in render order. */
function fieldRowsFromSchema(
  schema: FeatureInputSchema,
  uiSchema: FeatureUiSchema | undefined,
): FeatureFieldRow[] {
  const required = new Set(schema.required ?? []);
  return orderedPropertyNames(schema, uiSchema).map((name) => ({
    name,
    required: required.has(name),
    schema: normalizeSchema(schema.properties[name]),
  }));
}

/** A declared list, or ``undefined`` when the definition inherits instead. */
function declaredList(value: unknown): string[] | undefined {
  return Array.isArray(value)
    ? value.filter((item): item is string => typeof item === "string")
    : undefined;
}

/**
 * The capability layer of a loaded definition, flattened.
 *
 * ``null``/absent stays ``undefined``: a feature that declares no scope must
 * load as "inherit", never as "explicitly none".
 */
function capabilityFormValues(
  agent: FeatureAgent | null,
): Pick<
  FeatureFormValues,
  | "agentModel"
  | "max_iters"
  | "max_input_length"
  | "temperature"
  | "top_p"
  | "max_tokens"
  | "knowledge_base_ids"
  | "mcp_servers"
  | "toolsDisabled"
  | "skills"
  | "subagents"
> {
  const numberOrNull = (value: unknown): number | null =>
    typeof value === "number" ? value : null;
  return {
    // ``""`` is the expert picker's "auto": here it means "inherit the agent".
    agentModel: typeof agent?.model === "string" ? agent.model : "",
    max_iters: numberOrNull(agent?.max_iters),
    max_input_length: numberOrNull(agent?.max_input_length),
    temperature: numberOrNull(agent?.temperature),
    top_p: numberOrNull(agent?.top_p),
    max_tokens: numberOrNull(agent?.max_tokens),
    knowledge_base_ids: declaredList(agent?.knowledge_base_ids),
    mcp_servers: declaredList(agent?.mcp_servers),
    toolsDisabled: declaredList(agent?.tools_disabled),
    skills: declaredList(agent?.skills),
    subagents: declaredList(agent?.subagents),
  };
}

/**
 * The ``agent`` node to write, or ``null`` when nothing is declared.
 *
 * A cleared list is *omitted* rather than written as ``[]``: those are the two
 * halves of the contract (inherit vs none), and collapsing one into the other
 * would silently widen or narrow every run of this feature.
 */
export function capabilityFromFormValues(
  values: FeatureFormValues,
): FeatureAgent | null {
  const agent: FeatureAgent = {};
  const model = values.agentModel.trim();
  if (model) agent.model = model;
  for (const key of FEATURE_RUNTIME_KEYS) {
    const value = values[key];
    if (typeof value === "number") agent[key] = value;
  }
  if (values.toolsDisabled !== undefined) {
    agent.tools_disabled = cleanList(values.toolsDisabled);
  }
  for (const key of FEATURE_SCOPE_KEYS) {
    const list = values[key];
    if (list !== undefined) agent[key] = cleanList(list);
  }
  return Object.keys(agent).length > 0 ? agent : null;
}

/* ── Task steps (design 7.10) ────────────────────────────────────────────── */

/**
 * Step ids as the server matches them (the store's ``_STEP_ID_RE``): a rewind
 * names a step by its id, so the editor holds the same line here instead of
 * letting the round trip discover it.
 */
const STEP_ID_PATTERN = /^[a-z][a-z0-9_]*$/;

export function isValidStepId(id: string): boolean {
  return STEP_ID_PATTERN.test(id);
}

/** Gates a step may declare, in the order the picker offers them. */
export const FEATURE_STEP_GATES: FeatureStepGate[] = [
  "auto",
  "confirm",
  "validate",
];

/**
 * Modes a step may declare: ``agent`` runs the step with one agent, and
 * ``orchestrate`` has the step's agent decompose the step and dispatch
 * subagents itself. Both are offered, because both are what a run can do.
 */
export const FEATURE_STEP_MODES: FeatureStepMode[] = ["agent", "orchestrate"];

/** What a failed step may do, in the order the picker offers them. */
export const FEATURE_STEP_FAILURES: FeatureStepOnFailure[] = [
  "abort",
  "escalate",
  "retry",
];

/**
 * A new step: one agent, no gate, no fixed tools, and a failure stops the run
 * instead of guessing. Every one of these is a control the author can see and
 * change before saving.
 */
export function emptyStep(): FeatureStep {
  return {
    id: "",
    name: "",
    mode: "agent",
    inputs: [],
    output: { name: "", schema: "" },
    prompt: "",
    gate: "auto",
    on_failure: "abort",
  };
}

/**
 * A loaded step, copied for the form.
 *
 * The copy is shallow-but-complete: keys this editor knows are cloned so an edit
 * cannot reach the definition object behind it, and keys it does *not* know ride
 * along untouched — a definition is never rewritten into what this build
 * happens to understand.
 */
export function stepToFormValue(step: FeatureStep): FeatureStep {
  return {
    ...step,
    inputs: [...(step.inputs ?? [])],
    ...(step.tools ? { tools: [...step.tools] } : {}),
    output: {
      name: step.output?.name ?? "",
      schema: step.output?.schema ?? "",
    },
  };
}

/**
 * The steps to write.
 *
 * Omitted vs declared is a real difference in this schema, so nothing is
 * collapsed: ``inputs`` is always written (empty means "consumes nothing"),
 * ``tools`` only when the author declared a list at all (absent inherits the
 * run's tool surface, while ``[]`` means this step may use none — two different
 * runs), and ``allow_edit`` is always written for a gate that can stop for a
 * person (the server requires it there) while an automatic gate keeps whatever a
 * definition already declared rather than having it deleted by this editor.
 */
export function stepsFromFormValues(
  steps: readonly FeatureStep[] | undefined,
): FeatureStep[] {
  return (steps ?? []).map((step) => {
    // Copies the row and then owns the keys this editor manages: a step key the
    // editor cannot name (written by a newer build, or by hand) survives the
    // round trip instead of being deleted by a save that never saw it.
    const next: FeatureStep = {
      ...step,
      id: step.id.trim(),
      name: step.name.trim(),
      inputs: cleanList(step.inputs),
      output: {
        name: (step.output?.name ?? "").trim(),
        schema: (step.output?.schema ?? "").trim(),
      },
      prompt: step.prompt.trim(),
    };
    if (!step.mode) delete next.mode;
    if (!step.gate) delete next.gate;
    if (!step.on_failure) delete next.on_failure;
    if (
      step.gate === "confirm" ||
      step.gate === "validate" ||
      step.allow_edit !== undefined
    ) {
      next.allow_edit = step.allow_edit === true;
    } else {
      delete next.allow_edit;
    }
    if (step.tools === undefined) delete next.tools;
    else next.tools = cleanList(step.tools);
    if (typeof step.max_parallel !== "number") delete next.max_parallel;
    const role = (step.agent_role ?? "").trim();
    if (role) next.agent_role = role;
    else delete next.agent_role;
    return next;
  });
}

/**
 * Checks the definition format refuses at write time, beyond ''is it filled in''.
 *
 * A ``validate`` gate releases only an object artifact that carries a boolean
 * ``passed``; naming the steps that break it keeps a refusal the server would
 * make next to the fields that caused it.
 *
 * What is *not* here: ``mode`` and ``agent_role``. How a step runs is the
 * author's call and the definition is stored as written — this function names
 * only what the format refuses outright.
 */
export function validateGateProblems(steps: readonly FeatureStep[]): string[] {
  return steps
    .filter((step) => step.gate === "validate" && step.output?.schema !== "object")
    .map((step) => step.id.trim() || step.name.trim());
}

/** Step ids used by more than one step — a rewind target has to be unambiguous. */
export function duplicateStepIds(steps: readonly FeatureStep[]): string[] {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const step of steps) {
    const id = step.id.trim();
    if (!id) continue;
    if (seen.has(id)) duplicates.add(id);
    else seen.add(id);
  }
  return [...duplicates];
}

/** Artifact names the steps before ``index`` produce — what this step may consume. */
export function artifactNamesBefore(
  steps: readonly FeatureStep[],
  index: number,
): string[] {
  const names: string[] = [];
  for (const step of steps.slice(0, index)) {
    const name = step.output?.name?.trim();
    if (name && !names.includes(name)) names.push(name);
  }
  return names;
}

/** A loaded definition, flattened into the settings form. */
export function featureToFormValues(feature: Feature): FeatureFormValues {
  return {
    id: feature.id,
    labelZh: feature.label.zh,
    labelEn: feature.label.en,
    descriptionZh: feature.description.zh,
    descriptionEn: feature.description.en,
    iconName: feature.icon_name,
    color: feature.color ?? "",
    unit: feature.unit,
    outputKind: feature.output_kind,
    systemPrompt: feature.system_prompt ?? EMPTY_SYSTEM_PROMPT,
    userTemplate: feature.user_template,
    allowUnits: stringList(feature.permissions?.allow_units),
    allowRoles: stringList(feature.permissions?.allow_roles),
    fields: fieldRowsFromSchema(feature.input_schema, feature.ui_schema),
    ...capabilityFormValues(feature.agent),
    steps: (feature.steps ?? []).map(stepToFormValue),
  };
}

/** Blank form for a new feature: no fields, no restrictions, one text input. */
export function emptyFormValues(meta: FeatureMeta): FeatureFormValues {
  return {
    id: "",
    labelZh: "",
    labelEn: "",
    descriptionZh: "",
    descriptionEn: "",
    iconName: meta.icons[0] ?? "",
    color: "",
    unit: meta.units[0] ?? "",
    outputKind: meta.output_kinds[0] ?? "markdown",
    systemPrompt: EMPTY_SYSTEM_PROMPT,
    userTemplate: DEFAULT_USER_TEMPLATE,
    allowUnits: [],
    allowRoles: [],
    fields: [emptyFieldRow()],
    ...capabilityFormValues(null),
    // No steps: a new feature runs as one shot until its author adds a skeleton.
    steps: [],
  };
}

/** Names used by more than one row — ``properties`` keeps only the last of them. */
export function duplicateFieldNames(fields: FeatureFieldRow[]): string[] {
  const seen = new Set<string>();
  const duplicates = new Set<string>();
  for (const row of fields) {
    const name = row.name.trim();
    if (!name) continue;
    if (seen.has(name)) duplicates.add(name);
    else seen.add(name);
  }
  return [...duplicates];
}

/**
 * The document to write.
 *
 * ``existing`` is the definition that was loaded for editing (``null`` when
 * creating): parts the editor does not own — a root title/description, per-field
 * ``ui_schema.widgets`` overrides — are carried over for the fields that survive,
 * while everything derived from the rows is rebuilt from them.
 */
export function formValuesToDefinition(
  values: FeatureFormValues,
  existing: Feature | null,
): FeatureDefinitionBody {
  const fields = values.fields.map((row) => ({
    name: row.name.trim(),
    required: row.required,
    schema: normalizeSchema(row.schema),
  }));

  const inputSchema: FeatureInputSchema = {
    type: "object",
    properties: Object.fromEntries(fields.map((row) => [row.name, row.schema])),
  };
  if (existing?.input_schema.title) {
    inputSchema.title = existing.input_schema.title;
  }
  if (existing?.input_schema.description) {
    inputSchema.description = existing.input_schema.description;
  }
  const required = fields.filter((row) => row.required).map((row) => row.name);
  if (required.length > 0) inputSchema.required = required;

  // Widget overrides are not editable here, but a feature.json may declare them:
  // keep the surviving ones so a save never silently changes how a field renders.
  const widgets: Record<string, string> = {};
  for (const row of fields) {
    const widget = existing?.ui_schema.widgets?.[row.name];
    if (widget) widgets[row.name] = widget;
  }
  const uiSchema: FeatureUiSchema = { order: fields.map((row) => row.name) };
  if (Object.keys(widgets).length > 0) uiSchema.widgets = widgets;

  const permissions: FeaturePermissionsBody = {};
  const units = cleanList(values.allowUnits);
  const roles = cleanList(values.allowRoles);
  if (units.length > 0) permissions.allow_units = units;
  if (roles.length > 0) permissions.allow_roles = roles;

  const systemPrompt = values.systemPrompt.trim();
  const steps = stepsFromFormValues(values.steps);
  const body: FeatureDefinitionBody = {
    id: values.id.trim(),
    label: { zh: values.labelZh.trim(), en: values.labelEn.trim() },
    description: {
      zh: values.descriptionZh.trim(),
      en: values.descriptionEn.trim(),
    },
    icon_name: values.iconName.trim(),
    color: values.color.trim() || null,
    unit: values.unit.trim(),
    input_schema: inputSchema,
    ui_schema: uiSchema,
    prompt: {
      user_template: values.userTemplate.trim(),
      system_prompt: systemPrompt || null,
    },
    output: { kind: values.outputKind },
    permissions,
    agent: capabilityFromFormValues(values),
  };
  // No step at all writes no key: "single-shot" is what the absence already
  // means, and an empty list would only claim the editor had looked at it.
  if (steps.length > 0) body.steps = steps;
  return body;
}
