/**
 * The two rows an agent-scoped page draws, as one control.
 *
 * The automation page, the personalization page and an ACP page show the experts'
 * row and the features' row together, and the two are the same control drawn the
 * same way — a page whose halves are small enough for a row of chips gets chips on
 * *both* rows (``AgentSelector``'s own rule), and neither row is told which variant
 * to draw. What this file holds is that shape: the features' row is not, say, a
 * dropdown while the experts' row is chips, because a caller comparing the two rows
 * is comparing one question's two halves.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopAgent } from "../context/AgentContext";
import AgentScopeBars from "./AgentScopeBars";

const setActiveAgent = vi.fn();
const context: {
  agents: OctopAgent[];
  activeAgentId: string | null;
  loading: boolean;
} = { agents: [], activeAgentId: null, loading: false };

vi.mock("../context/AgentContext", () => ({
  useAgent: () => ({ ...context, setActiveAgent }),
}));

function agent(
  agentId: string,
  name: string,
  kind: string | undefined,
): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    name,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: "sparkles",
    icon_url: null,
    color: null,
    config: {},
    is_shared: false,
    is_owner: true,
  };
}

const EXPERT = agent("main", "通用助手", "agent");
const FEATURE = agent("feat-weekly", "周报生成", "feature");

beforeEach(() => {
  setActiveAgent.mockClear();
  context.agents = [EXPERT, FEATURE];
  context.activeAgentId = EXPERT.agent_id;
  context.loading = false;
});

describe("AgentScopeBars", () => {
  it("draws both halves with the one control, not one of each", () => {
    const { container } = render(<AgentScopeBars />);

    // One chip row per half — the experts' and the features' — and no half behind
    // a dropdown, which is the inconsistency a caller would read as two controls.
    expect(container.querySelectorAll('[role="tablist"]')).toHaveLength(2);
    expect(screen.queryByRole("combobox")).toBeNull();
    expect(screen.getByText(EXPERT.name)).toBeInTheDocument();
    expect(screen.getByText(FEATURE.name)).toBeInTheDocument();
  });
});
