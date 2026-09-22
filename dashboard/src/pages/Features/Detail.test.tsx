import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";
import type { Feature, FeatureMeta } from "../../api/modules/features";

/**
 * A feature's own page is the whole surface for that feature: running it, the
 * experts' personalization panels pointed at *its* agent, and the definition
 * itself. What is pinned here is that the page carries all of it, that the
 * panels configure the agent the server named, and that whatever cannot be
 * configured is said rather than offered as a control that could only be
 * refused.
 */

const {
  getFeature,
  getFeatureMeta,
  listRules,
  listCases,
  personalizeFeature,
  getAgentStatus,
} = vi.hoisted(() => ({
  getFeature: vi.fn(),
  getFeatureMeta: vi.fn(),
  listRules: vi.fn(),
  listCases: vi.fn(),
  personalizeFeature: vi.fn(),
  getAgentStatus: vi.fn(),
}));

vi.mock("../../api/modules/features", () => ({
  featuresApi: {
    getFeature,
    getFeatureMeta,
    listRules,
    listCases,
    personalizeFeature,
    listFeatures: vi.fn(),
    getFeatureCapabilities: vi.fn(),
    createFeature: vi.fn(),
    updateFeature: vi.fn(),
    deleteFeature: vi.fn(),
    runFeature: vi.fn(),
    finalizeTask: vi.fn(),
    promoteTask: vi.fn(),
    extractRules: vi.fn(),
    approveRule: vi.fn(),
    rejectRule: vi.fn(),
    submitRule: vi.fn(),
  },
}));

vi.mock("../../api/modules/octopAgents", () => ({
  octopAgentsApi: { getAgentStatus },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

// The panels are the experts' own components; which agent they are handed is
// this file's subject, so they are stubs that report what they received.
vi.mock("../Agent/Skills/components/SkillsTabs", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="skills">{`skills:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Tools/ToolsTabs", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="tools">{`tools:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Personalization/components/AgentPluginsPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="plugins">{`plugins:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Personalization/components/MBTISelector", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="mbti">{`mbti:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Personalization/components/AgentPersonaFiles", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="files">{`files:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Memory/MemoryPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="memory">{`memory:${agentId}`}</div>
  ),
}));
vi.mock("../Agent/Channels/ChannelsPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="channels">{`channels:${agentId}`}</div>
  ),
}));
vi.mock("../Experts/components/SubagentManager", () => ({
  default: ({
    agentId,
    agentState,
  }: {
    agentId: string;
    agentState?: string;
  }) => <div data-testid="subagents">{`subagents:${agentId}:${agentState}`}</div>,
}));

import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import FeatureDetailPage from "./Detail";

const ADMIN: OctopUser = {
  id: 1,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
};

const MEMBER: OctopUser = {
  id: 2,
  username: "li",
  role: "user",
  display_name: null,
  locale: "zh",
  permissions: ["features"],
};

const FEATURE: Feature = {
  id: "quote-draft",
  version: 1,
  label: { zh: "报价单草稿", en: "Quote draft" },
  description: { zh: "生成报价单", en: "Draft a quote" },
  icon_name: "receipt",
  color: null,
  unit: "sales",
  output_kind: "markdown",
  permissions: {},
  input_schema: {
    type: "object",
    properties: { customer: { type: "string" } },
    required: ["customer"],
  },
  ui_schema: { order: ["customer"] },
  user_template: "{{inputs}}",
  system_prompt: null,
  agent: null,
};

/** The agent the server materializes for ``FEATURE`` — never derived on the client. */
const AGENT_ID = "feat-quote-draft";

function metaWith(bundled: string[]): FeatureMeta {
  return {
    units: ["sales"],
    icons: ["receipt"],
    output_kinds: ["markdown", "json", "text"],
    bundled_ids: bundled,
  };
}

/** The feature's own route, plus the catalog its back button lands on. */
function renderDetail(user: OctopUser, path = "/features/quote-draft") {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <CurrentUserProvider user={user} setUser={vi.fn()}>
        <Routes>
          <Route path="/features" element={<div>feature-catalog</div>} />
          <Route path="/features/:id/*" element={<FeatureDetailPage />} />
        </Routes>
      </CurrentUserProvider>
    </MemoryRouter>,
  );
}

/** The run surface is what the page opens on, whatever tab was last used. */
async function waitForRunSurface() {
  return screen.findByText("features.formTitle", undefined, {
    timeout: 15_000,
  });
}

describe("<FeatureDetailPage /> configuration tabs", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A tab the previous case opened must not decide where this one starts.
    localStorage.clear();
    getFeature.mockResolvedValue(FEATURE);
    getFeatureMeta.mockResolvedValue(metaWith([]));
    listRules.mockResolvedValue({ feature_id: FEATURE.id, rules: [] });
    listCases.mockResolvedValue({ feature_id: FEATURE.id, cases: [] });
    personalizeFeature.mockResolvedValue({
      feature_id: FEATURE.id,
      agent_id: AGENT_ID,
      created: true,
    });
    getAgentStatus.mockResolvedValue({ agent_id: AGENT_ID, state: "stopped" });
  });

  it("offers the personalization set and the definition on the feature's page", async () => {
    renderDetail(ADMIN);

    await waitForRunSurface();
    // Everything an expert is configured with, plus the parts only a feature
    // has — one page, reached by clicking the feature in the catalog.
    expect(screen.getByText("features.tabRun")).toBeInTheDocument();
    expect(screen.getByText("features.tabPersonalization")).toBeInTheDocument();
    expect(
      screen.getByText("features.settingsTabDefinition"),
    ).toBeInTheDocument();
    expect(screen.getByText("features.settingsTabSteps")).toBeInTheDocument();
  });

  it("points the expert panels at the agent the server gave the feature", async () => {
    const user = userEvent.setup();
    renderDetail(ADMIN);

    await waitForRunSurface();
    expect(screen.queryByTestId("skills")).toBeNull();

    await user.click(screen.getByText("features.tabPersonalization"));

    // The agent is the one ``POST /features/{id}/agent`` answered with: the page
    // cannot fall back to the caller's own expert, because a feature's agent is
    // app-owned and is not in anyone's list.
    expect(await screen.findByTestId("skills")).toHaveTextContent(
      `skills:${AGENT_ID}`,
    );
    expect(personalizeFeature).toHaveBeenCalledWith("quote-draft");

    // And it is the same tab set an expert has, not a subset of it.
    expect(screen.getByText("personalization.tabs.tools")).toBeInTheDocument();
    expect(screen.getByText("personalization.tabs.mbti")).toBeInTheDocument();
    expect(screen.getByText("personalization.tabs.files")).toBeInTheDocument();

    await user.click(screen.getByText("personalization.tabs.tools"));
    expect(await screen.findByTestId("tools")).toHaveTextContent(
      `tools:${AGENT_ID}`,
    );
  });

  it("keeps the definition tabs off a feature nobody may write", async () => {
    renderDetail(MEMBER);

    await waitForRunSurface();
    expect(screen.getByText("features.tabRun")).toBeInTheDocument();
    expect(screen.queryByText("features.tabPersonalization")).toBeNull();
    expect(screen.queryByText("features.settingsTabDefinition")).toBeNull();
    // The definition format choices are a writer's call only.
    expect(getFeatureMeta).not.toHaveBeenCalled();
  });

  it("says a bundled feature cannot be configured instead of offering it", async () => {
    getFeatureMeta.mockResolvedValue(metaWith([FEATURE.id]));
    renderDetail(ADMIN);

    await waitForRunSurface();
    expect(screen.getByText("features.bundledNotice")).toBeInTheDocument();
    expect(screen.queryByText("features.tabPersonalization")).toBeNull();
    expect(screen.queryByText("features.settingsTabDefinition")).toBeNull();
    expect(personalizeFeature).not.toHaveBeenCalled();
  });

  it("lands a URL that names a tab nobody may show on the run surface", async () => {
    getFeatureMeta.mockResolvedValue(metaWith([FEATURE.id]));
    renderDetail(ADMIN, "/features/quote-draft/personalization/skills");

    // A bookmark, or the tab the previous visitor of this browser left open: an
    // empty pane would read as "this feature has nothing", so it does not stay.
    await waitForRunSurface();
    expect(screen.queryByTestId("skills")).toBeNull();
    expect(personalizeFeature).not.toHaveBeenCalled();
  });
});
