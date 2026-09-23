/**
 * The terminal page when the caller holds no expert.
 *
 * A terminal is rooted at one expert's workspace
 * (``WS /api/agents/{agent_id}/terminal/ws``), so with no expert the session had
 * no agent to open and ``useTerminal`` reported the tab as "disconnected" — the
 * page painted the reconnect card, a fault where there was merely nothing to
 * run. It now says what it is waiting for and offers the way to get one; a
 * caller who *does* hold an expert still gets the terminal surface, and a real
 * dropped connection still gets the reconnect card.
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { useEffect } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopAgent } from "../../../context/AgentContext";
import TerminalPage from "./index";
import { terminalStoreTestApi } from "./useTerminal.testUtils";

const navigateMock = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
}));

vi.mock("../../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));

vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));

/** xterm needs a real canvas; the page only asks the view to report ready. */
vi.mock("./components/TerminalView", () => ({
  default: function TerminalViewStub({ onReady }: { onReady?: () => void }) {
    useEffect(() => {
      onReady?.();
    }, [onReady]);
    return <div data-testid="terminal-view" />;
  },
}));

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

const held = vi.hoisted(() => ({
  agents: [] as OctopAgent[],
  loading: false,
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: held.agents,
    activeAgentId: held.agents[0]?.agent_id ?? null,
    loading: held.loading,
    refresh: () => undefined,
  }),
}));

function expert(agentId: string): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    name: "expert",
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: null,
    icon_url: null,
    color: null,
    config: {},
  };
}

describe("TerminalPage with no expert of the caller's", () => {
  beforeEach(() => {
    vi.stubGlobal("WebSocket", FakeWebSocket);
    navigateMock.mockClear();
    terminalStoreTestApi.reset();
    localStorage.removeItem("octop:terminal-sessions");
    held.agents = [];
    held.loading = false;
  });

  it("asks for an expert instead of showing a disconnected terminal", () => {
    render(<TerminalPage />);

    expect(screen.getByText("terminal.noExpertsTitle")).toBeInTheDocument();
    expect(screen.getByText("terminal.noExpertsHint")).toBeInTheDocument();
    expect(screen.queryByText("terminal.connectionLost")).toBeNull();
    expect(screen.queryByText("terminal.reconnect")).toBeNull();
    // No terminal can be opened, so no "new tab" affordance either.
    expect(screen.queryByText("terminal.newTab")).toBeNull();
    expect(screen.queryByTestId("terminal-view")).toBeNull();
  });

  it("points at the expert list to get one", () => {
    render(<TerminalPage />);

    fireEvent.click(
      screen.getByRole("button", { name: "terminal.noExpertsAction" }),
    );

    expect(navigateMock).toHaveBeenCalledWith("/experts");
  });

  it("renders the terminal surface, not the empty state, once an expert exists", () => {
    held.agents = [expert("a1")];

    render(<TerminalPage />);

    expect(screen.queryByText("terminal.noExpertsTitle")).toBeNull();
    expect(screen.getByTestId("terminal-view")).toBeInTheDocument();
  });
});
