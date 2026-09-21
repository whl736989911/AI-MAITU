/**
 * Pure helpers for the run view: turning artifact values into something a person
 * can edit, and turning their edits back into the map the API takes.
 *
 * The two directions are deliberately not symmetric. Reading shows the value as
 * JSON, whatever its type. Writing parses it back and keeps *only* what actually
 * changed — an edit that changes nothing would otherwise be written into the
 * audit as a human change that never happened.
 */

import type {
  FeatureArtifactEdits,
  FeatureRunArtifact,
  FeatureRunStep,
} from "../../../api/modules/features";

/**
 * A run timestamp as milliseconds, whatever unit the API used.
 *
 * The run tables store epoch **seconds**; an API contract once said
 * milliseconds, and a value read in the wrong unit renders as 1970. The two are
 * separated cleanly by 1e12 — a millisecond value only falls below it before
 * 2001, and a seconds value only rises above it after the year 33658.
 */
export function epochMillis(value: number | null | undefined): number | null {
  if (typeof value !== "number" || value <= 0) return null;
  return value < 1_000_000_000_000 ? value * 1000 : value;
}

/** What an artifact looks like while somebody is editing it: JSON as typed. */
export type ArtifactDrafts = Record<string, string>;

/** The value as the editor shows it: a string stays itself, everything is JSON. */
export function artifactText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "";
  return JSON.stringify(value, null, 2);
}

/**
 * Key-order-independent serialisation, so "the person rewrote the same object"
 * is not mistaken for a change.
 */
function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) {
    return `[${value.map(canonicalJson).join(",")}]`;
  }
  if (value !== null && typeof value === "object") {
    const entries = Object.entries(value as Record<string, unknown>).sort(
      ([a], [b]) => (a < b ? -1 : a > b ? 1 : 0),
    );
    return `{${entries
      .map(([key, item]) => `${JSON.stringify(key)}:${canonicalJson(item)}`)
      .join(",")}}`;
  }
  return JSON.stringify(value) ?? "null";
}

/** One draft per artifact, seeded from what the run currently holds. */
export function seedDrafts(artifacts: FeatureRunArtifact[]): ArtifactDrafts {
  const drafts: ArtifactDrafts = {};
  for (const artifact of artifacts) {
    drafts[artifact.name] = artifactText(artifact.value);
  }
  return drafts;
}

/** A draft that is not JSON is refused as itself — never quietly sent as text. */
export interface ArtifactParseFailure {
  artifact: string;
}

/**
 * The edits to send, or the artifact whose draft could not be parsed.
 *
 * Only artifacts whose value actually differs are included, so an untouched gate
 * approves without inventing a human edit, and an untouched artifact at a rerun
 * is not injected back as if somebody had changed it.
 */
export function parseArtifactEdits(
  artifacts: FeatureRunArtifact[],
  drafts: ArtifactDrafts,
): { edits: FeatureArtifactEdits } | { failure: ArtifactParseFailure } {
  const edits: FeatureArtifactEdits = {};
  for (const artifact of artifacts) {
    const draft = drafts[artifact.name];
    if (draft === undefined) continue;
    let parsed: unknown;
    try {
      parsed = JSON.parse(draft);
    } catch {
      return { failure: { artifact: artifact.name } };
    }
    if (canonicalJson(parsed) === canonicalJson(artifact.value)) continue;
    edits[artifact.name] = parsed;
  }
  return { edits };
}

/** One artifact a rerun could inject, and where it came from. */
export interface RewindArtifact {
  artifact: FeatureRunArtifact;
  /** The step that produced it — a correction is a write *to that step*. */
  stepId: string;
  stepName: string;
  /**
   * Whether that step declares ``allow_edit``. The server refuses an edit to an
   * artifact whose step does not, so the editor offers only the ones it can land.
   */
  editable: boolean;
}

/**
 * The artifacts a rerun could inject: those produced by steps strictly before
 * ``seq`` that a rewind does not discard, with the latest producer winning when
 * two steps write the same name.
 *
 * ``allowEdit`` is the definition's own declaration per step id; a run row does
 * not carry it, and an edit naming a step without it is refused by the server.
 */
export function artifactsBefore(
  steps: FeatureRunStep[],
  seq: number,
  allowEdit: Record<string, boolean>,
): RewindArtifact[] {
  const alive: RewindArtifact[] = [];
  for (const step of steps) {
    if (step.seq >= seq || step.voided) continue;
    for (const artifact of step.artifacts) {
      const existing = alive.findIndex(
        (item) => item.artifact.name === artifact.name,
      );
      if (existing >= 0) alive.splice(existing, 1);
      alive.push({
        artifact,
        stepId: step.id,
        stepName: step.name,
        editable: allowEdit[step.id] === true,
      });
    }
  }
  return alive;
}
