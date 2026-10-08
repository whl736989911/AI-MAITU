import {
  normalizeHitlRequest,
  type HitlPendingPayload,
} from "../api/types/hitl";
import type { ChatMessage } from "../pages/Chat/hooks/useChat";

export function injectPendingHitlMessage(
  messages: ChatMessage[],
  pending: unknown,
): ChatMessage[] {
  const normalized = pending ? normalizeHitlRequest(pending) : null;
  if (!normalized?.action_requests.length) return messages;
  const pendingId = normalized.pending_id?.trim() || "";
  const injectedId = pendingId ? `hitl-${pendingId}` : "";
  if (
    messages.some(
      (message) =>
        message.hitlData?.status === "pending" ||
        (pendingId &&
          (message.id === injectedId ||
            message.hitlData?.pending_id === pendingId)),
    )
  ) {
    return messages;
  }
  return [
    ...messages,
    {
      id: injectedId || `hitl-${Date.now()}`,
      role: "assistant",
      content: "",
      hitlData: {
        action_requests: normalized.action_requests,
        review_configs: normalized.review_configs,
        status: "pending",
        ...(pendingId ? { pending_id: pendingId } : {}),
      },
      status: "done",
      timestamp: Date.now(),
    },
  ];
}

export type { HitlPendingPayload };
