/**
 * A terminal tab with no expert is not a dropped connection.
 *
 * ``connect`` answered "no agent to open" with ``disconnected`` — the very state
 * the terminal page paints its "connection lost / reconnect" card from — so a
 * caller who holds no expert saw a fault where there was merely nothing to run.
 * The page owns that case now (its "create an expert first" empty state), and a
 * tab with nothing to connect to must not report a connection failure.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { useTerminal, type TerminalConnState } from "./useTerminal";
import { terminalStoreTestApi } from "./useTerminal.testUtils";

/** jsdom's WebSocket would dial out and schedule reconnects after the test. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = FakeWebSocket.CONNECTING;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {}

  send(): void {}

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

describe("useTerminal with no expert assigned", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    terminalStoreTestApi.reset();
    localStorage.removeItem("octop:terminal-sessions");
  });

  it("reports no disconnect for a tab that has no agent to connect to", () => {
    const reported: TerminalConnState[] = [];
    const { result } = renderHook(() => useTerminal());
    let sessionId = "";
    act(() => {
      sessionId = result.current.createSession();
    });

    act(() => {
      result.current.connect(sessionId, "", {
        onOutput: () => undefined,
        onStateChange: (state) => reported.push(state),
      });
    });

    expect(reported).not.toContain("disconnected");
    expect(result.current.getConnState(sessionId)).not.toBe("disconnected");
  });
});
