import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../hooks/useChat";
import { chatGeneratingPhase } from "./generatingGate";

const user: ChatMessage = {
  id: "u",
  role: "user",
  content: "Find it",
  timestamp: 1,
};
const tool: ChatMessage = {
  id: "t",
  role: "assistant",
  content: "",
  timestamp: 2,
  toolData: { name: "web_fetch" },
  status: "streaming",
};

describe("chatGeneratingPhase", () => {
  it("follows actual progress even between tool output and model text", () => {
    expect(chatGeneratingPhase([tool, user], true)).toBe("waiting");
    expect(chatGeneratingPhase([user, tool], true)).toBe("tools");
    const completed = { ...tool, status: "done" as const };
    expect(chatGeneratingPhase([user, completed], true)).toBe("continuing");
    expect(
      chatGeneratingPhase(
        [
          user,
          completed,
          { ...tool, toolData: undefined, content: "Found it" },
        ],
        true,
      ),
    ).toBe("writing");
    expect(chatGeneratingPhase([user, completed], false)).toBeNull();
  });

  it("does not show running tools while awaiting explicit approval", () => {
    const approval: ChatMessage = {
      ...tool,
      id: "approval",
      toolData: undefined,
      hitlData: { action_requests: [], status: "pending" },
    };
    expect(chatGeneratingPhase([user, approval, tool], true)).toBeNull();
  });
});
