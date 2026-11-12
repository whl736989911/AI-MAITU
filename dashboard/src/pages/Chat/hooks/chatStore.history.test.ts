import { afterEach, describe, expect, it } from "vitest";
import {
  appendUserMessage,
  captureHistoryBaseline,
  getSnapshot,
  invalidateHistory,
  isHistoryStale,
  prependHistoryMessages,
  removeSession,
  setHistoryPage,
  type ChatMessage,
} from "./chatStore";

const SESSION = "test-thread-history";

function makeMessage(id: string): ChatMessage {
  return {
    id,
    role: "user",
    content: `message ${id}`,
    timestamp: Date.now(),
  };
}

describe("prependHistoryMessages", () => {
  afterEach(() => {
    removeSession(SESSION);
  });

  it("skips messages whose ids already exist in the session", () => {
    setHistoryPage(SESSION, [makeMessage("m25"), makeMessage("m26")], {
      hasMore: true,
      nextOffset: 25,
    });

    prependHistoryMessages(
      SESSION,
      [makeMessage("m24"), makeMessage("m25"), makeMessage("m23")],
      { hasMore: true, nextOffset: 50 },
    );

    const ids = getSnapshot(SESSION).messages.map((message) => message.id);
    expect(ids).toEqual(["m24", "m23", "m25", "m26"]);
  });

  it("updates pagination state even when all older messages are duplicates", () => {
    setHistoryPage(SESSION, [makeMessage("m1")], {
      hasMore: true,
      nextOffset: 25,
    });

    prependHistoryMessages(SESSION, [makeMessage("m1")], {
      hasMore: false,
      nextOffset: 50,
    });

    expect(getSnapshot(SESSION).messages).toHaveLength(1);
    expect(getSnapshot(SESSION).historyHasMore).toBe(false);
    expect(getSnapshot(SESSION).historyNextOffset).toBe(50);
  });
});

describe("setHistoryPage live-tail merge", () => {
  afterEach(() => {
    removeSession(SESSION);
  });

  it("preserves post-request live messages despite server timestamp skew", () => {
    const initial = makeMessage("old");
    setHistoryPage(SESSION, [initial], {
      hasMore: false,
      nextOffset: 1,
    });
    const baseline = captureHistoryBaseline(SESSION);
    const live = { ...makeMessage("live"), timestamp: 1 };
    appendUserMessage(SESSION, live);

    setHistoryPage(SESSION, [{ ...initial, timestamp: 9_999_999 }], {
      hasMore: true,
      nextOffset: 2,
      baseline,
    });

    expect(getSnapshot(SESSION).messages.map((message) => message.id)).toEqual([
      "old",
      "live",
    ]);
  });

  it("uses the current streamed version when history has the same message id", () => {
    const streaming = {
      ...makeMessage("assistant"),
      role: "assistant" as const,
      content: "new streamed text",
      status: "streaming" as const,
    };
    appendUserMessage(SESSION, streaming);

    setHistoryPage(
      SESSION,
      [
        {
          ...streaming,
          content: "stale persisted text",
          status: "done",
        },
      ],
      { hasMore: false, nextOffset: 1 },
    );

    expect(getSnapshot(SESSION).messages[0].content).toBe("new streamed text");
  });

  it("replaces the local assistant after the same user turn is persisted", () => {
    const user = makeMessage("send-id");
    setHistoryPage(SESSION, [user], { hasMore: false, nextOffset: 1 });
    const baseline = captureHistoryBaseline(SESSION);
    appendUserMessage(SESSION, {
      ...makeMessage("local-assistant"),
      role: "assistant",
      content: "completed answer",
      status: "done",
    });

    setHistoryPage(
      SESSION,
      [
        user,
        {
          ...makeMessage("persisted-assistant"),
          role: "assistant",
          content: "completed answer",
          status: "done",
        },
      ],
      { hasMore: false, nextOffset: 2, baseline },
    );

    expect(getSnapshot(SESSION).messages.map((message) => message.id)).toEqual([
      "send-id",
      "persisted-assistant",
    ]);
  });

  it("keeps same-text user turns distinct when their message ids differ", () => {
    setHistoryPage(
      SESSION,
      [
        { ...makeMessage("send-1"), content: "repeat this" },
        { ...makeMessage("send-2"), content: "repeat this" },
      ],
      { hasMore: false, nextOffset: 2 },
    );

    expect(getSnapshot(SESSION).messages.map((message) => message.id)).toEqual([
      "send-1",
      "send-2",
    ]);
  });

  it("preserves a pending optimistic user turn already in the read baseline", () => {
    const pending = {
      ...makeMessage("pending-user"),
      role: "user" as const,
      localPending: true,
    };
    setHistoryPage(SESSION, [pending], { hasMore: false, nextOffset: 1 });
    const baseline = captureHistoryBaseline(SESSION);

    setHistoryPage(SESSION, [], {
      hasMore: false,
      nextOffset: 0,
      baseline,
    });

    expect(getSnapshot(SESSION).messages.map((message) => message.id)).toEqual([
      "pending-user",
    ]);
    expect(getSnapshot(SESSION).messages[0].localPending).toBe(true);
  });

  it("marks optimistic user messages as pending and deduplicates their ids", () => {
    const pending = makeMessage("optimistic");
    appendUserMessage(SESSION, pending);
    appendUserMessage(SESSION, pending);

    expect(getSnapshot(SESSION).messages).toHaveLength(1);
    expect(getSnapshot(SESSION).messages[0].localPending).toBe(true);
  });

  it("clears localPending once canonical history contains the user id", () => {
    appendUserMessage(SESSION, {
      ...makeMessage("persisted-pending"),
      role: "user",
    });

    setHistoryPage(SESSION, [makeMessage("persisted-pending")], {
      hasMore: false,
      nextOffset: 1,
    });

    expect(getSnapshot(SESSION).messages[0].localPending).toBeUndefined();
  });
});

describe("invalidateHistory", () => {
  afterEach(() => {
    removeSession(SESSION);
  });

  it("marks a hydrated session stale without dropping its messages", () => {
    setHistoryPage(SESSION, [makeMessage("m1")], {
      hasMore: false,
      nextOffset: 1,
    });

    invalidateHistory(SESSION);

    expect(isHistoryStale(SESSION)).toBe(true);
    expect(getSnapshot(SESSION).messages).toHaveLength(1);
    expect(getSnapshot(SESSION).historyHydrated).toBe(true);
  });

  it("clears staleness once the next page lands", () => {
    setHistoryPage(SESSION, [], { hasMore: false, nextOffset: 0 });
    invalidateHistory(SESSION);

    setHistoryPage(SESSION, [makeMessage("m1")], {
      hasMore: false,
      nextOffset: 1,
    });

    expect(isHistoryStale(SESSION)).toBe(false);
  });

  it("ignores sessions that never loaded history", () => {
    invalidateHistory(SESSION);
    expect(isHistoryStale(SESSION)).toBe(false);
  });
});
