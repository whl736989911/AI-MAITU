/**
 * The chat sidebar's two halves.
 *
 * Both kinds arrive in ``agents`` (``selectEnabledExperts`` filters by state, not
 * by kind), so this list is where they are told apart: the features are grouped
 * under a heading, and the experts are left exactly as they were — same rows,
 * same order, no heading. A caller with no feature gets the list this sidebar has
 * always rendered.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it } from "vitest";
import type { OctopAgent } from "../../../context/AgentContext";
import SessionList from "./SessionList";

function agent(agentId: string, name: string, kind?: string): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    name,
    description: `${name} description`,
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
  } as OctopAgent;
}

const noop = () => undefined;

function renderSidebar(agents: OctopAgent[]) {
  return render(
    <MemoryRouter>
      <SessionList
        agents={agents}
        sessions={[]}
        activeId={null}
        activeAgentId={agents[0]?.agent_id ?? null}
        hasMore={false}
        loadingMore={false}
        onLoadMore={noop}
        onFetchAllSessions={noop}
        onSelect={noop}
        onAgentSelect={noop}
        onDelete={noop}
        onRename={noop}
        onPin={noop}
        onFork={noop}
      />
    </MemoryRouter>,
  );
}

describe("SessionList", () => {
  it("renders an expert-only list as the one group it has always been", () => {
    renderSidebar([
      agent("main", "通用助手", "agent"),
      agent("writer", "写作专家", "agent"),
    ]);

    expect(screen.getByText("通用助手")).toBeInTheDocument();
    expect(screen.getByText("写作专家")).toBeInTheDocument();
    expect(screen.queryByText("功能")).not.toBeInTheDocument();
  });

  it("groups the features under their own heading, below the experts", () => {
    renderSidebar([
      agent("main", "通用助手", "agent"),
      agent("feat-weekly", "周报生成", "feature"),
    ]);

    expect(screen.getByText("功能")).toBeInTheDocument();
    expect(screen.getByText("周报生成")).toBeInTheDocument();
    // Rendered as a row like any other — the feature's own description comes
    // from the same card the experts use.
    expect(screen.getByText("周报生成 description")).toBeInTheDocument();
  });
});
