import { describe, expect, it } from "vitest";

import type { KnowledgeCitation } from "./parseKnowledgeCitations";
import {
  knowledgeLinkHref,
  linkifyKnowledgeCitations,
} from "./linkifyKnowledgeCitations";

const citation = (
  overrides: Partial<KnowledgeCitation>,
): KnowledgeCitation => ({
  kbId: "KB1",
  kbName: "企业知识库",
  docId: "d1",
  filename: "a.dwg",
  ...overrides,
});

describe("linkifyKnowledgeCitations", () => {
  it("links the file name the answer mentions", () => {
    const out = linkifyKnowledgeCitations(
      "已索引 套管四通 202604037(1).dwg 。",
      [citation({ filename: "套管四通 202604037(1).dwg" })],
    );
    const href = knowledgeLinkHref(
      citation({ filename: "套管四通 202604037(1).dwg" }),
    );
    expect(out).toContain(`[套管四通 202604037(1).dwg](${href})`);
  });

  it("replaces the longest name first so short names do not split it", () => {
    const out = linkifyKnowledgeCitations("底层套管四通 202604042(1).dwg", [
      citation({ docId: "short", filename: "套管四通" }),
      citation({ docId: "long", filename: "底层套管四通 202604042(1).dwg" }),
    ]);
    expect(out).toContain(
      "[底层套管四通 202604042(1).dwg](octop-kb://KB1/long)",
    );
  });

  it("replaces a name wrapped in inline code instead of leaving markdown inside it", () => {
    const out = linkifyKnowledgeCitations("路径为 `a.dwg` 已索引", [
      citation({}),
    ]);
    expect(out).toBe("路径为 [a.dwg](octop-kb://KB1/d1) 已索引");
  });

  it("leaves text alone when nothing is cited", () => {
    expect(linkifyKnowledgeCitations("没有引用", [])).toBe("没有引用");
  });

  it("does not double-link an already linked name", () => {
    const href = knowledgeLinkHref(citation({}));
    const text = `[a.dwg](${href})`;
    expect(linkifyKnowledgeCitations(text, [citation({})])).toBe(text);
  });

  it("escapes brackets in the file name so the markdown link survives", () => {
    const out = linkifyKnowledgeCitations("图纸[1].dwg", [
      citation({ filename: "图纸[1].dwg" }),
    ]);
    expect(out).toContain("[图纸\\[1\\].dwg](octop-kb://KB1/d1)");
  });
});
