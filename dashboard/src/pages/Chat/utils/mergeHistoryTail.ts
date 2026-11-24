import type { ChatMessage } from "../hooks/sseHelpers";

function assistantRichness(messages: ChatMessage[]): number {
  let score = 0;
  for (const message of messages) {
    if (message.role !== "assistant" || message.toolData) continue;
    score += message.content.length;
    for (const block of message.contentBlocks ?? []) {
      if (block.type === "text") score += block.content.length;
    }
  }
  return score;
}

function toolRichness(messages: ChatMessage[]): number {
  let score = 0;
  for (const message of messages) {
    if (message.role !== "tool" && !message.toolData) continue;
    score += 1 + message.content.length;
    score += (message.toolData?.output ?? "").length;
    score += (message.toolData?.arguments ?? "").length;
  }
  return score;
}

function localTailRicher(local: ChatMessage[], server: ChatMessage[]): boolean {
  const localText = assistantRichness(local);
  const serverText = assistantRichness(server);
  return localText === serverText
    ? toolRichness(local) > toolRichness(server)
    : localText > serverText;
}

function newSendIndex(tail: ChatMessage[], loadedIds: Set<string>): number {
  return tail.findIndex(
    (message) =>
      message.role === "user" &&
      message.localPending &&
      !loadedIds.has(message.id),
  );
}

export function retainedHistoryPrefix(
  loaded: ChatMessage[],
  current: ChatMessage[],
  confirmedUserId?: string,
): ChatMessage[] {
  const ids = new Set(loaded.map((message) => message.id));
  const overlap = current.findIndex((message) => ids.has(message.id));
  if (
    overlap <= 0 ||
    (current[overlap].localPending && current[overlap].id !== confirmedUserId)
  )
    return [];
  const prefix = current.slice(0, overlap);
  return prefix.every(
    (message) => !message.localPending && message.status !== "streaming",
  )
    ? prefix
    : [];
}

export function mergeHistoryWithLocalTail(
  loaded: ChatMessage[],
  current: ChatMessage[],
  isStreaming = false,
  confirmedUserId?: string,
): ChatMessage[] {
  if (!current.length) return loaded;
  const loadedIds = new Set(loaded.map((message) => message.id));
  const firstOverlap = current.findIndex((message) =>
    loadedIds.has(message.id),
  );
  if (firstOverlap < 0) {
    const pending = newSendIndex(current, loadedIds);
    return pending < 0 ? loaded : [...loaded, ...current.slice(pending)];
  }

  let lastOverlap = firstOverlap;
  for (let index = current.length - 1; index >= 0; index -= 1) {
    if (loadedIds.has(current[index].id)) {
      lastOverlap = index;
      break;
    }
  }
  const olderPrefix = retainedHistoryPrefix(loaded, current, confirmedUserId);
  const localTail = current.slice(lastOverlap + 1);
  const base = [...olderPrefix, ...loaded];
  if (!localTail.length) return base;
  const pending = newSendIndex(localTail, loadedIds);
  if (pending >= 0) return [...base, ...localTail.slice(pending)];

  const overlapId = current[lastOverlap].id;
  const loadedOverlap = loaded.findIndex((message) => message.id === overlapId);
  const serverAfter = loadedOverlap < 0 ? [] : loaded.slice(loadedOverlap + 1);
  if (!serverAfter.length) return [...base, ...localTail];
  const live =
    isStreaming || localTail.some((message) => message.status === "streaming");
  if (
    live &&
    !serverAfter.some((message) => message.role === "user") &&
    localTailRicher(localTail, serverAfter)
  ) {
    return [...base.slice(0, base.length - serverAfter.length), ...localTail];
  }
  return base;
}
