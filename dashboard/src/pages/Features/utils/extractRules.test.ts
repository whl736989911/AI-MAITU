import { describe, expect, it } from "vitest";

import type { FeatureExtractRules } from "../../../api/modules/featureData";
import {
  draftProblem,
  draftsFromRules,
  rulesFromDrafts,
  schemaFieldKeys,
  splitList,
  type ExtractProfileDraft,
} from "./extractRules";

const RULES: FeatureExtractRules = {
  version: 2,
  profiles: [
    {
      name: { zh: "锻造工艺卡", en: "Forging card" },
      when: { labels_any: ["锻造工艺卡"], filename_pattern: "工艺卡" },
      fields: {
        drawing_no: { labels: ["图号", "产品图号"] },
        weight_kg: { labels: ["下料重量"], unit: "kg" },
        max_od_mm: { from: "dimensions", pick: "max_outer" },
        size_text: { from: "dimensions", limit: 5 },
      },
    },
  ],
};

function draft(overrides: Partial<ExtractProfileDraft> = {}): ExtractProfileDraft {
  return {
    id: "p1",
    name: "锻造工艺卡",
    labelsAny: "锻造工艺卡",
    filenamePattern: "",
    fields: [
      {
        id: "f1",
        fieldKey: "drawing_no",
        source: "label",
        labels: "图号, 产品图号",
        pattern: "",
        unitKg: false,
        pick: "max_outer",
        limit: "",
      },
    ],
    ...overrides,
  };
}

describe("extract rules drafts", () => {
  it("splits label lists on either comma and trims blanks", () => {
    expect(splitList("图号, 产品图号，零件图号\n\n")).toEqual([
      "图号",
      "产品图号",
      "零件图号",
    ]);
  });

  it("round-trips a stored document without losing its mapping", () => {
    const drafts = draftsFromRules(RULES, "zh");
    expect(drafts).toHaveLength(1);
    expect(drafts[0].name).toBe("锻造工艺卡");
    expect(drafts[0].labelsAny).toBe("锻造工艺卡");
    const restored = rulesFromDrafts(drafts, "zh");
    expect(restored.profiles[0].fields).toEqual(RULES.profiles[0].fields);
    expect(restored.profiles[0].when).toEqual(RULES.profiles[0].when);
  });

  it("drops empty rules instead of sending them", () => {
    const restored = rulesFromDrafts(
      [draft({ fields: [{ ...draft().fields[0], labels: "  " }] })],
      "zh",
    );
    expect(restored.profiles[0].fields).toEqual({ drawing_no: {} });
  });

  it("keeps a dimension rule that picks the first N lines", () => {
    const restored = rulesFromDrafts(
      [
        draft({
          fields: [
            {
              id: "f2",
              fieldKey: "size_text",
              source: "dimensions",
              labels: "",
              pattern: "",
              unitKg: false,
              pick: "text",
              limit: "5",
            },
          ],
        }),
      ],
      "zh",
    );
    expect(restored.profiles[0].fields.size_text).toEqual({
      from: "dimensions",
      limit: 5,
    });
  });

  it("reports an empty document and a profile without fields", () => {
    expect(draftProblem([], null)).toBe("empty");
    expect(draftProblem([draft({ fields: [] })], null)).toBe("noFields");
    expect(draftProblem([draft({ fields: [{ ...draft().fields[0], fieldKey: "" }] })], null)).toBe(
      "noFields",
    );
    expect(draftProblem([draft()], null)).toBeNull();
  });

  it("lists schema field keys once, in declaration order", () => {
    expect(
      schemaFieldKeys({
        version: 1,
        objects: [
          {
            key: "drawing",
            name: "图纸",
            fields: [
              { key: "drawing_no", type: "text", label: "图号" },
              { key: "name", type: "text", label: "名称" },
              { key: "drawing_no", type: "text", label: "图号" },
            ],
          },
        ],
      }),
    ).toEqual(["drawing_no", "name"]);
  });
});
