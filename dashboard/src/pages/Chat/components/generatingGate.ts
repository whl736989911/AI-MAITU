import type { ChatMessage } from "../hooks/useChat";

export type ChatGeneratingPhase =
  | "waiting"
  | "thinking"
  | "tools"
  | "continuing"
  | "writing";

export function chatGeneratingPhase(
  messages: readonly ChatMessage[],
  isStreaming: boolean,
  loading?: boolean,
): ChatGeneratingPhase | null {
  if (!isStreaming || loading) return null;

  let latest: ChatGeneratingPhase = "waiting";
  let runningTools = false;
  for (let index = messages.length - 1; index >= 0; index--) {
    const message = messages[index];
    if (message.role === "user") break;
    if (message.hitlData?.status === "pending") return null;
    if (message.toolData && message.status === "streaming") runningTools = true;
    if (latest !== "waiting" || message.role !== "assistant") continue;
    if (message.toolData) {
      latest = "continuing";
    } else if (message.status === "streaming") {
      const lastBlock =
        message.contentBlocks?.[message.contentBlocks.length - 1];
      if (lastBlock?.type === "thinking") latest = "thinking";
      else if (message.content || lastBlock?.type === "text")
        latest = "writing";
    } else {
      latest = "continuing";
    }
  }
  return runningTools ? "tools" : latest;
}
