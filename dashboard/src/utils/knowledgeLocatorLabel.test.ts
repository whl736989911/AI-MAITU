import { describe, expect, it } from "vitest";

import { knowledgeLocatorLabel } from "./knowledgeLocatorLabel";

const t = (key: string, options?: Record<string, unknown>) =>
  `${key.replace("chat.citationLocator.", "")}${
    options ? JSON.stringify(options) : ""
  }`;

describe("knowledgeLocatorLabel", () => {
  it("prefers page locators and marks OCR text", () => {
    expect(knowledgeLocatorLabel({ page: 3 }, t)).toBe('page{"n":3}');
    expect(knowledgeLocatorLabel({ page: 3, image_ocr: true }, t)).toBe(
      'page{"n":3} · OCR',
    );
  });

  it("names the sheet and row of a spreadsheet block", () => {
    expect(knowledgeLocatorLabel({ sheet: "台账", row: 12 }, t)).toBe(
      'sheetRow{"sheet":"台账","n":12}',
    );
  });

  it("names a table row, then a bare line", () => {
    expect(knowledgeLocatorLabel({ table: 1, row: 5 }, t)).toBe(
      'tableRow{"table":1,"n":5}',
    );
    expect(knowledgeLocatorLabel({ line: 42 }, t)).toBe('line{"n":42}');
  });

  it("names a Word paragraph and an unlabelled spreadsheet row", () => {
    expect(knowledgeLocatorLabel({ paragraph: 7 }, t)).toBe('paragraph{"n":7}');
    expect(knowledgeLocatorLabel({ row: 9 }, t)).toBe('row{"n":9}');
  });

  it("locates a CAD text entity by type and handle", () => {
    expect(
      knowledgeLocatorLabel(
        { kind: "cad_entity", entity: "TEXT", handle: "628" },
        t,
      ),
    ).toBe('cadEntity{"entity":"TEXT","handle":"628"}');
    expect(knowledgeLocatorLabel({ entity: "MTEXT" }, t)).toBe("MTEXT");
  });

  it("returns nothing when the parser could not locate the block", () => {
    expect(knowledgeLocatorLabel({ scope: "document" }, t)).toBe("");
    expect(knowledgeLocatorLabel({}, t)).toBe("");
    expect(knowledgeLocatorLabel(undefined, t)).toBe("");
  });

  it("ignores non-numeric or non-positive positions", () => {
    expect(knowledgeLocatorLabel({ line: "x" }, t)).toBe("");
    expect(knowledgeLocatorLabel({ page: 0 }, t)).toBe("");
  });
});
