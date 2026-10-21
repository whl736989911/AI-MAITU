import { afterEach, describe, expect, it, vi } from "vitest";
import {
  appendUserMessage,
  attachThread,
  getSnapshot,
  removeSession,
  sendTurn,
  setHistoryPage,
} from "./chatStore";

const SESSION = "test-fork-connecting";

class PendingWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: PendingWebSocket[] = [];
  readyState = PendingWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    PendingWebSocket.instances.push(this);
  }

  send(data: string): void {
    const frame = JSON.parse(data) as { type: string };
    const responses =
      frame.type === "user_turn"
        ? [{ type: "token", content: "replacement answer" }, { type: "done" }]
        : [{ type: "turn_status", active: false }];
    for (const response of responses) {
      this.onmessage?.({ data: JSON.stringify(response) });
    }
  }

  close(): void {
    if (this.readyState === PendingWebSocket.CLOSED) return;
    this.readyState = PendingWebSocket.CLOSED;
    queueMicrotask(() => this.onclose?.());
  }
}

describe("history attachment while a first turn is connecting", () => {
  afterEach(() => {
    removeSession(SESSION);
    vi.unstubAllGlobals();
    PendingWebSocket.instances.length = 0;
  });

  it("delivers the new answer without losing the copied branch history", async () => {
    vi.stubGlobal("WebSocket", PendingWebSocket);
    setHistoryPage(
      SESSION,
      [
        { id: "u1", role: "user", content: "prior question", timestamp: 1 },
        { id: "a1", role: "assistant", content: "prior answer", timestamp: 2 },
      ],
      { hasMore: false, nextOffset: 2 },
    );
    appendUserMessage(SESSION, {
      id: "u2",
      role: "user",
      content: "edited question",
      timestamp: 3,
    });

    const sent = sendTurn(
      SESSION,
      "edited question",
      "main",
      "",
      undefined,
      undefined,
      null,
      SESSION,
    );
    const attached = attachThread(SESSION, "main", SESSION);
    for (const socket of [...PendingWebSocket.instances]) {
      if (socket.readyState !== PendingWebSocket.CONNECTING) continue;
      socket.readyState = PendingWebSocket.OPEN;
      socket.onopen?.();
    }
    await Promise.all([sent, attached]);

    expect(
      getSnapshot(SESSION).messages.map(({ role, content }) => ({
        role,
        content,
      })),
    ).toEqual([
      { role: "user", content: "prior question" },
      { role: "assistant", content: "prior answer" },
      { role: "user", content: "edited question" },
      { role: "assistant", content: "replacement answer" },
    ]);
    expect(getSnapshot(SESSION).isStreaming).toBe(false);
  });
});
