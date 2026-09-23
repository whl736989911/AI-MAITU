/**
 * The personalization page's subject, when the caller holds both kinds.
 *
 * The page's scope is one agent — the pointer, resolved among the agents it may
 * configure — and what has to hold is which half of the caller's agents it
 * resolves to and which scope the panels below are told they are in. A feature
 * of the caller's is that feature, with the tabs a feature offers (its persona
 * files among them); an expert is the expert it always was; and a pointer on an
 * agent that is somebody else's falls back to the caller's own expert rather than
 * offering panels whose only outcome is a refusal.
 *
 * The panels and the tab-state hook are mocked at their boundary, so what is
 * asserted is what the page *asks them for* — the two bars are the real ones.
 */

import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as PersonalizationPanelsModule from "./components/PersonalizationPanels";
import type { OctopRole } from "../../../api/modules/auth";
import type { OctopAgent } from "../../../context/AgentContext";
import { KIND_FEATURE } from "../../../utils/agentKind";
import PersonalizationPage from "./index";

const panelsMock = vi.fn();
const setActiveAgent = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => vi.fn(),
}));

// The shell's chrome is not what this file is about; its agent-bar slot is: the
// page's scope controls are what the panels' scope is read from.
vi.mock("../../../layouts/PageShell", () => ({
  default: ({
    children,
    agentBar,
  }: {
    children?: React.ReactNode;
    agentBar?: React.ReactNode;
  }) => (
    <div>
      {agentBar}
      {children}
    </div>
  ),
}));

vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => null,
}));
vi.mock("../../../hooks/usePathTabs", () => ({
  usePathTabs: () => ({
    activeTab: "skills",
    handleTabChange: vi.fn(),
    isMounted: () => true,
  }),
}));

// Only the panels themselves are replaced: which tabs a scope offers is the
// table's answer and stays the app's (``offeredTabs.test.ts``).
vi.mock("./components/PersonalizationPanels", async (importOriginal) => {
  const actual = await importOriginal<typeof PersonalizationPanelsModule>();
  return {
    ...actual,
    default: (props: {
      agentId: string | null;
      scope: string;
      tabs: readonly string[];
      canWrite: boolean;
    }) => {
      panelsMock(props);
      return <div data-testid="panels" />;
    },
  };
});

const held = vi.hoisted(() => ({
  agents: [] as OctopAgent[],
  activeAgentId: null as string | null,
  role: null as OctopRole | null,
}));

// The role is the other half of the write rule (``canManageExpert``); null is
// its own case — "not an administrator".
vi.mock("../../../hooks/useUserRole", () => ({
  useUserRole: () => held.role,
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: held.agents,
    activeAgentId: held.activeAgentId,
    setActiveAgent,
    loading: false,
  }),
}));

function agent(
  agentId: string,
  kind?: string,
  overrides: Partial<OctopAgent> = {},
): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    name: agentId,
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
    is_shared: false,
    is_owner: true,
    ...overrides,
  };
}

/** The one call the page makes to its panels, as the props it made it with. */
function panelsProps() {
  expect(panelsMock).toHaveBeenCalledTimes(1);
  return panelsMock.mock.calls[0][0] as {
    agentId: string | null;
    scope: string;
    tabs: readonly string[];
    canWrite: boolean;
  };
}

describe("PersonalizationPage's subject", () => {
  beforeEach(() => {
    panelsMock.mockReset();
    setActiveAgent.mockClear();
    held.agents = [];
    held.activeAgentId = null;
    held.role = null;
  });

  it("is a feature of the caller's, in the feature's own scope", () => {
    held.agents = [agent("A1"), agent("feat-weekly", KIND_FEATURE)];
    held.activeAgentId = "feat-weekly";
    render(<PersonalizationPage />);

    expect(screen.getByTestId("panels")).toBeInTheDocument();
    // Its persona files are a feature's own tab — the one an expert does not have.
    expect(panelsProps()).toEqual(
      expect.objectContaining({
        agentId: "feat-weekly",
        scope: "feature",
        canWrite: true,
        tabs: expect.arrayContaining(["files", "skills", "memory"]),
      }),
    );
    // Both rows are the page's scope controls: each half of what it can configure.
    expect(screen.getByText("agentSelector.label")).toBeInTheDocument();
    expect(screen.getByText("agentSelector.featureLabel")).toBeInTheDocument();
  });

  it("is an expert of the caller's, in the expert's own scope as before", () => {
    held.agents = [agent("A1"), agent("feat-weekly", KIND_FEATURE)];
    held.activeAgentId = "A1";
    render(<PersonalizationPage />);

    const props = panelsProps();
    expect(props.agentId).toBe("A1");
    expect(props.scope).toBe("expert");
    expect(props.tabs).toContain("skills");
    expect(props.tabs).not.toContain("files");
  });

  it("is the caller's own expert when the pointer is on somebody else's feature", () => {
    held.agents = [
      agent("A1"),
      agent("feat-theirs", KIND_FEATURE, { is_shared: true, is_owner: false }),
    ];
    held.activeAgentId = "feat-theirs";
    render(<PersonalizationPage />);

    // A feature shared with the caller is not theirs to configure here — the
    // panels fall back exactly where the experts' side always has.
    expect(panelsProps()).toEqual(
      expect.objectContaining({ agentId: "A1", scope: "expert" }),
    );
  });

  it("is somebody else's feature, writable, for an administrator", () => {
    held.agents = [
      agent("A1"),
      agent("feat-theirs", KIND_FEATURE, { is_shared: false, is_owner: false }),
    ];
    held.activeAgentId = "feat-theirs";
    held.role = "admin";
    render(<PersonalizationPage />);

    // ``is_owner`` says "you did not create this" and nothing about whether the
    // write is allowed: for an administrator the server accepts it
    // (``assert_agent_owner``), so the panels that write are offered — the same
    // answer the row's owner gets.
    expect(panelsProps()).toEqual(
      expect.objectContaining({
        agentId: "feat-theirs",
        scope: "feature",
        canWrite: true,
        tabs: expect.arrayContaining(["skills", "files", "memory"]),
      }),
    );
  });

  it("keeps that feature read-only for a caller who is no administrator", () => {
    held.agents = [
      agent("A1"),
      agent("feat-theirs", KIND_FEATURE, { is_shared: false, is_owner: false }),
    ];
    held.activeAgentId = "feat-theirs";
    held.role = "user";
    render(<PersonalizationPage />);

    const props = panelsProps();
    expect(props.canWrite).toBe(false);
    // The tabs that write are not offered at all; what is readable stays.
    expect(props.tabs).not.toContain("skills");
    expect(props.tabs).not.toContain("files");
    expect(props.tabs).toContain("memory");
  });
});
