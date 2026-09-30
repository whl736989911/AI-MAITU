/**
 * ACPPanel — who may edit the global runner definitions.
 *
 * ``PUT /api/acp`` is a system-administrator write on top of the ``acp`` key
 * (design §4.4), so the create button, the per-card enable switch and the
 * editor drawer are the administrator's. The runner list itself is readable
 * with ``acp``, and the per-agent tool switch needs that key only — it must
 * not follow the role, or a granted non-administrator would lose their own
 * agent's tool.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App } from "antd";
import type { OctopUser } from "../../../api/modules/auth";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import { ACPPanel } from "./index";

const agentMocks = vi.hoisted(() => ({
  agents: [] as Array<{ agent_id: string; config: Record<string, unknown> }>,
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ activeAgentId: "ag1", agents: agentMocks.agents }),
}));

vi.mock("../../../api/modules/acp", () => {
  // Stable payload references: the panel's fetch effects list ``t`` in their
  // deps, and the test i18n mock hands out a fresh ``t`` per render, so a mock
  // that returned a new object each call would re-render and re-fetch forever.
  const runners = { opencode: { command: "opencode acp", enabled: true } };
  const list = { runners };
  return {
    acpApi: {
      getGlobalRunners: vi.fn(async () => list),
      getConfig: vi.fn(async () => ({ tool_enabled: false })),
      updateGlobalRunners: vi.fn(async () => list),
      updateToolEnabled: vi.fn(async () => ({ tool_enabled: true })),
    },
  };
});

/** Holds the key; the role-only gate on the definition writes still refuses. */
const GRANTED: OctopUser = {
  id: 1,
  username: "member",
  role: "user",
  display_name: null,
  locale: "zh",
  permissions: ["acp"],
};

const ADMIN: OctopUser = {
  id: 2,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
  permissions: ["acp"],
};

function renderPanel(user: OctopUser) {
  return render(
    <App>
      <CurrentUserProvider user={user} setUser={() => undefined}>
        <ACPPanel />
      </CurrentUserProvider>
    </App>,
  );
}

/** The card's top row — icon, label, and the enable switch when there is one. */
function runnerCardTop(): HTMLElement {
  const top = screen.getByText("acp.runner_opencode").closest("div");
  expect(top).not.toBeNull();
  return top as HTMLElement;
}

/** The per-agent tool row, told apart from the cards' switches by its label. */
function agentToolSwitch(): HTMLElement {
  const row = screen.getByText("acp.toolEnabled").parentElement;
  expect(row).not.toBeNull();
  return within(row as HTMLElement).getByRole("switch");
}

beforeEach(() => {
  agentMocks.agents = [];
});

describe("<ACPPanel /> runner definitions", () => {
  it("leaves the definitions to a non-administrator as read-only cards", async () => {
    renderPanel(GRANTED);

    // The list is readable, and says who may change it.
    await screen.findByText("acp.runner_opencode");
    expect(screen.getByText("common.adminRequired")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "acp.create" }),
    ).not.toBeInTheDocument();
    // The card carries no enable switch — the agent's tool switch is the one
    // control left, and it answers to the key, not to the role.
    expect(within(runnerCardTop()).queryByRole("switch")).toBeNull();
    expect(screen.getAllByRole("switch")).toEqual([agentToolSwitch()]);

    // Clicking a card is information, not an editor: the drawer stays shut.
    fireEvent.click(screen.getByText("acp.runner_opencode"));
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("gives an administrator the create button, the card switch and the drawer", async () => {
    renderPanel(ADMIN);

    await screen.findByText("acp.runner_opencode");
    expect(
      screen.getByRole("button", { name: "acp.create" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("common.adminRequired")).not.toBeInTheDocument();
    expect(within(runnerCardTop()).getByRole("switch")).toBeInTheDocument();

    fireEvent.click(screen.getByText("acp.runner_opencode"));
    expect(await screen.findByRole("dialog")).toBeInTheDocument();
  });

  it("blocks enabling outbound ACP for sandboxed agents", async () => {
    agentMocks.agents = [
      {
        agent_id: "ag1",
        config: { backend: { type: "named", name: "sandbox" } },
      },
    ];
    renderPanel(ADMIN);

    expect(
      await screen.findByText("acp.outboundBlockedHint"),
    ).toBeInTheDocument();
    await screen.findByText("acp.runner_opencode");
    await waitFor(() => expect(agentToolSwitch()).toBeDisabled());
  });
});
