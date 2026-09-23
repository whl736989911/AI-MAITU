/**
 * A feature's workflow — the definition its runs follow, as the API carries it.
 *
 * One endpoint, three verbs: read the definition, store one (``null`` removes it),
 * remove it. The document itself is the shape ``octop/infra/agents/feature_workflow.py``
 * validates, and this module mirrors it rather than inventing a friendlier one —
 * a definition the editor wrote and one an assistant wrote are the same document,
 * so the type here is the type the server has, keys and all.
 *
 * Two answers are not errors and are on the response instead:
 *   - ``workflow: null`` — the feature declares no workflow, which is a normal
 *     feature rather than a broken one;
 *   - ``error`` — a definition file exists but is not a valid document. A run goes
 *     without the block; the editor shows the reason so its author can fix it.
 *
 * Writing is refused with the usual envelope (``WORKFLOW_INVALID`` carrying every
 * problem in ``details.reason``, ``WORKFLOW_NOT_A_FEATURE`` for an expert). Both
 * are ordinary refusals to ``request``, so nothing is caught here.
 */

import { request } from "../request";

/** The only definition format version this build understands. */
export const WORKFLOW_VERSION = 1;

export const WORKFLOW_STATUSES = ["draft", "active"] as const;
/** ``draft`` = the shape is checked; ``active`` = the definition must stand alone. */
export type WorkflowStatus = (typeof WORKFLOW_STATUSES)[number];

export const WORKFLOW_INPUT_TYPES = [
  "string",
  "number",
  "integer",
  "boolean",
  "array",
  "file",
] as const;
export type WorkflowInputType = (typeof WORKFLOW_INPUT_TYPES)[number];

/** Array fields hold scalars only; objects and nested arrays are not a form. */
export const WORKFLOW_ITEM_TYPES = [
  "string",
  "number",
  "integer",
  "boolean",
] as const;
export type WorkflowItemType = (typeof WORKFLOW_ITEM_TYPES)[number];

export const WORKFLOW_STRING_FORMATS = [
  "text",
  "textarea",
  "date",
  "email",
] as const;
export type WorkflowStringFormat = (typeof WORKFLOW_STRING_FORMATS)[number];

export const WORKFLOW_STEP_GATES = ["auto", "confirm"] as const;
/** ``confirm`` stops the run at this step and asks the caller to go on. */
export type WorkflowStepGate = (typeof WORKFLOW_STEP_GATES)[number];

export const WORKFLOW_OUTPUT_FORMS = [
  "markdown",
  "json",
  "text",
  "file",
] as const;
export type WorkflowOutputForm = (typeof WORKFLOW_OUTPUT_FORMS)[number];

/** A ``{"zh": …, "en": …}`` pair; both halves are required when the pair is used. */
export interface LocalizedText {
  zh: string;
  en: string;
}

/** One field of the run form. ``type`` decides which of the rest apply. */
export interface WorkflowInputField {
  type: WorkflowInputType;
  /** What the field is called, in both languages — required for every field. */
  title: LocalizedText;
  description?: LocalizedText;
  /** string only. */
  format?: WorkflowStringFormat;
  /** string only. */
  enum?: string[];
  /** array only, scalar items. */
  items?: { type: WorkflowItemType };
  /** file only — the accept attribute, e.g. ``.pdf,.docx``. */
  accept?: string;
  /** file only — allow more than one file. */
  multiple?: boolean;
}

/** The run form. ``properties`` order is the form's order, so it is an object. */
export interface WorkflowInputs {
  type: "object";
  /** Field names that must be filled in. */
  required?: string[];
  properties: Record<string, WorkflowInputField>;
}

/** One fixed step of the flow. The array's order *is* the flow. */
export interface WorkflowStep {
  /** ``^[a-z][a-z0-9_]{0,63}$``; optional in draft, required when active. */
  id?: string;
  name: string;
  /** Required when active; ``{{inputs}}`` / ``{{inputs_json}}`` are substituted. */
  prompt?: string;
  /** May only name *earlier* steps. */
  depends_on?: string[];
  skills?: string[];
  subagents?: string[];
  tools?: string[];
  gate?: WorkflowStepGate;
}

/** A deliverable a run produces. */
export interface WorkflowOutput {
  name: string;
  form: WorkflowOutputForm;
  path?: string;
  description?: LocalizedText;
}

/** A feature's workflow definition. */
export interface FeatureWorkflow {
  version: number;
  /** Absent means draft — a half-written definition is never published by omission. */
  status?: WorkflowStatus;
  inputs?: WorkflowInputs;
  steps?: WorkflowStep[];
  outputs?: WorkflowOutput[];
  /** Soft rules the run follows; a caller's own overlay may outrank them. */
  rules?: string[];
}

/** What both reads and writes answer. */
export interface WorkflowResponse {
  workflow: FeatureWorkflow | null;
  error: string | null;
}

/**
 * One run the calling user submitted. The values are the ones its input card
 * sent — kept because nothing else holds them, and because the card is shown
 * read-only afterwards next to what the run produced.
 */
export interface WorkflowRunItem {
  id: string;
  /** Unix seconds the run was submitted at. */
  created_at: number;
  /** The conversation it happened in, when known. */
  thread_id: string | null;
  inputs: Record<string, unknown>;
}

/** The caller's own runs of one feature, newest first. */
export interface WorkflowRunsResponse {
  runs: WorkflowRunItem[];
}

/** One place an applied improvement touched. */
export interface WorkflowChangeItem {
  /** ``/rules/2``, ``/steps/1/gate`` — a JSON-pointer-ish path into the document. */
  path: string;
  /** What that place said before; ``null`` means nothing was there. */
  before: unknown;
  after: unknown;
}

/**
 * One recorded improvement to a feature's workflow. Its ``items`` are how to undo
 * it, so the card can show what it changed and offer to take it back.
 */
export interface WorkflowChange {
  id: string;
  /** ``definition`` (the feature's own, its author's) or ``overlay`` (the caller's own). */
  target: string;
  summary: string;
  items: WorkflowChangeItem[];
  /** ``applied`` or ``reverted``. */
  status: string;
  run_id: string | null;
  created_at: number;
  reverted_at: number | null;
}

export interface WorkflowChangesResponse {
  changes: WorkflowChange[];
}
/** A caller's private standing instruction, never the shared definition. */
export interface WorkflowOverlayResponse {
  overlay: string | null;
}

export const featureWorkflowApi = {
  /** Read the definition. Readable by anyone who may reach the feature. */
  get(agentId: string): Promise<WorkflowResponse> {
    return request<WorkflowResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow`,
    );
  },

  /** Store the definition; ``null`` removes it. A refusal writes nothing. */
  put(
    agentId: string,
    workflow: FeatureWorkflow | null,
  ): Promise<WorkflowResponse> {
    return request<WorkflowResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow`,
      {
        method: "PUT",
        body: JSON.stringify({ workflow }),
      },
    );
  },

  /** Remove the definition — the same thing as storing ``null``. */
  remove(agentId: string): Promise<WorkflowResponse> {
    return request<WorkflowResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow`,
      { method: "DELETE" },
    );
  },

  /**
   * The caller's own runs, newest first. Another caller's runs are never listed:
   * a run carries the values somebody typed.
   */
  runs(agentId: string, limit = 20): Promise<WorkflowRunsResponse> {
    return request<WorkflowRunsResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow/runs?limit=${limit}`,
    );
  },

  /**
   * The improvements this caller may see on one feature, newest first — an
   * applied one is what the chat's improvement card shows and what can be undone.
   */
  changes(agentId: string): Promise<WorkflowChangesResponse> {
    return request<WorkflowChangesResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow/changes`,
    );
  },

  /** Undo one applied improvement. Undoing twice is fine. */
  revertChange(agentId: string, changeId: string): Promise<WorkflowChange> {
    return request<WorkflowChange>(
      `/agents/${encodeURIComponent(
        agentId,
      )}/workflow/changes/${encodeURIComponent(changeId)}/revert`,
      { method: "POST" },
    );
  },

  /** Read only this caller's private instruction for the feature. */
  overlay(agentId: string): Promise<WorkflowOverlayResponse> {
    return request<WorkflowOverlayResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow/overlay`,
    );
  },

  /** Blank or null removes the caller's private instruction. */
  putOverlay(
    agentId: string,
    overlay: string | null,
  ): Promise<WorkflowOverlayResponse> {
    return request<WorkflowOverlayResponse>(
      `/agents/${encodeURIComponent(agentId)}/workflow/overlay`,
      { method: "PUT", body: JSON.stringify({ overlay }) },
    );
  },
};
