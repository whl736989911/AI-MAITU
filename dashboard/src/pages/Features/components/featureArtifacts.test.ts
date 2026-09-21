import { describe, expect, it } from "vitest";
import type {
  FeatureRunArtifact,
  FeatureRunStep,
} from "../../../api/modules/features";
import {
  artifactsBefore,
  epochMillis,
  parseArtifactEdits,
  seedDrafts,
} from "./featureArtifacts";

/**
 * What a person types at a gate becomes either an edit or a refusal.
 *
 * Two failures this mapping exists to prevent: an approval that carries an
 * artifact nobody touched (which would write a human change into the audit that
 * never happened), and a rerun that injects only part of what was corrected
 * because the rest failed to parse in silence.
 */

function artifact(
  name: string,
  value: unknown,
  schema = "table:4cols",
): FeatureRunArtifact {
  return { name, schema, value };
}

function stepFixture(overrides: Partial<FeatureRunStep> = {}): FeatureRunStep {
  return {
    id: "extract_l1",
    name: "提取 L1",
    seq: 0,
    status: "succeeded",
    gate: "auto",
    on_failure: "abort",
    mode: "agent",
    artifacts: [],
    started_at: 1_760_000_000_000,
    ended_at: 1_760_000_001_000,
    error: null,
    attempts: 1,
    voided: false,
    ...overrides,
  };
}

describe("artifact drafts ⇄ edits", () => {
  it("seeds one draft per artifact and keeps a string a string", () => {
    const drafts = seedDrafts([
      artifact("note", "客户名写全称"),
      artifact("bom_rows", [["A", 2]], "table:2cols"),
    ]);

    expect(drafts.note).toBe("客户名写全称");
    expect(JSON.parse(drafts.bom_rows)).toEqual([["A", 2]]);
  });

  it("sends only the artifact the person actually changed", () => {
    const artifacts = [
      artifact("bom_rows", [["A", 2]]),
      artifact("coating", "powder"),
    ];
    const drafts = seedDrafts(artifacts);
    drafts.coating = JSON.stringify("anodised");

    const parsed = parseArtifactEdits(artifacts, drafts);

    expect(parsed).toEqual({ edits: { coating: "anodised" } });
  });

  it("treats a rewritten value that says the same thing as untouched", () => {
    const artifacts = [artifact("routing", { b: 2, a: 1 })];
    // Same object, keys in another order: nothing a person changed.
    const drafts = { routing: JSON.stringify({ a: 1, b: 2 }) };

    expect(parseArtifactEdits(artifacts, drafts)).toEqual({ edits: {} });
  });

  it("refuses a draft that is not JSON instead of sending it as text", () => {
    const artifacts = [artifact("bom_rows", [["A", 2]])];
    const drafts = { bom_rows: "客户名写全称" };

    const parsed = parseArtifactEdits(artifacts, drafts);

    expect(parsed).toEqual({ failure: { artifact: "bom_rows" } });
  });

  it("offers a rerun only the artifacts the rewind keeps alive", () => {
    const steps = [
      stepFixture({
        id: "read_bom",
        seq: 0,
        artifacts: [artifact("bom_rows", [["L1", "A"]])],
      }),
      stepFixture({
        id: "cross_check",
        seq: 1,
        artifacts: [artifact("bom_rows", [["L1", "B"]])],
      }),
      stepFixture({
        id: "op_design",
        seq: 2,
        artifacts: [artifact("ops", "工艺")],
      }),
      stepFixture({
        id: "discarded",
        seq: 3,
        voided: true,
        artifacts: [artifact("gone", "作废")],
      }),
    ];

    const allowEdit = { read_bom: true, cross_check: false };

    // Rerunning op_design (seq 2) keeps what the steps before it produced, with
    // the later writer of a shared name winning — and discards the voided step.
    expect(artifactsBefore(steps, 2, allowEdit)).toEqual([
      {
        artifact: artifact("bom_rows", [["L1", "B"]]),
        stepId: "cross_check",
        stepName: "提取 L1",
        // The write lands on the step that produced the artifact, so that is
        // where the definition's ``allow_edit`` is read.
        editable: false,
      },
    ]);
  });
});

describe("run timestamps", () => {
  it("reads the seconds the run tables store as milliseconds", () => {
    // 2026-09-21 in seconds; read as milliseconds it would render as 1970.
    expect(epochMillis(1_789_996_052)).toBe(1_789_996_052_000);
  });

  it("leaves a millisecond value alone and treats nothing as no time", () => {
    expect(epochMillis(1_789_996_052_000)).toBe(1_789_996_052_000);
    expect(epochMillis(null)).toBeNull();
    expect(epochMillis(0)).toBeNull();
    expect(epochMillis(undefined)).toBeNull();
  });
});
