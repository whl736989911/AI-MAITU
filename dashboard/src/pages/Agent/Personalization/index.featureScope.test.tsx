import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";

/**
 * The Personalization page's feature scope (design 5.1).
 *
 * Every panel on this page is parameterised on one agent id, so the whole
 * feature is "which id do they get": the caller's own expert by default, and
 * that feature's agent once one is chosen in the feature row. Three things are
 * pinned here and nowhere else —
 *
 *   - the id comes from the server (``POST /features/{id}/agent``), never from
 *     the definition id spelled out on the client;
 *   - the feature's agent is app-owned, so it is **not** in ``agents``; if the
 *     panels fell back to the active expert when it could not be opened, they
 *     would quietly configure the wrong agent — the failure has to be a screen;
 *   - the persona files tab belongs to that scope, and is not offered without it.
 */

const { listFeatures, personalizeFeature, getFeatureMeta, getAgentStatus } =
  vi.hoisted(() => ({
    listFeatures: vi.fn(),
    personalizeFeature: vi.fn(),
    getFeatureMeta: vi.fn(),
    getAgentStatus: vi.fn(),
  }));

vi.mock("../../../api/modules/features", () => ({
  featuresApi: { listFeatures, personalizeFeature, getFeatureMeta },
}));
vi.mock("../../../api/modules/octopAgents", () => ({
  octopAgentsApi: { getAgentStatus },
}));

// The panels are the experts' own components; which agent they are handed is
// this file's subject, so they are stubs that report what they received.
vi.mock("../Skills/components/SkillsTabs", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="skills">{`skills:${agentId}`}</div>
  ),
}));
vi.mock("../Tools/ToolsTabs", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="tools">{`tools:${agentId}`}</div>
  ),
}));
vi.mock("./components/AgentPluginsPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="plugins">{`plugins:${agentId}`}</div>
  ),
}));
vi.mock("../Memory/MemoryPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="memory">{`memory:${agentId}`}</div>
  ),
}));
vi.mock("../Channels/ChannelsPanel", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="channels">{`channels:${agentId}`}</div>
  ),
}));
vi.mock("../../Experts/components/SubagentManager", () => ({
  default: ({
    agentId,
    agentState,
  }: {
    agentId: string;
    agentState?: string;
  }) => <div data-testid="subagents">{`subagents:${agentId}:${agentState}`}</div>,
}));
vi.mock("./components/MBTISelector", () => ({
  default: ({ agentId }: { agentId: string | null }) => (
    <div data-testid="mbti">{`mbti:${agentId}`}</div>
  ),
}));
vi.mock("./components/AgentPersonaFiles", () => ({
  default: ({ agentId }: { agentId: string }) => (
    <div data-testid="files">{`files:${agentId}`}</div>
  ),
}));
vi.mock("./components/FeatureScopeBar", () => ({
  // The bar's own chrome is not this file's subject; it only has to be handed
  // the selection, and to be able to change it.
  default: ({
    features,
    selected,
    onSelect,
  }: {
    features: { id: string; label: string }[];
    selected: string | null;
    onSelect: (id: string | null) => void;
  }) => (
    <div data-testid="scope-bar">
      {`selected:${selected}`}
      {features.map((feature) => (
        <button
          key={feature.id}
          type="button"
          onClick={() => onSelect(feature.id)}
        >
          {`pick:${feature.id}`}
        </button>
      ))}
      <button type="button" onClick={() => onSelect(null)}>
        pick:none
      </button>
    </div>
  ),
}));

import type { OctopUser } from "../../../api/modules/auth";

const user: OctopUser = {
  id: 1,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
  permissions: ["features"],
};

// ``useAgent`` is the caller's own expert list — a feature's agent is app-owned
// and never appears in it, which is the whole point of this file.
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    activeAgentId: "my-expert",
    agents: [{ agent_id: "my-expert", state: "running" }],
  }),
}));

import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import PersonalizationPage from "./index";

vi.setConfig({ testTimeout: 30_000 });

const FEATURES = {
  features: [
    {
      id: "weekly-report",
      version: 1,
      label: { zh: "周报", en: "Weekly report" },
      description: { zh: "", en: "" },
      icon_name: "clipboard-list",
      color: null,
      unit: "general",
      output_kind: "markdown" as const,
      permissions: {},
    },
  ],
  units: [],
};

const AGENT_ID = "feat-weekly-report";

/** One definition this instance ships: readable, and never personalizable. */
const BUNDLED_ID = "meeting-notes";

/** The catalog plus a shipped definition — what ``GET /features`` answers. */
function catalogWithShipped() {
  return {
    features: [
      ...FEATURES.features,
      {
        ...FEATURES.features[0],
        id: BUNDLED_ID,
        label: { zh: "会议纪要", en: "Meeting notes" },
      },
    ],
    units: [],
  };
}

/** ``GET /features/_meta``: the same writability test the server refuses on. */
const META = {
  units: [],
  icons: [],
  output_kinds: ["markdown", "json", "text"],
  bundled_ids: [] as string[],
};

/** A caller who holds ``features`` without being an administrator. */
const FEATURES_USER: OctopUser = { ...user, username: "member1", role: "user" };

function renderPage(path = "/personalization/skills", as: OctopUser = user) {
  return render(
    <MemoryRouter initialEntries={[path]}>
      <Routes>
        <Route
          path="/personalization/*"
          element={
            <CurrentUserProvider user={as} setUser={vi.fn()}>
              <PersonalizationPage />
            </CurrentUserProvider>
          }
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("<PersonalizationPage /> feature scope", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    listFeatures.mockResolvedValue(FEATURES);
    getFeatureMeta.mockResolvedValue(META);
    personalizeFeature.mockResolvedValue({
      feature_id: "weekly-report",
      agent_id: AGENT_ID,
      created: true,
    });
    getAgentStatus.mockResolvedValue({ state: "running", last_error: null });
  });

  it("configures the caller's own expert until a feature is chosen", async () => {
    renderPage();

    expect(await screen.findByTestId("skills")).toHaveTextContent(
      "skills:my-expert",
    );
    // Nothing is materialized while the page is on its ordinary scope.
    expect(personalizeFeature).not.toHaveBeenCalled();
    // The persona files are a feature's; without one the tab is not offered.
    expect(screen.queryByText("personalization.tabs.files")).toBeNull();
  });

  it("points every panel at the feature's own agent once one is selected", async () => {
    const user1 = userEvent.setup();
    renderPage();

    await user1.click(await screen.findByText("pick:weekly-report"));

    expect(await screen.findByTestId("skills")).toHaveTextContent(
      `skills:${AGENT_ID}`,
    );
    expect(personalizeFeature).toHaveBeenCalledWith("weekly-report");
    // The state the subagent manager refuses installs without is the server's
    // own record of the agent, not ``undefined`` from the expert list.
    await user1.click(screen.getByText("personalization.tabs.subagents"));
    expect(await screen.findByTestId("subagents")).toHaveTextContent(
      `subagents:${AGENT_ID}:running`,
    );
  });

  it("shows the refusal instead of panels pointed at the wrong agent", async () => {
    personalizeFeature.mockRejectedValue(new Error("agent will not start"));
    renderPage("/personalization/skills?feature=weekly-report");

    expect(
      await screen.findByText("features.scopeFeatureAgentFailed"),
    ).toBeInTheDocument();
    // Not one panel mounted: an unanswered scope must not degrade into the
    // caller's own expert looking like the feature's configuration.
    expect(screen.queryByTestId("skills")).toBeNull();
    expect(screen.queryByTestId("subagents")).toBeNull();
  });

  it("says the feature agent's memory is shared with every caller, and only there", async () => {
    const user1 = userEvent.setup();
    renderPage("/personalization/memory");

    // On the caller's own expert the memory is theirs alone, so there is nothing
    // to warn about — the same rule the MBTI note follows.
    expect(await screen.findByTestId("memory")).toBeInTheDocument();
    expect(screen.queryByText("personalization.memorySharedNote")).toBeNull();

    await user1.click(await screen.findByText("pick:weekly-report"));

    // One agent, one MEMORY.md, every caller: the panel stays usable, but it
    // must not read as "my own memory".
    expect(
      await screen.findByText("personalization.memorySharedNote"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("memory")).toHaveTextContent(`memory:${AGENT_ID}`);
  });

  it("edits the feature's persona files from the tab that scope adds", async () => {
    const user1 = userEvent.setup();
    renderPage("/personalization/files?feature=weekly-report");

    expect(await screen.findByTestId("files")).toHaveTextContent(
      `files:${AGENT_ID}`,
    );
    await waitFor(() => expect(personalizeFeature).toHaveBeenCalledOnce());
  });

  /**
   * The row and ``POST /features/{id}/agent`` name the same set. The call
   * refuses a bundled definition (403, ``reason: "bundled"``), so the definition
   * must not be in the picker either: an option whose only outcome is a refusal
   * is a dead end, not a choice.
   */
  it("does not offer a definition this instance ships", async () => {
    listFeatures.mockResolvedValue(catalogWithShipped());
    getFeatureMeta.mockResolvedValue({ ...META, bundled_ids: [BUNDLED_ID] });
    renderPage();

    // The user's own definition is offered — the row is not simply empty.
    expect(await screen.findByText("pick:weekly-report")).toBeInTheDocument();
    expect(screen.queryByText(`pick:${BUNDLED_ID}`)).toBeNull();
  });

  it("never asks the server for a bundled definition reached by URL", async () => {
    listFeatures.mockResolvedValue(catalogWithShipped());
    getFeatureMeta.mockResolvedValue({ ...META, bundled_ids: [BUNDLED_ID] });
    renderPage(`/personalization/skills?feature=${BUNDLED_ID}`);

    await screen.findByText("pick:weekly-report");
    // Not materialized: the call is the 403, and a refusal is not a scope.
    expect(personalizeFeature).not.toHaveBeenCalled();
    // Dropped rather than shown, so the page is the caller's own expert again —
    // with neither the refusal screen nor the persona-files tab that scope adds.
    expect(await screen.findByTestId("skills")).toHaveTextContent(
      "skills:my-expert",
    );
    expect(screen.queryByText("features.scopeFeatureAgentFailed")).toBeNull();
    expect(screen.queryByText("personalization.tabs.files")).toBeNull();
  });

  it("offers the feature row only to the caller the call accepts", async () => {
    // ``features`` alone is a run permission; giving a definition an agent is an
    // administrator's call, and every pick would come back refused.
    renderPage("/personalization/skills", FEATURES_USER);

    expect(await screen.findByTestId("skills")).toHaveTextContent(
      "skills:my-expert",
    );
    expect(screen.queryByText("pick:weekly-report")).toBeNull();
    expect(listFeatures).not.toHaveBeenCalled();
  });
});
