/**
 * ToolsTabs — the ACP tab answers to the ``acp`` module key.
 *
 * It used to be filtered on the ``admin`` role alone, which both hid it from a
 * granted account and said nothing about the key the route and the nav entry
 * read (design §4.4). The agent's own tool toggle behind it, and the runner
 * list, need only the key; the runner *definitions* stay an administrator's
 * and are refused inside the panel.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OctopUser } from "../../../api/modules/auth";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import ToolsTabs from "./ToolsTabs";

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ activeAgentId: "ag1" }),
}));

vi.mock("../../../api/modules/agentTools", () => ({
  agentToolsApi: {
    get: vi.fn(async () => ({ tools: [] })),
    patch: vi.fn(async () => ({ tools: [] })),
  },
}));

const ACP_TAB = "toolSettings.tabs.acp";
const BUILTIN_TAB = "toolSettings.tabs.builtin";

function renderTabs(user: OctopUser) {
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <ToolsTabs agentId="ag1" />
    </CurrentUserProvider>,
  );
}

describe("<ToolsTabs /> ACP tab", () => {
  it("shows it to the key's holder whatever the role", async () => {
    renderTabs({
      id: 1,
      username: "member",
      role: "user",
      display_name: null,
      locale: "zh",
      permissions: ["acp"],
    });
    expect(await screen.findByRole("tab", { name: ACP_TAB })).toBeInTheDocument();
  });

  it("hides it from an account without the key, the administrator aside", async () => {
    const { unmount } = renderTabs({
      id: 2,
      username: "other",
      role: "user",
      display_name: null,
      locale: "zh",
      permissions: ["terminal"],
    });
    // Wait for the panel behind the default tab to settle before asserting.
    await screen.findByRole("tab", { name: BUILTIN_TAB });
    expect(screen.queryByRole("tab", { name: ACP_TAB })).not.toBeInTheDocument();
    unmount();

    renderTabs({
      id: 3,
      username: "root",
      role: "admin",
      display_name: null,
      locale: "zh",
      permissions: [],
    });
    expect(await screen.findByRole("tab", { name: ACP_TAB })).toBeInTheDocument();
  });
});
