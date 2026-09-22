/**
 * The personalization page when the caller has no expert of their own.
 *
 * The panels below are built from one agent id, so with none the page used to
 * leave a switcher's rejection ("pick an agent first") where the body should be.
 * It now says what it is waiting for and offers the two ways to get one, and it
 * still renders the panels the moment the caller has an expert. The empty state
 * is about the caller's *experts*: a caller who only holds features is exactly
 * the case this branch serves, so the branch is never read as "this deployment
 * has no features".
 */

import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as PersonalizationPanelsModule from "./components/PersonalizationPanels";
import type { OctopAgent } from "../../../context/AgentContext";
import { KIND_FEATURE } from "../../../utils/agentKind";
import PersonalizationPage from "./index";

const navigateMock = vi.fn();
const panelsMock = vi.fn();

vi.mock("react-router-dom", () => ({
  useNavigate: () => navigateMock,
}));

vi.mock("../../../layouts/PageShell", () => ({
  default: ({ children }: { children?: React.ReactNode }) => (
    <div>{children}</div>
  ),
}));

vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../../hooks/useCurrentUser", () => ({ useCurrentUser: () => null }));
vi.mock("../../../hooks/usePathTabs", () => ({
  usePathTabs: () => ({
    activeTab: "skills",
    handleTabChange: vi.fn(),
    isMounted: () => true,
  }),
}));

vi.mock("./components/PersonalizationPanels", async (importOriginal) => {
  // The page's own imports of this module stay the app's — the tabs a scope
  // offers are asked of the real table; only the panels are replaced.
  const actual = await importOriginal<typeof PersonalizationPanelsModule>();
  return {
    ...actual,
    default: (props: { agentId: string | null }) => {
      panelsMock(props);
      return <div data-testid="panels" />;
    },
  };
});

const held = vi.hoisted(() => ({ agents: [] as OctopAgent[] }));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ agents: held.agents, activeAgentId: null }),
}));

function agent(agentId: string, kind?: string): OctopAgent {
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
  };
}

describe("PersonalizationPage with no expert of the caller's", () => {
  beforeEach(() => {
    navigateMock.mockReset();
    panelsMock.mockReset();
    held.agents = [];
  });

  it("offers both ways to get one instead of an empty panel", () => {
    // A caller who holds a feature and no expert of their own: the panels are
    // per-expert, so this is the branch — and the entries still lead to both.
    held.agents = [agent("feat-weekly", KIND_FEATURE)];
    render(<PersonalizationPage />);

    expect(screen.queryByTestId("panels")).toBeNull();
    // ``t("personalization.noExpertTitle")`` — the i18n test mock resolves a
    // key with no fallback to itself.
    expect(
      screen.getByText("personalization.noExpertTitle"),
    ).toBeInTheDocument();

    fireEvent.click(
      screen.getByRole("button", { name: "personalization.createExpert" }),
    );
    expect(navigateMock).toHaveBeenCalledWith("/experts");

    fireEvent.click(
      screen.getByRole("button", { name: "personalization.pickFeature" }),
    );
    expect(navigateMock).toHaveBeenCalledWith("/features");
  });

  it("renders the panels, for the caller's own expert", () => {
    held.agents = [agent("A1")];
    render(<PersonalizationPage />);

    expect(screen.getByTestId("panels")).toBeInTheDocument();
    expect(panelsMock).toHaveBeenCalledWith(
      expect.objectContaining({ agentId: "A1" }),
    );
    expect(
      screen.queryByRole("button", { name: "personalization.createExpert" }),
    ).toBeNull();
  });

  it("prefers the caller's own expert over a feature they also hold", () => {
    held.agents = [agent("feat-weekly", KIND_FEATURE), agent("A1")];
    render(<PersonalizationPage />);

    expect(panelsMock).toHaveBeenCalledWith(
      expect.objectContaining({ agentId: "A1" }),
    );
  });
});
