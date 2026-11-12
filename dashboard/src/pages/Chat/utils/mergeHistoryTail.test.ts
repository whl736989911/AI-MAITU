import { describe, expect, it } from "vitest";
import type { ChatMessage } from "../hooks/sseHelpers";
import {
  mergeHistoryWithLocalTail,
  retainedHistoryPrefix,
} from "./mergeHistoryTail";

function message(
  id: string,
  role: ChatMessage["role"],
  content: string,
  status?: ChatMessage["status"],
): ChatMessage {
  return { id, role, content, timestamp: 1, status };
}

function pending(id: string, content: string): ChatMessage {
  return { ...message(id, "user", content), localPending: true };
}

describe("history tail reconciliation", () => {
  it("keeps a new optimistic send after the latest server page", () => {
    const loaded = [message("old", "assistant", "previous answer")];
    const local = [
      pending("new-user", "continue"),
      message("new-answer", "assistant", "partial"),
    ];
    expect(
      mergeHistoryWithLocalTail(loaded, local).map((item) => item.id),
    ).toEqual(["old", "new-user", "new-answer"]);
  });

  it("does not lose a repeated-text send with a distinct id", () => {
    const loaded = [
      message("user-1", "user", "repeat"),
      message("answer-1", "assistant", "done"),
    ];
    const local = [
      ...loaded,
      pending("user-2", "repeat"),
      message("answer-2", "assistant", "new answer"),
    ];
    expect(
      mergeHistoryWithLocalTail(loaded, local).map((item) => item.id),
    ).toEqual(["user-1", "answer-1", "user-2", "answer-2"]);
  });

  it("does not append an unmatched older local turn", () => {
    const loaded = [
      message("new-user", "user", "recent"),
      message("new-answer", "assistant", "latest"),
    ];
    const stale = [
      message("old-user", "user", "recent"),
      message(
        "old-answer",
        "assistant",
        "stale but longer".repeat(20),
        "streaming",
      ),
    ];
    expect(mergeHistoryWithLocalTail(loaded, stale, true)).toEqual(loaded);
  });

  it("keeps a richer live answer until the canonical reply catches up", () => {
    const loaded = [
      message("user", "user", "question"),
      message("server", "assistant", "short"),
    ];
    const local = [
      message("user", "user", "question"),
      message("local", "assistant", "longer in-flight answer", "streaming"),
    ];
    expect(
      mergeHistoryWithLocalTail(loaded, local, true).map((item) => item.id),
    ).toEqual(["user", "local"]);
    const complete = [
      loaded[0],
      message("server", "assistant", "longer in-flight answer"),
    ];
    expect(
      mergeHistoryWithLocalTail(complete, local).map((item) => item.id),
    ).toEqual(["user", "server"]);
  });

  it("retains only a persisted prefix before the latest page", () => {
    const loaded = [message("latest-user", "user", "latest")];
    const oldPrefix = [message("older", "user", "older"), loaded[0]];
    expect(
      retainedHistoryPrefix(loaded, oldPrefix).map((item) => item.id),
    ).toEqual(["older"]);
    expect(
      retainedHistoryPrefix(loaded, [pending("latest-user", "latest")]),
    ).toEqual([]);
  });
});
