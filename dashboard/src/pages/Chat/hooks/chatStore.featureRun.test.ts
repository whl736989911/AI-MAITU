/**
 * A submitted run is one ``feature_run`` field on one turn.
 *
 * The platform records the run and renders its values into that turn's prompt by
 * reading the run off the inbound message the dashboard's ``user_turn`` frame
 * becomes — so the frame's own field, and that it is sent once per run, is the
 * whole contract between the card and the backend. A turn that carries no run must
 * be untouched by it, and the run must not be smuggled into a bag the server
 * decides the contents of.
 */

import { afterEach, describe, expect, it, vi } from "vitest";

import {
  FEATURE_RUN_FRAME_KEY,
  type FeatureRunPayload,
} from "../utils/featureRun";
import { getSnapshot, removeSession, sendTurn } from "./chatStore";

vi.mock("../../../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

const SESSION = "test-feature-run";
const frames: Array<Record<string, unknown>> = [];

/** jsdom has no WebSocket, and the frame is the thing under test. */
class FakeWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  readyState = FakeWebSocket.OPEN;
  onopen: (() => void) | null = null;
  onmessage: ((event: { data: string }) => void) | null = null;
  onclose: (() => void) | null = null;
  onerror: (() => void) | null = null;

  constructor(readonly url: string) {
    // The handlers are assigned synchronously after construction; the frame goes
    // out on open, so opening has to happen after that assignment.
    queueMicrotask(() => this.onopen?.());
  }

  send(data: string): void {
    frames.push(JSON.parse(data) as Record<string, unknown>);
  }

  close(): void {
    this.readyState = FakeWebSocket.CLOSED;
  }
}

/** Send one turn, and wait for the frame it puts on the wire. */
async function sendAndReadFrame(featureRun?: FeatureRunPayload) {
  void sendTurn(
    SESSION,
    "运行工作流",
    "agent-feat",
    "sk",
    undefined,
    undefined,
    null,
    "thr-1",
    undefined,
    undefined,
    undefined,
    undefined,
    undefined,
    featureRun,
  );
  await vi.waitFor(() => expect(frames).toHaveLength(1));
  return frames[0];
}

describe("sendTurn with a submitted workflow run", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    removeSession(SESSION);
    frames.length = 0;
  });

  it("carries the run as the frame's own feature_run field, once", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    const payload: FeatureRunPayload = {
      inputs: { customer_name: "ACME", count: 3 },
      attachments: ["inbound/quote.pdf"],
    };

    const frame = await sendAndReadFrame(payload);

    expect(frame.type).toBe("user_turn");
    expect(frame[FEATURE_RUN_FRAME_KEY]).toEqual(payload);
    // One frame, one run: the turn is not repeated per field or per file.
    expect(frames).toHaveLength(1);
    // Not smuggled into the metadata bag the server decides the contents of.
    expect(frame.metadata).toBeUndefined();
  });

  it("leaves a turn with no run exactly as it was", async () => {
    vi.stubGlobal("WebSocket", FakeWebSocket);

    const frame = await sendAndReadFrame();

    expect(frame.type).toBe("user_turn");
    expect(frame[FEATURE_RUN_FRAME_KEY]).toBeUndefined();
    expect(frame.metadata).toBeUndefined();
    expect(getSnapshot(SESSION).isStreaming).toBe(true);
  });
});
