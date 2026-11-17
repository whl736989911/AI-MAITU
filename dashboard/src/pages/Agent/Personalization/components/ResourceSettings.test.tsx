import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({ role: "admin" }),
}));
vi.mock("../../../../layouts/PageShell", () => ({ pageShellStyles: {} }));
vi.mock("../../Skills/components/SkillsTabs", () => ({
  default: () => <input aria-label="skills draft" defaultValue="" />,
}));
vi.mock("../../Tools/ToolsTabs", () => ({ default: () => null }));
vi.mock("../../../Experts/components/SubagentManager", () => ({
  default: () => null,
}));
vi.mock("../../Memory/MemoryPanel", () => ({ default: () => null }));
vi.mock("../../Channels/ChannelsPanel", () => ({ default: () => null }));
vi.mock("./MBTISelector", () => ({ default: () => null }));
vi.mock("./AgentPluginsPanel", () => ({
  default: () => <div>plugin settings fixture</div>,
}));
vi.mock("./AgentPersonaFiles", () => ({ default: () => null }));
vi.mock("../../../Features/components/FeatureWorkspaceTabs", () => ({
  default: () => null,
}));

import ResourceSettings from "./ResourceSettings";

describe("ResourceSettings controlled navigation", () => {
  it("opens a newly requested panel and preserves a previously visited draft", async () => {
    const props = {
      agentId: "expert-a",
      agentState: "running",
      scope: "expert" as const,
      canWrite: true,
    };
    const { rerender } = render(
      <ResourceSettings {...props} activeTab="skills" />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "skills draft" }), {
      target: { value: "unsaved draft" },
    });

    rerender(<ResourceSettings {...props} activeTab="plugins" />);
    expect(await screen.findByText("plugin settings fixture")).toBeVisible();

    rerender(<ResourceSettings {...props} activeTab="skills" />);
    expect(screen.getByRole("textbox", { name: "skills draft" })).toHaveValue(
      "unsaved draft",
    );
  });
});
