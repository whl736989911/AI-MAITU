import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi, type Mock } from "vitest";
import { agentApi } from "../api/modules/agent";
import {
  AgentProvider,
  projectChatAgentOption,
  selectEnabledExperts,
  useAgent,
  type OctopAgent,
} from "./AgentContext";
const mockedAgentApi = agentApi as unknown as {
  list: Mock<() => Promise<OctopAgent[]>>;
};

beforeEach(() => {
  localStorage.clear();
  mockedAgentApi.list.mockReset();
});

describe("AgentProvider refresh ordering", () => {
  it("does not let an older agent refresh overwrite a newer response", async () => {
    let resolveOlder!: (agents: OctopAgent[]) => void;
    const olderRequest = new Promise<OctopAgent[]>((resolve) => {
      resolveOlder = resolve;
    });
    let resolveNewer!: (agents: OctopAgent[]) => void;
    const newerRequest = new Promise<OctopAgent[]>((resolve) => {
      resolveNewer = resolve;
    });
    mockedAgentApi.list
      .mockReturnValueOnce(olderRequest)
      .mockReturnValueOnce(newerRequest);

    const { result } = renderHook(() => useAgent(), { wrapper: AgentProvider });
    await waitFor(() => expect(mockedAgentApi.list).toHaveBeenCalledTimes(1));

    let newerRefresh!: Promise<void>;
    act(() => {
      newerRefresh = result.current.refresh({ silent: true });
    });
    await waitFor(() => expect(mockedAgentApi.list).toHaveBeenCalledTimes(2));

    await act(async () => {
      resolveNewer([agent("new-agent", "running")]);
      await newerRefresh;
    });
    await act(async () => {
      resolveOlder([agent("old-agent", "running")]);
      await olderRequest;
    });

    expect(result.current.agents.map((item) => item.agent_id)).toEqual([
      "new-agent",
    ]);
    expect(result.current.activeAgentId).toBe("new-agent");
  });
});

vi.mock("../api/modules/agent", () => ({
  agentApi: { list: vi.fn() },
}));

import { isFeatureAgent, KIND_FEATURE } from "../utils/agentKind";

function agent(
  agent_id: string,
  state: OctopAgent["state"],
  overrides: Partial<OctopAgent> = {},
): OctopAgent {
  return {
    id: 0,
    agent_id,
    name: agent_id,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state,
    last_error: null,
    icon: null,
    icon_name: null,
    icon_url: null,
    color: null,
    config: {},
    ...overrides,
  };
}

describe("selectEnabledExperts", () => {
  it("keeps only running experts when no resolved id is provided", () => {
    const agents = [
      agent("A", "running"),
      agent("B", "stopped"),
      agent("C", "starting"),
      agent("D", "failed"),
      agent("E", "stopping"),
    ];
    expect(selectEnabledExperts(agents, null).map((a) => a.agent_id)).toEqual([
      "A",
    ]);
  });

  it("hides general-assistant and system-doctor style experts once they are stopped", () => {
    // User's two scenarios from the bug report.
    const agents = [
      agent("G1", "stopped", { template_name: "general-assistant" }),
      agent("G2", "running", { template_name: "general-assistant" }),
      agent("S1", "stopped", { template_name: "cvm-ai-doctor" }),
      agent("S2", "running", { template_name: "cvm-ai-doctor" }),
    ];
    const result = selectEnabledExperts(agents, null).map((a) => a.agent_id);
    // Both templates follow the same rule: only running ones survive.
    expect(result).toEqual(["G2", "S2"]);
  });

  it("hides the focused expert once it is stopped (no pinning)", () => {
    // Regression for: stopping the focused expert in the chat page used to
    // leave it pinned in the sidebar because of an early pinActive default.
    const agents = [
      agent("S1", "stopped", { template_name: "cvm-ai-doctor" }),
      agent("G1", "running", { template_name: "general-assistant" }),
    ];
    // S1 is the resolvedAgentId (URL is /chat/S1). Without pinActive it must
    // disappear from the sidebar just like every other stopped expert.
    const sidebar = selectEnabledExperts(agents, "S1").map((a) => a.agent_id);
    expect(sidebar).toEqual(["G1"]);
    // @-picker / minimal-layout path also excludes it.
    const pickable = selectEnabledExperts(agents, "S1", {
      pinActive: false,
    }).map((a) => a.agent_id);
    expect(pickable).toEqual(["G1"]);
  });

  it("is a no-op when every expert is running", () => {
    const agents = [agent("A", "running"), agent("B", "running")];
    expect(selectEnabledExperts(agents, null)).toEqual(agents);
  });

  it("returns empty when nothing is running and there is no active pin", () => {
    const agents = [agent("A", "stopped"), agent("B", "failed")];
    expect(selectEnabledExperts(agents, null)).toEqual([]);
  });

  it("opt-in pinActive=true still keeps the active stopped expert (API stability)", () => {
    // Pinning is no longer the default — every production caller passes
    // pinActive:false — but the option still exists in case a future
    // surface wants to keep the focused expert visible.
    const agents = [
      agent("S1", "stopped", { template_name: "cvm-ai-doctor" }),
      agent("G1", "running", { template_name: "general-assistant" }),
    ];
    const result = selectEnabledExperts(agents, "S1", { pinActive: true }).map(
      (a) => a.agent_id,
    );
    expect(result).toEqual(["S1", "G1"]);
  });
});

describe("projectChatAgentOption", () => {
  it("projects the full OctopAgent down to ChatAgentOption shape", () => {
    const projected = projectChatAgentOption(
      agent("S1", "running", {
        icon_name: "cpu",
        icon_url: "https://example/icon.png",
        color: "#0052D9",
        is_shared: 1,
        is_owner: 0,
        owner_username: "alice",
      }),
    );
    expect(projected).toEqual({
      agent_id: "S1",
      name: "S1",
      icon_name: "cpu",
      icon_url: "https://example/icon.png",
      color: "#0052D9",
      is_shared: true,
      is_owner: false,
      owner_username: "alice",
      kind: null,
    });
  });

  it("normalizes undefined owner_username to null", () => {
    const projected = projectChatAgentOption(agent("X", "running"));
    expect(projected.owner_username).toBeNull();
    expect(projected.is_shared).toBe(false);
    expect(projected.is_owner).toBe(false);
  });

  it("keeps the kind, so a projected row is still told apart as a feature", () => {
    const feature = projectChatAgentOption(
      agent("feat-weekly", "running", { kind: KIND_FEATURE }),
    );
    expect(isFeatureAgent(feature)).toBe(true);
    // A row that says nothing is an expert — the same answer the row itself
    // gives (``utils/agentKind``).
    expect(isFeatureAgent(projectChatAgentOption(agent("S2", "running")))).toBe(
      false,
    );
  });
});
