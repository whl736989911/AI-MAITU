import { afterEach, describe, expect, it, vi } from "vitest";
import { getSnapshot, removeSession, resumeHitl } from "./chatStore";

vi.mock("../../../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

const SESSION = "test-hitl-resume";

describe("resumeHitl", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
    removeSession(SESSION);
  });

  it("renders resumed tokens and notifies after the completed stream", async () => {
    const body = [
      'event: chunk\ndata: {"type":"token","content":"completed answer"}\n\n',
      'event: chunk\ndata: {"type":"done"}\n\n',
    ].join("");
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(
        new Response(body, {
          status: 200,
          headers: { "Content-Type": "text/event-stream" },
        }),
      ),
    );
    const onStreamEnd = vi.fn();

    const fetchMock = vi.mocked(fetch);
    await resumeHitl(
      SESSION,
      "agent-1",
      "thread-1",
      "pending-1",
      [{ type: "respond", message: "answer" }],
      onStreamEnd,
    );

    const requestBody = JSON.parse(String(fetchMock.mock.calls[0]?.[1]?.body));
    expect(requestBody.pending_id).toBe("pending-1");

    expect(getSnapshot(SESSION).messages.at(-1)?.content).toBe(
      "completed answer",
    );
    expect(getSnapshot(SESSION).isStreaming).toBe(false);
    expect(onStreamEnd).toHaveBeenCalledOnce();
  });
});
