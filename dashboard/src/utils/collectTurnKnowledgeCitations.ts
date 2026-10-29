import { parseKnowledgeCitations } from "./parseKnowledgeCitations";
import type { KnowledgeCitation } from "./parseKnowledgeCitations";
import type { AssistantTurnSplit } from "../pages/Chat/utils/messageContent";

export type { KnowledgeCitation };

/**
 * Collect unique knowledge citations from tool results in an assistant turn.
 *
 * Any tool result may carry the `octop-kb-citations` trailer — the knowledge
 * tools report the documents they listed, matched or read — so the trailer
 * itself decides, not the tool name.
 */
export function collectTurnKnowledgeCitations(
  split: AssistantTurnSplit,
): KnowledgeCitation[] {
  const out: KnowledgeCitation[] = [];
  const seen = new Set<string>();
  for (const msg of split?.tools ?? []) {
    // Live turns keep the tool output in toolData; replayed history only keeps
    // the raw message content, which is where the trailer then lives.
    const output = msg.toolData?.output ?? msg.content;
    if (!output) continue;
    const { citations } = parseKnowledgeCitations(output);
    for (const citation of citations) {
      if (seen.has(citation.docId)) continue;
      seen.add(citation.docId);
      out.push(citation);
    }
  }
  return out;
}
