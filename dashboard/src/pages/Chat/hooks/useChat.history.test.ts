import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { AUTH_IDENTITY_CHANGED_EVENT } from "../../../api/request";
import * as chatStore from "./chatStore";
import { useChat } from "./useChat";

import type { OctopThreadHistory } from "../../../api/modules/octopThreads";
const thread = "versioned-history-test";
const otherThread = "versioned-history-other-thread";

afterEach(() => {
  vi.restoreAllMocks();
  chatStore.removeSession(thread);
  chatStore.removeSession(otherThread);
});

describe("history failures", () => {
  it("keeps a loaded conversation and its cursor when refreshing fails", async () => {
    chatStore.setHistoryPage(
      thread,
      [{ id: "old", role: "user", content: "keep me", timestamp: 1 }],
      { hasMore: true, nextOffset: 25, nextCursor: "boundary" },
    );
    vi.spyOn(octopThreadsApi, "history").mockRejectedValue(
      new Error("archive unavailable"),
    );
    const { result } = renderHook(() => useChat(thread, "agent"));
    await act(async () => {
      await result.current.refreshHistory();
    });
    expect(result.current.historyError).toBe(true);
    expect(chatStore.getSnapshot(thread).messages[0].content).toBe("keep me");
    expect(chatStore.getSnapshot(thread).historyNextCursor).toBe("boundary");
    expect(chatStore.getSnapshot(thread).historyHasMore).toBe(true);
  });

  it("does not mark a failed initial load as an empty hydrated conversation", async () => {
    const history = vi
      .spyOn(octopThreadsApi, "history")
      .mockRejectedValueOnce(new Error("decode failed"));
    const { result } = renderHook(() => useChat(thread, "agent"));
    await act(async () => {
      await result.current.loadHistory(thread);
    });
    expect(result.current.historyError).toBe(true);
    expect(chatStore.getSnapshot(thread).historyHydrated).toBe(false);
    history.mockResolvedValueOnce({
      thread_id: thread,
      messages: [{ id: "ok", role: "user", content: "restored" }],
      has_more: false,
    });
    await act(async () => {
      await result.current.loadHistory(thread);
    });
    expect(result.current.historyError).toBe(false);
    expect(chatStore.getSnapshot(thread).historyHydrated).toBe(true);
    expect(chatStore.getSnapshot(thread).messages[0].content).toBe("restored");
  });

  it("refetches a thread cached as empty so background turns show up", async () => {
    chatStore.setHistoryPage(thread, [], {
      hasMore: false,
      nextOffset: 0,
      nextCursor: null,
    });
    const history = vi.spyOn(octopThreadsApi, "history").mockResolvedValue({
      thread_id: thread,
      messages: [{ id: "cron", role: "assistant", content: "task done" }],
      has_more: false,
    });
    const { result } = renderHook(() => useChat(thread, "agent"));
    await act(async () => {
      await result.current.loadHistory(thread);
    });
    expect(history).toHaveBeenCalled();
    expect(chatStore.getSnapshot(thread).messages[0].content).toBe("task done");
  });
  it("uses completed server history to replace a disconnected local assistant", async () => {
    const user = {
      id: "stable-user-id",
      role: "user" as const,
      content: "question",
      timestamp: 1,
    };
    chatStore.setHistoryPage(thread, [user], {
      hasMore: false,
      nextOffset: 1,
    });
    chatStore.appendUserMessage(thread, {
      id: "local-stream",
      role: "assistant",
      content: "partial answer",
      status: "streaming",
      timestamp: 2,
    });
    vi.spyOn(octopThreadsApi, "history").mockResolvedValue({
      thread_id: thread,
      messages: [
        user,
        {
          id: "server-answer",
          role: "assistant",
          content: "final answer",
        },
      ],
      has_more: false,
      turn_active: false,
    });
    const { result } = renderHook(() => useChat(thread, "agent"));

    await act(async () => {
      await result.current.refreshHistory();
    });

    expect(
      chatStore.getSnapshot(thread).messages.map((message) => message.id),
    ).toEqual(["stable-user-id", "server-answer"]);
  });

  it("passes the pinned cursor when loading older messages", async () => {
    chatStore.setHistoryPage(
      thread,
      [{ id: "new", role: "user", content: "new", timestamp: 1 }],
      { hasMore: true, nextOffset: 25, nextCursor: "pinned-boundary" },
    );
    const history = vi
      .spyOn(octopThreadsApi, "history")
      .mockResolvedValue({ thread_id: thread, messages: [], has_more: false });
    const { result } = renderHook(() => useChat(thread, "agent"));
    await act(async () => {
      await result.current.loadMoreHistory();
    });
    expect(history).toHaveBeenCalledWith(
      "agent",
      thread,
      expect.objectContaining({ cursor: "pinned-boundary" }),
    );
  });
  it("retries the failed older page with the same cursor", async () => {
    chatStore.setHistoryPage(
      thread,
      [{ id: "latest", role: "user", content: "latest", timestamp: 1 }],
      { hasMore: true, nextOffset: 25, nextCursor: "same-boundary" },
    );
    const history = vi
      .spyOn(octopThreadsApi, "history")
      .mockRejectedValueOnce(new Error("unavailable"))
      .mockResolvedValueOnce({
        thread_id: thread,
        messages: [{ id: "older", role: "user", content: "older" }],
        has_more: false,
      });
    const { result } = renderHook(() => useChat(thread, "agent"));
    await act(async () => {
      await result.current.loadMoreHistory();
    });
    expect(result.current.historyError).toBe(true);
    await act(async () => {
      await result.current.retryHistory();
    });
    expect(history).toHaveBeenLastCalledWith(
      "agent",
      thread,
      expect.objectContaining({ cursor: "same-boundary" }),
    );
    expect(result.current.historyError).toBe(false);
    expect(
      chatStore.getSnapshot(thread).messages.map((m) => m.content),
    ).toEqual(["older", "latest"]);
  });

  it("discards an older thread response after the hook switches threads", async () => {
    let resolveFirst!: (value: OctopThreadHistory) => void;
    const first = new Promise<OctopThreadHistory>((resolve) => {
      resolveFirst = resolve;
    });
    vi.spyOn(octopThreadsApi, "history").mockImplementation(
      (_agentId, targetThreadId) =>
        targetThreadId === thread
          ? first
          : Promise.resolve({
              thread_id: otherThread,
              messages: [{ id: "new-thread", role: "user", content: "new" }],
              has_more: false,
            }),
    );

    const { result, rerender } = renderHook(
      ({ threadId }) => useChat(threadId, "agent"),
      { initialProps: { threadId: thread } },
    );
    let oldLoad!: Promise<void>;
    await act(async () => {
      oldLoad = result.current.loadHistory(thread);
      await Promise.resolve();
    });
    rerender({ threadId: otherThread });
    await act(async () => {
      await result.current.loadHistory(otherThread);
    });
    resolveFirst({
      thread_id: thread,
      messages: [{ id: "old-thread", role: "user", content: "old" }],
      has_more: false,
    });
    await act(async () => {
      await oldLoad;
    });

    expect(chatStore.getSnapshot(thread).messages).toEqual([]);
    expect(chatStore.getSnapshot(otherThread).messages[0]?.id).toBe(
      "new-thread",
    );
  });

  it("rejects a history page owned by a different thread", async () => {
    chatStore.setHistoryPage(
      thread,
      [{ id: "keep", role: "user", content: "existing" }],
      { hasMore: false, nextOffset: 1 },
    );
    chatStore.invalidateHistory(thread);
    vi.spyOn(octopThreadsApi, "history").mockResolvedValue({
      thread_id: otherThread,
      messages: [{ id: "foreign", role: "user", content: "wrong thread" }],
      has_more: false,
    });
    const { result } = renderHook(() => useChat(thread, "agent"));

    await act(async () => {
      await result.current.loadHistory(thread);
    });

    expect(result.current.historyError).toBe(true);
    expect(
      chatStore.getSnapshot(thread).messages.map((message) => message.id),
    ).toEqual(["keep"]);
  });
});

describe("chat identity changes", () => {
  it("does not let an old history response repopulate a reused thread id", async () => {
    let resolveAccountA!: (value: OctopThreadHistory) => void;
    const accountAResponse = new Promise<OctopThreadHistory>((resolve) => {
      resolveAccountA = resolve;
    });
    vi.spyOn(octopThreadsApi, "history").mockReturnValue(accountAResponse);
    const { result } = renderHook(() => useChat(thread, "agent"));
    let oldLoad!: Promise<void>;
    await act(async () => {
      oldLoad = result.current.loadHistory(thread);
      await Promise.resolve();
    });

    window.dispatchEvent(new Event(AUTH_IDENTITY_CHANGED_EVENT));
    chatStore.setHistoryPage(
      thread,
      [{ id: "account-b", role: "user", content: "B history" }],
      { hasMore: false, nextOffset: 1 },
    );
    resolveAccountA({
      thread_id: thread,
      messages: [{ id: "account-a", role: "user", content: "A history" }],
      has_more: false,
    });
    await act(async () => {
      await oldLoad;
    });

    expect(
      chatStore.getSnapshot(thread).messages.map((message) => message.id),
    ).toEqual(["account-b"]);
  });
});
