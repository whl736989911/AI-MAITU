/**
 * ``AgentSelector``'s half-of-the-fleet rule.
 *
 * The bar names the caller's experts by default; ``scope="features"`` names
 * their features instead. Both read the same pointer (``activeAgentId``), so the
 * one thing that has to hold is that neither bar pulls the page off the other
 * half's choice — and that the experts' bar still adopts a default when there is
 * no pointer at all, which is what every agent-scoped page has always done.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopAgent } from "../context/AgentContext";
import AgentSelector from "./AgentSelector";

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
  context.activeAgentId = null;
  context.loading = false;
});

describe("AgentSelector", () => {
  it("adopts the first expert when there is no pointer at all", () => {
    render(<AgentSelector />);

    expect(setActiveAgent).toHaveBeenCalledWith(EXPERT.agent_id);
  });

  it("leaves a page scoped to a feature alone", () => {
    // Only the features' bar can have put the pointer there, so the experts' bar
    // must not drag it back — and the features' bar must not adopt either.
    context.activeAgentId = FEATURE.agent_id;
    render(
      <>
        {/* The page's own pair (``AgentScopeBars``): one control, one rule, so
            the features' row is drawn exactly as the experts' row is. */}
        <AgentSelector />
        <AgentSelector scope="features" />
      </>,
    );

    expect(setActiveAgent).not.toHaveBeenCalled();
    // The features' bar names it; the experts' bar offers its own list and
    // claims none of it.
    expect(screen.getByText(FEATURE.name)).toBeInTheDocument();
    expect(screen.getByText(EXPERT.name)).toBeInTheDocument();
  });

  it("names nothing when the pointer is on the other half", () => {
    context.activeAgentId = FEATURE.agent_id;
    render(<AgentSelector variant="select" />);

    // No expert is claimed the way a selected option is — the bar shows its
    // placeholder instead of a row it does not hold.
    expect(screen.queryByText(EXPERT.name)).not.toBeInTheDocument();
  });

  it("offers only the caller's features on the features' bar", () => {
    context.activeAgentId = EXPERT.agent_id;
    render(<AgentSelector scope="features" variant="select" />);

    expect(screen.queryByText(EXPERT.name)).not.toBeInTheDocument();
    expect(
      screen.getByText("agentSelector.featurePlaceholder"),
    ).toBeInTheDocument();
  });
});
