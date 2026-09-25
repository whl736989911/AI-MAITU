import { fireEvent, render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as PersonalizationPanelsModule from "./components/PersonalizationPanels";
import type { OctopAgent } from "../../../context/AgentContext";
import { KIND_FEATURE } from "../../../utils/agentKind";
import PersonalizationPage from "./index";

const navigate = vi.fn();
const held = vi.hoisted(() => ({
  agents: [] as OctopAgent[],
  activeAgentId: null as string | null,
}));

vi.mock("react-router-dom", () => ({ useNavigate: () => navigate }));
vi.mock("../../../layouts/PageShell", () => ({
  default: ({
    children,
    pathTabs,
  }: {
    children?: React.ReactNode;
    pathTabs?: { options: { value: string; label: string }[] };
  }) => (
    <div>
      <div role="tablist" aria-label="personalization">
        {pathTabs?.options.map(({ value, label }) => (
          <button role="tab" key={value}>
            {label}
          </button>
        ))}
      </div>
      {children}
    </div>
  ),
}));
vi.mock("../../../hooks/useIsMobile", () => ({ useIsMobile: () => false }));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => null,
}));
vi.mock("../../../hooks/useUserRole", () => ({ useUserRole: () => null }));
vi.mock("../../../hooks/usePathTabs", () => ({
  usePathTabs: ({ tabs }: { tabs: string[] }) => ({
    activeTab: tabs[0],
    handleTabChange: vi.fn(),
    isMounted: () => true,
  }),
}));
vi.mock("./components/PersonalizationPanels", async (importOriginal) => ({
  ...(await importOriginal<typeof PersonalizationPanelsModule>()),
  default: () => <div data-testid="panels" />,
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ agents: held.agents, activeAgentId: held.activeAgentId }),
}));

function sharedFeature(): OctopAgent {
  return {
    id: 1,
    agent_id: "feat-weekly",
    kind: KIND_FEATURE,
    name: "Shared feature",
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
    is_shared: true,
    is_owner: false,
  };
}

describe("PersonalizationPage feature availability", () => {
  beforeEach(() => {
    navigate.mockReset();
    held.agents = [];
    held.activeAgentId = null;
  });

  it("offers creation and feature navigation only when no agent is available", () => {
    render(<PersonalizationPage />);

    expect(
      screen.getByText("personalization.noExpertTitle"),
    ).toBeInTheDocument();
    expect(screen.queryByTestId("panels")).not.toBeInTheDocument();
    fireEvent.click(
      screen.getByRole("button", { name: "personalization.createExpert" }),
    );
    fireEvent.click(
      screen.getByRole("button", { name: "personalization.pickFeature" }),
    );
    expect(navigate).toHaveBeenCalledWith("/experts");
    expect(navigate).toHaveBeenCalledWith("/features");
  });

  it("offers private memory for the first shared feature without an expert", () => {
    held.agents = [sharedFeature()];
    render(<PersonalizationPage />);

    expect(
      screen.getByRole("tab", { name: "personalization.tabs.memory" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "personalization.tabs.skills" }),
    ).not.toBeInTheDocument();
    expect(screen.getByTestId("panels")).toBeInTheDocument();
    expect(
      screen.queryByText("personalization.noExpertTitle"),
    ).not.toBeInTheDocument();
  });
  it("keeps a selected shared feature in the memory-only scope even with an owned expert", () => {
    held.agents = [
      {
        ...sharedFeature(),
        agent_id: "my-expert",
        kind: undefined,
        is_shared: false,
        is_owner: true,
      },
      sharedFeature(),
    ];
    held.activeAgentId = "feat-weekly";
    render(<PersonalizationPage />);

    expect(
      screen.getByRole("tab", { name: "personalization.tabs.memory" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("tab", { name: "personalization.tabs.skills" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("personalization.noExpertTitle"),
    ).not.toBeInTheDocument();
  });
});
