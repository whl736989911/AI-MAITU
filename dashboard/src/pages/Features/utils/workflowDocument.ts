/**
 * The workflow document, as the editor holds it — and the checks it must pass.
 *
 * The definition is a document, not a form's fields, so the editor keeps *the
 * document* in state and every section edits a part of it: what the JSON tab shows
 * is what saving writes, and the other way round. That is why the document's own
 * order is never rebuilt (``properties`` by insertion, steps by array), why a
 * half-filled optional part is dropped on the way out rather than sent as
 * ``""``/``[]``, and why nothing here renames a key for prettiness.
 *
 * ``validateWorkflowDocument`` mirrors ``octop/infra/agents/feature_workflow.py``:
 * the same rules, the same order, the same wording — so a definition the editor
 * refuses and one the server refuses read identically, and an author who fixes what
 * the editor listed will not meet a second list from the server. The server is
 * still the authority (it validates again and reports every problem at once); this
 * exists so a save that cannot succeed never leaves the browser.
 */

import {
  WORKFLOW_INPUT_TYPES,
  WORKFLOW_ITEM_TYPES,
  WORKFLOW_OUTPUT_FORMS,
  WORKFLOW_STATUSES,
  WORKFLOW_STEP_GATES,
  WORKFLOW_STRING_FORMATS,
  WORKFLOW_VERSION,
  type FeatureWorkflow,
  type LocalizedText,
  type WorkflowInputField,
  type WorkflowInputs,
  type WorkflowOutput,
  type WorkflowStep,
} from "../../../api/modules/featureWorkflow";

/** The caps the server enforces; the editor stops an author before them. */
export const MAX_STEPS = 24;
export const MAX_RULES = 20;
export const MAX_INPUT_FIELDS = 40;
export const MAX_PROMPT_CHARS = 8000;
export const MAX_NAME_CHARS = 120;
export const MAX_RULE_CHARS = 500;
export const MAX_ACCEPT_CHARS = 200;

/** ``^[a-z][a-z0-9_]{0,63}$`` — a step id has to survive being referenced. */
export const STEP_ID_PATTERN = /^[a-z][a-z0-9_]{0,63}$/;

const DOCUMENT_KEYS: Record<string, true> = {
  version: true,
  status: true,
  inputs: true,
  steps: true,
  outputs: true,
  rules: true,
};
const INPUT_ROOT_KEYS: Record<string, true> = {
  type: true,
  properties: true,
  required: true,
};
const INPUT_FIELD_KEYS: Record<string, true> = {
  type: true,
  title: true,
  description: true,
  format: true,
  enum: true,
  items: true,
  accept: true,
  multiple: true,
};
const STEP_KEYS: Record<string, true> = {
  id: true,
  name: true,
  prompt: true,
  depends_on: true,
  skills: true,
  subagents: true,
  tools: true,
  gate: true,
};
const OUTPUT_KEYS: Record<string, true> = {
  name: true,
  form: true,
  path: true,
  description: true,
};

/**
 * Membership in a literal table of keys, own keys only.
 *
 * The tables are literal objects rather than ``Set``s, so the check has to say
 * "own": a document may legitimately carry a key named ``constructor``, and the
 * inherited one must not read as a key the format knows.
 */
function hasOwn(table: object, key: string): boolean {
  return Object.prototype.hasOwnProperty.call(table, key);
}

/** The name lists a step may carry, in the order the server checks them. */
const STEP_NAME_LIST_KEYS = [
  "skills",
  "subagents",
  "tools",
  "depends_on",
] as const;

/**
 * A document with nothing in it yet: the version it declares, draft, no steps.
 *
 * ``steps`` is there from the start because the format requires the key itself —
 * an empty flow and no flow are not the same document, and a definition missing it
 * is refused rather than read as "no steps yet".
 */
export function emptyWorkflow(): FeatureWorkflow {
  return { version: WORKFLOW_VERSION, status: "draft", steps: [] };
}

function isMapping(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isFilledString(value: unknown): value is string {
  return typeof value === "string" && value.trim() !== "";
}

/** Whether a value is a ``{zh, en}`` pair with both halves filled. */
function isLocalizedText(value: unknown): value is LocalizedText {
  return (
    isMapping(value) && isFilledString(value.zh) && isFilledString(value.en)
  );
}

/**
 * A legal step id derived from a step name — the server's own rule, so an id the
 * editor invents and one an assistant invents are the same id.
 *
 * A name with nothing ASCII in it (a Chinese step name, the common case here)
 * carries nothing to build from, so *index* names it instead.
 */
export function slugifyStepId(name: string, index?: number): string {
  const chars: string[] = [];
  for (const char of name.trim().toLowerCase()) {
    if (/[a-z0-9]/.test(char)) chars.push(char);
    else if (chars.length > 0 && chars[chars.length - 1] !== "_")
      chars.push("_");
  }
  let slug = chars
    .join("")
    .replace(/^_+|_+$/g, "")
    .slice(0, 64);
  if (slug === "") return index === undefined ? "step" : `step_${index}`;
  if (!/^[a-z]/.test(slug)) slug = `step_${slug}`.slice(0, 64);
  return slug;
}

/** A step id no other step holds — a duplicate would make ``depends_on`` lie. */
export function uniqueStepId(base: string, used: ReadonlySet<string>): string {
  let candidate = base;
  let suffix = 2;
  while (used.has(candidate)) {
    candidate = `${base.slice(0, 60)}_${suffix}`;
    suffix += 1;
  }
  return candidate;
}

/** Move one entry of an array — for the arrays whose order *is* their meaning. */
export function moveItem<T>(
  items: readonly T[],
  from: number,
  to: number,
): T[] {
  const next = [...items];
  const [moved] = next.splice(from, 1);
  if (moved === undefined) return next;
  next.splice(to, 0, moved);
  return next;
}

/** Rename one form field, keeping the declaration order the form renders in. */
export function renameInputField(
  properties: Record<string, WorkflowInputField>,
  from: string,
  to: string,
): Record<string, WorkflowInputField> {
  const next: Record<string, WorkflowInputField> = {};
  for (const [name, field] of Object.entries(properties)) {
    next[name === from ? to : name] = field;
  }
  return next;
}

/** A bilingual pair with the empty halves dropped, or nothing if both are empty. */
function trimText(text: LocalizedText | undefined): LocalizedText | undefined {
  if (!text) return undefined;
  const zh = text.zh.trim();
  const en = text.en.trim();
  if (zh === "" && en === "") return undefined;
  return { zh, en };
}

/** The strings of a name list, blanks dropped. */
function trimList(values: readonly string[] | undefined): string[] {
  return (values ?? [])
    .map((value) => value.trim())
    .filter((value) => value !== "");
}

function normalizeField(field: WorkflowInputField): WorkflowInputField {
  const out: WorkflowInputField = {
    type: field.type,
    title: { zh: field.title.zh.trim(), en: field.title.en.trim() },
  };
  const description = trimText(field.description);
  if (description) out.description = description;
  if (field.type === "string") {
    if (field.format) out.format = field.format;
    if (field.enum && field.enum.length > 0) out.enum = trimList(field.enum);
  }
  if (field.type === "array" && field.items)
    out.items = { type: field.items.type };
  if (field.type === "file") {
    const accept = field.accept?.trim() ?? "";
    if (accept !== "") out.accept = accept;
    if (field.multiple) out.multiple = true;
  }
  return out;
}

function normalizeStep(step: WorkflowStep): WorkflowStep {
  const out: WorkflowStep = { name: step.name.trim() };
  const id = step.id?.trim() ?? "";
  if (id !== "") out.id = id;
  const prompt = step.prompt?.trim() ?? "";
  if (prompt !== "") out.prompt = prompt;
  for (const key of ["depends_on", "skills", "subagents", "tools"] as const) {
    const values = trimList(step[key]);
    if (values.length > 0) out[key] = values;
  }
  if (step.gate === "confirm") out.gate = "confirm";
  return out;
}

function normalizeOutput(output: WorkflowOutput): WorkflowOutput {
  const out: WorkflowOutput = { name: output.name.trim(), form: output.form };
  const path = output.path?.trim() ?? "";
  if (path !== "") out.path = path;
  const description = trimText(output.description);
  if (description) out.description = description;
  return out;
}

function normalizeInputs(
  inputs: WorkflowInputs | undefined,
): WorkflowInputs | undefined {
  if (!inputs) return undefined;
  const properties: Record<string, WorkflowInputField> = {};
  // The names are kept exactly as the author wrote them — a field *is* its name,
  // and one this editor trimmed or dropped would be a field the run form no longer
  // asks for, silently.
  for (const [name, field] of Object.entries(inputs.properties ?? {})) {
    properties[name] = normalizeField(field);
  }
  if (Object.keys(properties).length === 0) return undefined;
  const required = (inputs.required ?? []).filter((name) =>
    hasOwn(properties, name),
  );
  const out: WorkflowInputs = { type: "object", properties };
  if (required.length > 0) out.required = required;
  return out;
}

/**
 * The document as it will be stored: the shape it declares, and nothing empty.
 *
 * The form holds a half-typed document — a step whose prompt is not written yet, a
 * list somebody cleared, a description only one language of which is typed — and
 * sending those as they are would be refused for reasons the author never meant to
 * state. Dropping what is empty keeps the *stored* document the one on screen, and
 * the JSON tab shows exactly this.
 *
 * The two names a document is built from are not dropped: a step's name and a
 * form field's name are kept even when blank, so an unfinished row is refused and
 * marked like anything else instead of quietly leaving the definition on save.
 */
export function normalizeWorkflow(workflow: FeatureWorkflow): FeatureWorkflow {
  const out: FeatureWorkflow = {
    version: WORKFLOW_VERSION,
    status: workflow.status === "active" ? "active" : "draft",
  };
  const inputs = normalizeInputs(workflow.inputs);
  if (inputs) out.inputs = inputs;
  // ``steps`` is kept even when it is empty: the format requires the key, so a
  // feature with no steps yet declares an empty flow rather than none at all.
  out.steps = (workflow.steps ?? []).map(normalizeStep);
  const outputs = (workflow.outputs ?? []).map(normalizeOutput);
  if (outputs.length > 0) out.outputs = outputs;
  const rules = trimList(workflow.rules);
  if (rules.length > 0) out.rules = rules;
  return out;
}

function collectTextProblem(
  node: unknown,
  where: string,
  problems: string[],
  required: boolean,
): void {
  if (node === undefined || node === null) {
    if (required) problems.push(`${where} is required`);
    return;
  }
  if (!isLocalizedText(node)) {
    problems.push(`${where} must be a non-empty {'zh': …, 'en': …} pair`);
  }
}

function collectNameListProblem(
  node: unknown,
  where: string,
  problems: string[],
): void {
  if (node === undefined || node === null) return;
  if (!Array.isArray(node) || node.some((item) => !isFilledString(item))) {
    problems.push(`${where} must be an array of non-empty strings`);
  }
}

function collectFieldProblems(
  name: string,
  spec: unknown,
  problems: string[],
): void {
  const where = `inputs.properties.${name}`;
  if (!isMapping(spec)) {
    problems.push(`${where} must be an object`);
    return;
  }
  const unknown = Object.keys(spec)
    .filter((key) => !hasOwn(INPUT_FIELD_KEYS, key))
    .sort();
  if (unknown.length > 0) {
    problems.push(`${where} has unsupported keys: ${unknown.join(", ")}`);
  }
  const type = spec.type;
  if (!WORKFLOW_INPUT_TYPES.some((candidate) => candidate === type)) {
    problems.push(
      `${where}.type must be one of ${WORKFLOW_INPUT_TYPES.join(", ")}`,
    );
    return;
  }
  collectTextProblem(spec.title, `${where}.title`, problems, true);
  collectTextProblem(spec.description, `${where}.description`, problems, false);
  if (type === "string") {
    const format = spec.format;
    if (
      format !== undefined &&
      format !== null &&
      !WORKFLOW_STRING_FORMATS.some((candidate) => candidate === format)
    ) {
      problems.push(
        `${where}.format must be one of ${WORKFLOW_STRING_FORMATS.join(", ")}`,
      );
    }
    const values = spec.enum;
    if (
      values !== undefined &&
      values !== null &&
      (!Array.isArray(values) ||
        values.length === 0 ||
        values.some((item) => !isFilledString(item)))
    ) {
      problems.push(`${where}.enum must be a non-empty array of strings`);
    }
  } else if (
    (spec.format !== undefined && spec.format !== null) ||
    (spec.enum !== undefined && spec.enum !== null)
  ) {
    problems.push(`${where}.format/.enum only apply to a string field`);
  }
  if (type === "array") {
    const items = spec.items;
    if (
      !isMapping(items) ||
      !WORKFLOW_ITEM_TYPES.some((candidate) => candidate === items.type)
    ) {
      problems.push(
        `${where}.items.type must be one of ${WORKFLOW_ITEM_TYPES.join(", ")}`,
      );
    } else if (Object.keys(items).some((key) => key !== "type")) {
      problems.push(`${where}.items supports only 'type'`);
    }
  } else if (spec.items !== undefined && spec.items !== null) {
    problems.push(`${where}.items only applies to an array field`);
  }
  if (type === "file") {
    const accept = spec.accept;
    if (
      accept !== undefined &&
      accept !== null &&
      (typeof accept !== "string" || accept.length > MAX_ACCEPT_CHARS)
    ) {
      problems.push(
        `${where}.accept must be a string of at most ${MAX_ACCEPT_CHARS} chars`,
      );
    }
    const multiple = spec.multiple;
    if (
      multiple !== undefined &&
      multiple !== null &&
      typeof multiple !== "boolean"
    ) {
      problems.push(`${where}.multiple must be a boolean`);
    }
  } else if (
    (spec.accept !== undefined && spec.accept !== null) ||
    (spec.multiple !== undefined && spec.multiple !== null)
  ) {
    problems.push(`${where}.accept/.multiple only apply to a file field`);
  }
}

function collectInputProblems(node: unknown, problems: string[]): void {
  if (node === undefined || node === null) return;
  if (!isMapping(node)) {
    problems.push("inputs must be an object");
    return;
  }
  const unknown = Object.keys(node)
    .filter((key) => !hasOwn(INPUT_ROOT_KEYS, key))
    .sort();
  if (unknown.length > 0) {
    problems.push(`inputs has unsupported keys: ${unknown.join(", ")}`);
  }
  if (node.type !== "object") problems.push("inputs.type must be 'object'");
  const properties = node.properties;
  if (!isMapping(properties) || Object.keys(properties).length === 0) {
    problems.push("inputs.properties must be a non-empty object");
    return;
  }
  if (Object.keys(properties).length > MAX_INPUT_FIELDS) {
    problems.push(`inputs may declare at most ${MAX_INPUT_FIELDS} fields`);
  }
  for (const [name, spec] of Object.entries(properties)) {
    if (!isFilledString(name)) {
      problems.push("inputs.properties keys must be non-empty strings");
      continue;
    }
    collectFieldProblems(name, spec, problems);
  }
  const required = node.required;
  if (required !== undefined && required !== null) {
    if (
      !Array.isArray(required) ||
      required.some((item) => typeof item !== "string")
    ) {
      problems.push("inputs.required must be an array of field names");
    } else {
      const missing = required.filter((item) => !hasOwn(properties, item));
      if (missing.length > 0) {
        problems.push(
          `inputs.required names unknown fields: ${missing.join(", ")}`,
        );
      }
    }
  }
}

/**
 * ``depends_on`` may only name an *earlier* step.
 *
 * Run only once nothing else is wrong — the same way the server does it: while an
 * id is still being typed, every reference to it would be reported as unknown.
 */
function collectDependencyProblems(
  steps: readonly unknown[],
  indexOf: ReadonlyMap<string, number>,
  problems: string[],
): void {
  steps.forEach((step, index) => {
    if (!isMapping(step)) return;
    const dependsOn = step.depends_on;
    if (!Array.isArray(dependsOn)) return;
    for (const ref of dependsOn) {
      if (typeof ref !== "string") continue;
      const target = indexOf.get(ref);
      if (target === undefined) {
        problems.push(`steps[${index}].depends_on names unknown step '${ref}'`);
      } else if (target >= index) {
        problems.push(
          `steps[${index}].depends_on must name an earlier step ('${ref}')`,
        );
      }
    }
  });
}

function collectStepProblems(
  node: unknown,
  problems: string[],
  active: boolean,
): void {
  if (!Array.isArray(node)) {
    problems.push("steps must be an array");
    return;
  }
  if (active && node.length === 0) {
    problems.push("steps must not be empty for an active workflow");
  }
  if (node.length > MAX_STEPS) {
    problems.push(`steps may hold at most ${MAX_STEPS} entries`);
  }
  const indexOf = new Map<string, number>();
  node.forEach((step, index) => {
    const where = `steps[${index}]`;
    if (!isMapping(step)) {
      problems.push(`${where} must be an object`);
      return;
    }
    const unknown = Object.keys(step)
      .filter((key) => !hasOwn(STEP_KEYS, key))
      .sort();
    if (unknown.length > 0) {
      problems.push(`${where} has unsupported keys: ${unknown.join(", ")}`);
    }
    const rawId = step.id;
    if (rawId === undefined || rawId === null) {
      if (active)
        problems.push(`${where}.id is required for an active workflow`);
    } else if (!isFilledString(rawId) || !STEP_ID_PATTERN.test(rawId)) {
      problems.push(`${where}.id must match ${STEP_ID_PATTERN.source}`);
    } else if (indexOf.has(rawId)) {
      problems.push(`${where}.id duplicates steps[${indexOf.get(rawId)}].id`);
    } else {
      indexOf.set(rawId, index);
    }
    const name = step.name;
    if (!isFilledString(name)) {
      problems.push(`${where}.name is required`);
    } else if (name.length > MAX_NAME_CHARS) {
      problems.push(`${where}.name is longer than ${MAX_NAME_CHARS} chars`);
    }
    const prompt = step.prompt;
    if (active && !isFilledString(prompt)) {
      problems.push(`${where}.prompt is required for an active workflow`);
    } else if (
      prompt !== undefined &&
      prompt !== null &&
      (typeof prompt !== "string" || prompt.length > MAX_PROMPT_CHARS)
    ) {
      problems.push(
        `${where}.prompt must be at most ${MAX_PROMPT_CHARS} chars`,
      );
    }
    const gate = step.gate;
    if (
      gate !== undefined &&
      gate !== null &&
      !WORKFLOW_STEP_GATES.some((candidate) => candidate === gate)
    ) {
      problems.push(
        `${where}.gate must be one of ${WORKFLOW_STEP_GATES.join(", ")}`,
      );
    }
    for (const key of STEP_NAME_LIST_KEYS) {
      collectNameListProblem(step[key], `${where}.${key}`, problems);
    }
  });
  if (problems.length === 0) collectDependencyProblems(node, indexOf, problems);
}

function collectOutputProblems(node: unknown, problems: string[]): void {
  if (node === undefined || node === null) return;
  if (!Array.isArray(node)) {
    problems.push("outputs must be an array");
    return;
  }
  const seen = new Set<string>();
  node.forEach((output, index) => {
    const where = `outputs[${index}]`;
    if (!isMapping(output)) {
      problems.push(`${where} must be an object`);
      return;
    }
    const unknown = Object.keys(output)
      .filter((key) => !hasOwn(OUTPUT_KEYS, key))
      .sort();
    if (unknown.length > 0) {
      problems.push(`${where} has unsupported keys: ${unknown.join(", ")}`);
    }
    const name = output.name;
    if (!isFilledString(name)) {
      problems.push(`${where}.name is required`);
    } else if (seen.has(name)) {
      problems.push(`${where}.name duplicates an earlier output`);
    } else {
      seen.add(name);
    }
    if (!WORKFLOW_OUTPUT_FORMS.some((candidate) => candidate === output.form)) {
      problems.push(
        `${where}.form must be one of ${WORKFLOW_OUTPUT_FORMS.join(", ")}`,
      );
    }
    const path = output.path;
    if (path !== undefined && path !== null && !isFilledString(path)) {
      problems.push(`${where}.path must be a non-empty string`);
    }
    collectTextProblem(
      output.description,
      `${where}.description`,
      problems,
      false,
    );
  });
}

function collectRuleProblems(node: unknown, problems: string[]): void {
  if (node === undefined || node === null) return;
  if (!Array.isArray(node)) {
    problems.push("rules must be an array");
    return;
  }
  if (node.length > MAX_RULES) {
    problems.push(`rules may hold at most ${MAX_RULES} entries`);
  }
  node.forEach((rule, index) => {
    if (!isFilledString(rule)) {
      problems.push(`rules[${index}] must be a non-empty string`);
    } else if (rule.length > MAX_RULE_CHARS) {
      problems.push(`rules[${index}] is longer than ${MAX_RULE_CHARS} chars`);
    }
  });
}

/**
 * Every problem in *raw*, in the order they appear. Empty list = valid.
 *
 * Total and pure, like the server's own: it never throws, and it reports all the
 * problems rather than the first, because the editor shows the list and the author
 * fixes it in one pass.
 */
export function validateWorkflowDocument(raw: unknown): string[] {
  if (!isMapping(raw)) return ["definition must be a JSON object"];
  const problems: string[] = [];
  if (raw.version !== WORKFLOW_VERSION) {
    problems.push(`version must be ${WORKFLOW_VERSION}`);
  }
  const status = raw.status;
  if (
    status !== undefined &&
    status !== null &&
    !WORKFLOW_STATUSES.some((candidate) => candidate === status)
  ) {
    problems.push(`status must be one of ${WORKFLOW_STATUSES.join(", ")}`);
  }
  const unknown = Object.keys(raw)
    .filter((key) => !hasOwn(DOCUMENT_KEYS, key))
    .sort();
  if (unknown.length > 0) {
    problems.push(`definition has unsupported keys: ${unknown.join(", ")}`);
  }
  collectInputProblems(raw.inputs, problems);
  collectStepProblems(raw.steps, problems, raw.status === "active");
  collectOutputProblems(raw.outputs, problems);
  collectRuleProblems(raw.rules, problems);
  return problems;
}

export interface WorkflowJsonRead {
  /** The document, when the text is one. */
  workflow: FeatureWorkflow | null;
  /** Why it is not one — the same list the server would answer with. */
  problems: string[];
}

/** Parse the JSON tab's text into a document, or say every reason it is not one. */
export function readWorkflowJson(text: string): WorkflowJsonRead {
  let parsed: unknown;
  try {
    parsed = JSON.parse(text) as unknown;
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      workflow: null,
      problems: [`definition must be valid JSON: ${message}`],
    };
  }
  const problems = validateWorkflowDocument(parsed);
  return {
    workflow: problems.length === 0 ? (parsed as FeatureWorkflow) : null,
    problems,
  };
}

/** A document as JSON tab text, exactly as it stands. */
export function documentJson(workflow: FeatureWorkflow): string {
  return `${JSON.stringify(workflow, null, 2)}\n`;
}

/** The document as JSON tab text: the normalized one, i.e. what saving writes. */
export function workflowToJson(workflow: FeatureWorkflow): string {
  return documentJson(normalizeWorkflow(workflow));
}

/** Which part of the document one problem belongs to. */
export type WorkflowSection =
  | "document"
  | "inputs"
  | "steps"
  | "outputs"
  | "rules";

/**
 * Where a problem belongs, read off its own wording.
 *
 * Problems name their place (``steps[2].name is required``) because the server
 * reports them to whoever wrote the document, not to a screen. That naming is what
 * lets the same list mark the outline row it is about, whether it came from the
 * editor's own checks or from a refusal.
 */
export function problemOwner(problem: string): {
  section: WorkflowSection;
  step: number | null;
} {
  const step = /^steps\[(\d+)\]/.exec(problem);
  if (step) return { section: "steps", step: Number(step[1]) };
  if (problem.startsWith("steps")) return { section: "steps", step: null };
  if (problem.startsWith("inputs")) return { section: "inputs", step: null };
  if (problem.startsWith("outputs")) return { section: "outputs", step: null };
  if (problem.startsWith("rules")) return { section: "rules", step: null };
  return { section: "document", step: null };
}

/** The problems that belong to one step of the outline. */
export function stepProblems(
  problems: readonly string[],
  index: number,
): string[] {
  return problems.filter((problem) => problemOwner(problem).step === index);
}

/** The problems a section carries itself, rather than through its steps. */
export function sectionProblems(
  problems: readonly string[],
  section: WorkflowSection,
): string[] {
  return problems.filter((problem) => {
    const owner = problemOwner(problem);
    return owner.section === section && owner.step === null;
  });
}
