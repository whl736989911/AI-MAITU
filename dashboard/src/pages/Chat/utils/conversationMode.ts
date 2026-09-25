export type ConversationMode = "ask" | "plan" | "craft";

export function normalizeConversationMode(value: unknown): ConversationMode {
  return value === "ask" || value === "plan" ? value : "craft";
}
