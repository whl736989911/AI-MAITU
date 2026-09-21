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

/**
 * The artifacts a rerun may inject: those produced by steps strictly before
 * ``seq`` that a rewind does not discard, with the latest producer winning when
 * two steps write the same name.
 */
export function artifactsBefore(
  steps: FeatureRunStep[],
  seq: number,
): FeatureRunArtifact[] {
  const alive: FeatureRunArtifact[] = [];
  for (const step of steps) {
    if (step.seq >= seq || step.voided) continue;
    for (const artifact of step.artifacts) {
      const existing = alive.findIndex((item) => item.name === artifact.name);
      if (existing >= 0) alive.splice(existing, 1);
      alive.push(artifact);
    }
  }
  return alive;
}
