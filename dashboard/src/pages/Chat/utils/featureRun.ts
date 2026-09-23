/**
 * One submitted run of a feature's workflow — the values the input card sends,
 * and how what came back is read.
 *
 * The submitted values do not travel as text. They ride the turn as its own
 * ``feature_run`` field — the values the caller filled in, and the files they
 * attached — which the server copies onto the inbound message's metadata under
 * ``octop_feature_run`` (the key ``src/octop/infra/agents/feature_workflow.py``
 * reads it by), so the platform records the run and renders the values into that
 * turn's prompt. The card therefore sends an ordinary chat turn and the platform
 * does the rest. The uploaded files are the turn's ordinary attachments: they
 * already live under the workspace's ``inbound/``, and the same paths are listed
 * on the run so the block the model runs under names the files it was given.
 *
 * **Pure.** The definition owns the shape, the cards own the widgets, and this
 * module owns the two questions both cards ask: what "filled in" means for a
 * declared field, and which produced file answers to which declared output.
 */

import type {
  FeatureWorkflow,
  WorkflowInputField,
  WorkflowInputs,
  WorkflowItemType,
  WorkflowOutput,
} from "../../../api/modules/featureWorkflow";

/** The ``user_turn`` frame field one submitted run is carried in. */
export const FEATURE_RUN_FRAME_KEY = "feature_run";

/** What one submitted run carries, as the frame's ``feature_run`` field. */
export interface FeatureRunPayload {
  /** The submitted values, keyed by field name. */
  inputs: Record<string, unknown>;
  /** The uploaded files' workspace paths. */
  attachments: string[];
}

/** What a field holds while the form is being filled in. */
export type WorkflowInputValue = string | number | boolean | string[] | null;

/** The whole form, keyed by field name. */
export type WorkflowInputDraft = Record<string, WorkflowInputValue>;

/** A produced file, as the output card shows it. */
export interface RunOutputFile {
  /** The workspace path the thread's artifact list reported. */
  path: string;
  /** The file's own name — what it is called when nothing declared it. */
  filename: string;
  /** The declared output this file answers to, when the definition declares one. */
  declared: WorkflowOutput | null;
}

/** The last segment of a workspace path, either separator. */
export function filenameOf(path: string): string {
  const segments = path.split(/[\\/]/).filter(Boolean);
  return segments[segments.length - 1] || path;
}

/** A field that holds a list: an array, or the files a file field takes. */
function isListField(field: WorkflowInputField): boolean {
  return field.type === "array" || field.type === "file";
}

/** The empty form, in the fields' declared order. */
export function emptyInputDraft(inputs: WorkflowInputs): WorkflowInputDraft {
  const draft: WorkflowInputDraft = {};
  for (const [name, field] of Object.entries(inputs.properties)) {
    if (field.type === "boolean") draft[name] = false;
    else if (isListField(field)) draft[name] = [];
    else draft[name] = null;
  }
  return draft;
}

/**
 * Whether a field is answered. A boolean always is — "no" is an answer, not a
 * blank — while a list needs one entry and a text field needs something in it.
 */
export function isFieldFilled(
  field: WorkflowInputField,
  value: WorkflowInputValue | undefined,
): boolean {
  if (field.type === "boolean") return true;
  if (isListField(field)) {
    return Array.isArray(value) && value.length > 0;
  }
  if (field.type === "number" || field.type === "integer") {
    return typeof value === "number" && Number.isFinite(value);
  }
  return typeof value === "string" && value.trim() !== "";
}

/** The required fields that still have no answer, in the declared order. */
export function missingRequiredInputs(
  inputs: WorkflowInputs,
  draft: WorkflowInputDraft,
): string[] {
  const required = inputs.required ?? [];
  return required.filter((name) => {
    const field = inputs.properties[name];
    if (!field) return false;
    return !isFieldFilled(field, draft[name]);
  });
}

/** One entry of an array field, as the item type the field declares. */
function coerceItem(
  type: WorkflowItemType,
  raw: string,
): string | number | boolean {
  const text = raw.trim();
  if (type === "number" || type === "integer") {
    const value = Number(text);
    if (!Number.isFinite(value)) return raw;
    return type === "integer" ? Math.trunc(value) : value;
  }
  if (type === "boolean") {
    const lowered = text.toLowerCase();
    if (["true", "1", "yes"].includes(lowered)) return true;
    if (["false", "0", "no"].includes(lowered)) return false;
    return raw;
  }
  return raw;
}

/** One field's value as the run carries it, or ``null`` when it was left blank. */
function submittedValue(
  field: WorkflowInputField,
  value: WorkflowInputValue | undefined,
): unknown {
  if (field.type === "boolean") return Boolean(value);
  if (field.type === "file") {
    const paths = Array.isArray(value) ? value : [];
    if (paths.length === 0) return null;
    return field.multiple === true ? paths : paths[0];
  }
  if (field.type === "array") {
    const items = Array.isArray(value) ? value : [];
    const itemType = field.items?.type ?? "string";
    return items.map((item) => coerceItem(itemType, String(item)));
  }
  if (field.type === "number" || field.type === "integer") {
    return typeof value === "number" && Number.isFinite(value) ? value : null;
  }
  return typeof value === "string" && value.trim() !== "" ? value.trim() : null;
}

/**
 * The submitted values, as the run's metadata carries them: every field the form
 * declares, with the answers that were actually given. A blank stays out rather
 * than travelling as an empty value the model could read as a deliberate answer.
 */
export function submittedInputs(
  inputs: WorkflowInputs,
  draft: WorkflowInputDraft,
): Record<string, unknown> {
  const values: Record<string, unknown> = {};
  for (const [name, field] of Object.entries(inputs.properties)) {
    const value = submittedValue(field, draft[name]);
    if (value === null) continue;
    values[name] = value;
  }
  return values;
}

/** A path in one spelling: separators normalized, no leading ``./`` or ``/``. */
function comparablePath(path: string): string {
  return path
    .trim()
    .replace(/\\/g, "/")
    .replace(/^\.\//, "")
    .replace(/^\/+/, "")
    .toLowerCase();
}

/** Whether a declared output's ``path`` names this produced file. */
function pathMatches(file: string, declared: string): boolean {
  const produced = comparablePath(file);
  const wanted = comparablePath(declared);
  if (!wanted) return false;
  return produced === wanted || produced.endsWith(`/${wanted}`);
}

/**
 * The produced files, each under the declared output that named it. A declaration
 * without a ``path`` labels nothing: which file it is cannot be known from here,
 * and guessing would put the wrong name on somebody's deliverable.
 */
export function labelRunOutputs(
  files: readonly string[],
  outputs?: readonly WorkflowOutput[],
): RunOutputFile[] {
  const declared = outputs ?? [];
  return files.map((path) => ({
    path,
    filename: filenameOf(path),
    declared:
      declared.find(
        (output) => output.path && pathMatches(path, output.path),
      ) ?? null,
  }));
}

/** Whether the definition declares a form worth showing. */
export function declaredInputs(
  workflow: FeatureWorkflow | null,
): WorkflowInputs | null {
  const inputs = workflow?.inputs;
  if (!inputs || Object.keys(inputs.properties).length === 0) return null;
  return inputs;
}
