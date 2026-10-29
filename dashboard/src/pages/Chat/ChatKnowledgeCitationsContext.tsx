import { createContext, useContext } from "react";

import type { KnowledgeCitation } from "../../utils/parseKnowledgeCitations";

/**
 * Citations of the assistant turn currently being rendered.
 *
 * Answers must link the file names they mention regardless of which component
 * paints the bubble (answer row, process segment, compact summary), so the turn
 * publishes its citations here instead of threading a prop through every path.
 */
const ChatKnowledgeCitationsContext = createContext<KnowledgeCitation[]>([]);

export const ChatKnowledgeCitationsProvider =
  ChatKnowledgeCitationsContext.Provider;

export function useChatKnowledgeCitations(fallback?: KnowledgeCitation[]) {
  const fromContext = useContext(ChatKnowledgeCitationsContext);
  if (fromContext.length > 0) return fromContext;
  return fallback ?? fromContext;
}
