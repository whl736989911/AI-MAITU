import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  resetSessionStoreForTests,
  sortSessions,
  toSession,
  useSessions,
  type Session,
} from "./useSessions";

const listMock = vi.fn();
const patchMock = vi.fn();
const renameMock = vi.fn();

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    list: (...args: unknown[]) => listMock(...args),
    create: vi.fn(),
    delete: vi.fn(),
    patch: (...args: unknown[]) => patchMock(...args),
    rename: (...args: unknown[]) => renameMock(...args),
    rebind: vi.fn(),
  },
}));

/** Same shape ``request()`` throws for a rejected PATCH on a stale thread id. */
function threadNotFoundError() {
  return new Error(
    'Request failed: 404 Not Found - {"error":{"code":"NOT_FOUND","message":"thread not found"}}',
  );
}

function threadRow(threadId: string, agentExtra?: Partial<{ title: string }>) {
  return {
    thread_id: threadId,
    title: agentExtra?.title ?? null,
    last_active: 1,
    created_at: 1,
    channel_type: "dashboard",
    is_active: false,
    has_messages: true,
    pinned: false,
  };
}

describe("toSession / sortSessions ordering", () => {
  it("sorts empty new chats by created_at above older active ones", () => {
    const olderActive = toSession({
      thread_id: "thr_old",
      title: "old",
      last_active: 100,
      created_at: 10,
      has_messages: true,
    });
    const emptyNew = toSession({
      thread_id: "thr_new",
      title: null,
      last_active: 0,
      created_at: 200,
      has_messages: false,
    });
    expect(
      sortSessions([olderActive, emptyNew]).map((s: Session) => s.id),
    ).toEqual(["thr_new", "thr_old"]);
  });

  it("keeps pinned sessions first", () => {
    const pinned = toSession({
      thread_id: "thr_pin",
      title: "pin",
      last_active: 1,
      created_at: 1,
      pinned: true,
    });
    const recent = toSession({
      thread_id: "thr_recent",
      title: "recent",
      last_active: 999,
      created_at: 999,
    });
    expect(sortSessions([recent, pinned]).map((s) => s.id)).toEqual([
      "thr_pin",
      "thr_recent",
    ]);
  });
});

describe("useSessions agent switch", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock.mockReset();
  });

  afterEach(() => {
    resetSessionStoreForTests();
  });

  it("clears previous-agent threads on the first render of a new agent", async () => {
    listMock.mockImplementation(async (agentId: string) => {
      if (agentId === "agent-a") {
        return [threadRow("thr_from_a")];
      }
      // Keep B's fetch pending so we can observe the sync clear.
      return new Promise(() => {});
    });

    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) => useSessions(agentId),
      { initialProps: { agentId: "agent-a" } },
    );

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_from_a"]);
    });

    rerender({ agentId: "agent-b" });

    // Critical: no one-frame leak of agent-a's thr_* into agent-b.
    expect(result.current.sessions).toEqual([]);
    expect(result.current.loading).toBe(true);
    expect(result.current.sessions.some((s) => s.id === "thr_from_a")).toBe(
      false,
    );
  });

  it("ensureThreadInList returns unknown on probe network errors", async () => {
    listMock
      .mockResolvedValueOnce([]) // initial fetch for agent
      .mockRejectedValueOnce(new Error("network down")); // probe

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("unknown");
  });

  it("ensureThreadInList returns missing when probe confirms absence", async () => {
    listMock.mockResolvedValue([]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_foreign");
    });
    expect(probe).toBe("missing");
  });

  it("ensureThreadInList returns found when the thread is already listed", async () => {
    listMock.mockResolvedValue([threadRow("thr_ok")]);

    const { result } = renderHook(() => useSessions("agent-new"));

    await waitFor(() => {
      expect(result.current.loading).toBe(false);
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_ok"]);
    });

    let probe: string | undefined;
    await act(async () => {
      probe = await result.current.ensureThreadInList("thr_ok");
    });
    expect(probe).toBe("found");
    expect(listMock).toHaveBeenCalledTimes(1);
  });
});

describe("useSessions rename / pin persistence", () => {
  beforeEach(() => {
    resetSessionStoreForTests();
    listMock.mockReset();
    patchMock.mockReset();
    renameMock.mockReset();
    listMock.mockResolvedValue([threadRow("thr_keep", { title: "Keep me" })]);
  });

  afterEach(() => {
    resetSessionStoreForTests();
  });

  async function mountWithThread() {
    const { result } = renderHook(() => useSessions("agent-a"));
    await waitFor(() => {
      expect(result.current.sessions.map((s) => s.id)).toEqual(["thr_keep"]);
    });
    return result;
  }

  it("reports a rejected rename as not stored", async () => {
    renameMock.mockRejectedValue(threadNotFoundError());
    const result = await mountWithThread();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.renameSession("thr_keep", "Renamed locally");
    });

    expect(stored).toBe(false);
  });

  it("drops a rejected rename from the list", async () => {
    renameMock.mockRejectedValue(threadNotFoundError());
    const result = await mountWithThread();

    await act(async () => {
      await result.current.renameSession("thr_keep", "Renamed locally");
    });

    expect(result.current.sessions[0].name).toBe("Keep me");
  });

  it("reports a rejected pin as not stored", async () => {
    patchMock.mockRejectedValue(threadNotFoundError());
    const result = await mountWithThread();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.pinSession("thr_keep", true);
    });

    expect(stored).toBe(false);
  });

  it("drops a rejected pin from the list", async () => {
    patchMock.mockRejectedValue(threadNotFoundError());
    const result = await mountWithThread();

    await act(async () => {
      await result.current.pinSession("thr_keep", true);
    });

    expect(result.current.sessions[0].pinned).toBe(false);
  });

  it("keeps a rename the server stored", async () => {
    renameMock.mockResolvedValue({});
    const result = await mountWithThread();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.renameSession("thr_keep", "Renamed on server");
    });

    expect(stored).toBe(true);
    expect(result.current.sessions[0].name).toBe("Renamed on server");
  });

  it("keeps a pin the server stored", async () => {
    patchMock.mockResolvedValue({});
    const result = await mountWithThread();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.pinSession("thr_keep", true);
    });

    expect(stored).toBe(true);
    expect(result.current.sessions[0].pinned).toBe(true);
  });
});
