import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../pages/Chat/hooks/useChat";
import { injectPendingHitlMessage } from "./injectPendingHitlMessage";

function message(
  partial: Partial<ChatMessage> & Pick<ChatMessage, "id" | "role">,
): ChatMessage {
  return {
    content: "",
    status: "done",
    timestamp: 0,
    ...partial,
  };
}

const pending = {
  pending_id: "request-1",
  action_requests: [{ name: "ask_user_question", args: { questions: [] } }],
};

describe("injectPendingHitlMessage", () => {
  it("restores one pending card with the server request id", () => {
    const result = injectPendingHitlMessage([], pending);
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe("hitl-request-1");
    expect(result[0].hitlData?.pending_id).toBe("request-1");
    expect(result[0].hitlData?.status).toBe("pending");
  });

  it("does not resurrect a card already answered in history", () => {
    const answered = [
      message({
        id: "hitl-request-1",
        role: "assistant",
        hitlData: {
          action_requests: pending.action_requests,
          pending_id: "request-1",
          status: "approved",
        },
      }),
    ];
    expect(injectPendingHitlMessage(answered, pending)).toEqual(answered);
  });

  it("does not add a duplicate when a pending card is already present", () => {
    const existing = [
      message({
        id: "hitl-request-1",
        role: "assistant",
        hitlData: {
          action_requests: pending.action_requests,
          pending_id: "request-1",
          status: "pending",
        },
      }),
    ];
    expect(injectPendingHitlMessage(existing, pending)).toEqual(existing);
  });
});
