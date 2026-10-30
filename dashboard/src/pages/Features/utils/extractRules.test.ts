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
      name: { zh: "示例版式", en: "Example layout" },
      when: { labels_any: ["示例标题"], filename_pattern: "example" },
      fields: {
        drawing_no: { labels: ["图号", "产品图号"] },
        weight_kg: { labels: ["重量"], unit: "kg" },
        max_od_mm: { from: "dimensions", pick: "max_outer" },
        size_text: { from: "dimensions", limit: 5 },
      },
    },
  ],
};

function draft(
  overrides: Partial<ExtractProfileDraft> = {},
): ExtractProfileDraft {
  return {
    id: "p1",
    name: "示例版式",
    labelsAny: "示例标题",
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
    expect(drafts[0].name).toBe("示例版式");
    expect(drafts[0].labelsAny).toBe("示例标题");
    const restored = rulesFromDrafts(drafts, "zh");
    expect(restored.profiles[0].name).toEqual(RULES.profiles[0].name);
    expect(restored.profiles[0].fields).toEqual(RULES.profiles[0].fields);
    expect(restored.profiles[0].when).toEqual(RULES.profiles[0].when);
  });

  it("editing the English profile name preserves the Chinese translation", () => {
    const drafts = draftsFromRules(RULES, "en");
    drafts[0].name = "Renamed layout";
    const saved = rulesFromDrafts(drafts, "en");
    expect(saved.profiles[0].name).toEqual({
      zh: "示例版式",
      en: "Renamed layout",
    });
  });

  it("reopens a text dimension mapping as text when no line limit is supplied", () => {
    const initial = draft({
      fields: [
        {
          ...draft().fields[0],
          source: "dimensions",
          pick: "text",
          labels: "",
          limit: "",
        },
      ],
    });
    const saved = rulesFromDrafts([initial], "zh");
    expect(draftsFromRules(saved, "zh")[0].fields[0].pick).toBe("text");
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
    expect(
      draftProblem(
        [draft({ fields: [{ ...draft().fields[0], fieldKey: "" }] })],
        null,
      ),
    ).toBe("noFields");
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
