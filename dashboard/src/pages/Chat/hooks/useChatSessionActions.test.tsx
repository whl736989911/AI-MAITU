import { renderHook, act } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, describe, expect, it, vi } from "vitest";
import type { ReactNode } from "react";
import { useChatSessionActions } from "./useChatSessionActions";
import {
  appendUserMessage,
  getSnapshot,
  removeSession,
  type ChatMessage,
} from "./chatStore";
import { EMPTY_CHAT_SESSION_KEY } from "../constants";

const navigateMock = vi.fn();
const renameMock = vi.fn();
const pinMock = vi.fn();
const errorToastMock = vi.fn();

vi.mock("react-router-dom", async () => {
  const actual = await vi.importActual<typeof import("react-router-dom")>(
    "react-router-dom",
  );
  return { ...actual, useNavigate: () => navigateMock };
});

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    rebind: vi.fn().mockResolvedValue({}),
    list: vi.fn().mockResolvedValue([]),
  },
}));

vi.mock("../../../utils/antdMessage", () => ({
  message: {
    error: (...args: unknown[]) => errorToastMock(...args),
    success: vi.fn(),
  },
}));

function wrapper({ children }: { children: ReactNode }) {
  return <MemoryRouter>{children}</MemoryRouter>;
}

const THREAD = "thr_leaving";

function makeMessage(id: string): ChatMessage {
  return { id, role: "user", content: `message ${id}`, timestamp: Date.now() };
}

function renderActions() {
  return renderHook(
    () =>
      useChatSessionActions({
        resolvedAgentId: "agent-a",
        activeThreadId: THREAD,
        sessions: [],
        isMobile: false,
        setActiveAgent: vi.fn(),
        setSidebarOpen: vi.fn(),
        setSelectedModel: vi.fn(),
        setHasBrowserTool: vi.fn(),
        deleteSession: vi.fn().mockResolvedValue(true),
        renameSession: (...args: unknown[]) => renameMock(...args),
        pinSession: (...args: unknown[]) => pinMock(...args),
        clearMessages: vi.fn(),
        resetNavForAgentSwitch: vi.fn(),
        markInitialNavDone: vi.fn(),
      }),
    { wrapper },
  );
}

describe("rename / pin failure reporting", () => {
  afterEach(() => {
    vi.clearAllMocks();
  });

  it("surfaces a rename the server rejected", async () => {
    renameMock.mockResolvedValue(false);
    const { result } = renderActions();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.handleRenameSession(THREAD, "Renamed locally");
    });

    expect(stored).toBe(false);
    expect(errorToastMock).toHaveBeenCalledTimes(1);
    expect(errorToastMock.mock.calls[0][0]).toBe("chat.renameFailed");
  });

  it("surfaces a pin the server rejected", async () => {
    pinMock.mockResolvedValue(false);
    const { result } = renderActions();

    let stored: boolean | undefined;
    await act(async () => {
      stored = await result.current.handlePinSession(THREAD, true);
    });

    expect(stored).toBe(false);
    expect(errorToastMock).toHaveBeenCalledTimes(1);
    expect(errorToastMock.mock.calls[0][0]).toBe("chat.pinFailed");
  });

  it("stays quiet when the server stored the change", async () => {
    renameMock.mockResolvedValue(true);
    pinMock.mockResolvedValue(true);
    const { result } = renderActions();

    let renamed: boolean | undefined;
    let pinned: boolean | undefined;
    await act(async () => {
      renamed = await result.current.handleRenameSession(THREAD, "Renamed on server");
      pinned = await result.current.handlePinSession(THREAD, true);
    });

    expect(renamed).toBe(true);
    expect(pinned).toBe(true);
    expect(errorToastMock).not.toHaveBeenCalled();
  });
});

describe("navigateToAgent", () => {
  afterEach(() => {
    removeSession(THREAD);
    removeSession(EMPTY_CHAT_SESSION_KEY);
    vi.clearAllMocks();
  });

  it("clears the new-chat view without wiping the thread being left", async () => {
    appendUserMessage(THREAD, makeMessage("m1"));
    appendUserMessage(EMPTY_CHAT_SESSION_KEY, makeMessage("draft"));

    const { result } = renderActions();
    await act(async () => {
      result.current.navigateToAgent("agent-b");
    });

    expect(getSnapshot(THREAD).messages).toHaveLength(1);
    expect(getSnapshot(EMPTY_CHAT_SESSION_KEY).messages).toHaveLength(0);
  });
});
