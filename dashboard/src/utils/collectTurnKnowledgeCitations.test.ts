import { describe, expect, it } from "vitest";

import type { ChatMessage } from "../pages/Chat/hooks/sseHelpers";
import type { AssistantTurnSplit } from "../pages/Chat/utils/messageContent";
import { collectTurnKnowledgeCitations } from "./collectTurnKnowledgeCitations";

const MARKER = "<!--octop-kb-citations:";

function trailer(rows: { doc_id: string; filename?: string }[]): string {
  const payload = rows.map((row) => ({
    kb_id: "KB1",
    kb_name: "企业知识库",
    doc_id: row.doc_id,
    filename: row.filename ?? `${row.doc_id}.doc`,
    path: row.filename ?? `${row.doc_id}.doc`,
  }));
  return `\n${MARKER}${JSON.stringify(payload)}-->`;
}

function toolMessage(name: string, output: string): ChatMessage {
  return {
    id: `${name}-1`,
    role: "tool",
    content: output,
    toolData: { name, output },
    timestamp: 0,
  };
}

function split(tools: ChatMessage[]): AssistantTurnSplit {
  return { tools, thinkings: [], processSteps: [], answerMessage: null };
}

describe("collectTurnKnowledgeCitations", () => {
  it("reads the citation trailer from any knowledge tool, not just search_knowledge", () => {
    const citations = collectTurnKnowledgeCitations(
      split([
        toolMessage(
          "search_knowledge_paths",
          `{"status":"found"}${trailer([
            { doc_id: "d1", filename: "台账.doc" },
          ])}`,
        ),
      ]),
    );

    expect(citations).toEqual([
      {
        kbId: "KB1",
        kbName: "企业知识库",
        docId: "d1",
        filename: "台账.doc",
        path: "台账.doc",
      },
    ]);
  });

  it("deduplicates a document reported by several tool results", () => {
    const citations = collectTurnKnowledgeCitations(
      split([
        toolMessage(
          "browse_knowledge",
          `{"documents":[]}${trailer([{ doc_id: "d1" }])}`,
        ),
        toolMessage(
          "read_knowledge_document",
          `{"status":"read"}${trailer([{ doc_id: "d1" }])}`,
        ),
      ]),
    );

    expect(citations.map((citation) => citation.docId)).toEqual(["d1"]);
  });

  it("reads the trailer from replayed history, where only content survives", () => {
    const history: ChatMessage = {
      id: "h1",
      role: "tool",
      content: `{"status":"found"}${trailer([{ doc_id: "d9" }])}`,
      timestamp: 0,
    };

    expect(
      collectTurnKnowledgeCitations(split([history])).map((c) => c.docId),
    ).toEqual(["d9"]);
  });

  it("returns nothing when tool output carries no trailer", () => {
    const citations = collectTurnKnowledgeCitations(
      split([
        toolMessage(
          "search_knowledge_paths",
          '{"status":"no_match","documents":[]}',
        ),
      ]),
    );

    expect(citations).toEqual([]);
  });
});
