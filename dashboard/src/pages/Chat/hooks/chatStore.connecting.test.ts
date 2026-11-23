import { afterEach, describe, expect, it, vi } from "vitest";
import {
  appendUserMessage,
  attachThread,
  getSnapshot,
  hasLiveSocket,
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
  deferTurn = false;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    PendingWebSocket.instances.push(this);
  }

  received(frame: object): void {
    this.onmessage?.({ data: JSON.stringify(frame) });
  }

  send(data: string): void {
    const frame = JSON.parse(data) as { type: string };
    if (this.deferTurn && frame.type === "user_turn") return;
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

class FailedWebSocket extends PendingWebSocket {
  constructor(url: string) {
    super(url);
    this.close();
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

  it("ignores frames and close events from a replaced socket", async () => {
    vi.stubGlobal("WebSocket", PendingWebSocket);
    const firstAttach = attachThread(SESSION, "agent-1", "thread-one");
    const first = PendingWebSocket.instances[0];
    first.send = () => {};
    first.readyState = PendingWebSocket.OPEN;
    first.onopen?.();
    await firstAttach;

    const secondAttach = attachThread(SESSION, "agent-1", "thread-two");
    const second = PendingWebSocket.instances[1];
    second.send = () => {};
    second.readyState = PendingWebSocket.OPEN;
    second.onopen?.();
    await secondAttach;
    await Promise.resolve();

    first.onmessage?.({
      data: JSON.stringify({ type: "token", content: "stale frame" }),
    });

    expect(getSnapshot(SESSION).messages).toEqual([]);
    expect(hasLiveSocket(SESSION)).toBe(true);
  });

  it("ignores turn completion from an older logical turn", async () => {
    vi.stubGlobal("WebSocket", PendingWebSocket);
    const attach = attachThread(SESSION, "agent-1", "thread-one");
    const socket = PendingWebSocket.instances[0];
    socket.send = () => {};
    socket.readyState = PendingWebSocket.OPEN;
    socket.onopen?.();
    await attach;
    socket.onmessage?.({
      data: JSON.stringify({ type: "turn.started", turn_id: "current-turn" }),
    });
    socket.onmessage?.({
      data: JSON.stringify({ type: "turn.ended", turn_id: "old-turn" }),
    });

    expect(getSnapshot(SESSION).activeTurnId).toBe("current-turn");
  });

  it("surfaces a connection failure when the socket closes before opening", async () => {
    vi.stubGlobal("WebSocket", FailedWebSocket);

    await sendTurn(
      SESSION,
      "question",
      "agent-1",
      "",
      undefined,
      undefined,
      null,
      SESSION,
    );

    const snapshot = getSnapshot(SESSION);
    expect(snapshot.isStreaming).toBe(false);
    expect(snapshot.messages.at(-1)).toMatchObject({
      role: "assistant",
      status: "error",
      errorInfo: { code: "ws_error" },
    });
  });

  it("shows every live result without borrowing a different tool's call", async () => {
    vi.stubGlobal("WebSocket", PendingWebSocket);
    const turn = sendTurn(
      SESSION,
      "check",
      "main",
      "",
      undefined,
      undefined,
      null,
      SESSION,
    );
    const socket = PendingWebSocket.instances[0];
    socket.deferTurn = true;
    socket.readyState = PendingWebSocket.OPEN;
    socket.onopen?.();
    socket.received({ type: "tool_call_chunk", id: "a", name: "read_file" });
    socket.received({
      type: "tool_call_chunk",
      id: "b",
      index: 1,
      name: "web_fetch",
    });
    socket.received({
      type: "tool_result",
      messages: [
        {
          tool_call_id: "c",
          name: "current_time",
          content: "missing start frame",
        },
        { tool_call_id: "b", status: "error", content: "fetch refused" },
      ],
    });
    const rows = () =>
      getSnapshot(SESSION).messages.filter((message) => message.toolData);
    expect(
      rows().map(({ status, toolData }) => [
        toolData?.callId,
        status,
        toolData?.output,
      ]),
    ).toEqual([
      ["a", "streaming", undefined],
      ["b", "error", "fetch refused"],
      ["c", "done", "missing start frame"],
    ]);
    socket.received({
      type: "tool_result",
      messages: [{ tool_call_id: "a", content: "file read" }],
    });
    expect(rows()[0].toolData?.output).toBe("file read");
    expect(getSnapshot(SESSION).isStreaming).toBe(true);
    socket.received({ type: "done" });
    await turn;
    expect(getSnapshot(SESSION).isStreaming).toBe(false);
  });
});
