import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { ResolvedModel } from "../../../api/types";
import type { ChatAgentOption } from "./ExpertAgentAvatar";
import { KIND_FEATURE } from "../../../utils/agentKind";
import ChatInputActionsRow from "./ChatInputActionsRow";

vi.mock("./ContextWindowRing", () => ({
  default: () => null,
}));

const models: ResolvedModel[] = [
  {
    provider_id: 1,
    provider_name: "Provider",
    provider_kind: "openai",
    model: "compact-model",
    name: "Compact Model",
    context_window: 128_000,
  },
];

const baseProps = {
  isMobile: false,
  isStreaming: false,
  canSend: false,
  text: "",
  polishing: false,
  uploading: false,
  recording: false,
  transcribing: false,
  slashPickerGroups: null,
  slashMenuItems: [],
  onSlashShortcutSelect: vi.fn(),
  onFileSelect: vi.fn(),
  onNewChat: vi.fn(),
  onPolish: vi.fn(),
  onToggleVoice: vi.fn(),
  onCancel: vi.fn(),
  onSubmit: vi.fn(),
};

const feature: ChatAgentOption = {
  agent_id: "F1",
  name: "Weekly digest",
  kind: KIND_FEATURE,
};

describe("ChatInputActionsRow compact pickers", () => {
  it("uses a popover instead of a full-width drawer on narrow desktop", async () => {
    const { container } = render(
      <MemoryRouter>
        <ChatInputActionsRow
          {...baseProps}
          availableModels={models}
          onModelChange={vi.fn()}
        />
      </MemoryRouter>,
    );

    const modelButton = container
      .querySelector("svg.lucide-cpu")
      ?.closest("button");
    expect(modelButton).not.toBeNull();

    fireEvent.click(modelButton!);

    await waitFor(() => {
      expect(document.querySelector(".ant-popover")).toBeInTheDocument();
    });
    expect(document.querySelector(".ant-drawer-content")).toBeNull();
  });

  it("names the feature-only selector when hovered on desktop", async () => {
    const measure = vi
      .spyOn(HTMLElement.prototype, "getBoundingClientRect")
      .mockReturnValue({ width: 800 } as DOMRect);
    const { container } = render(
      <MemoryRouter>
        <ChatInputActionsRow
          {...baseProps}
          availableExperts={[feature]}
          onInsertExpertMention={vi.fn()}
        />
      </MemoryRouter>,
    );
    measure.mockRestore();

    const picker = container
      .querySelector("svg.lucide-graduation-cap")
      ?.closest("button");
    expect(picker).toBeTruthy();
    fireEvent.mouseEnter(picker!);
    expect(await screen.findByText("chat.featurePicker")).toBeInTheDocument();
  });

  it("names both offered kinds in the mobile overflow menu", async () => {
    const { container } = render(
      <MemoryRouter>
        <ChatInputActionsRow
          {...baseProps}
          isMobile
          availableExperts={[{ agent_id: "E1", name: "Expert" }, feature]}
          onInsertExpertMention={vi.fn()}
        />
      </MemoryRouter>,
    );

    const overflow = container
      .querySelector("svg.lucide-ellipsis, svg.lucide-more-horizontal")
      ?.closest("button");
    expect(overflow).not.toBeNull();
    fireEvent.click(overflow!);
    expect(await screen.findByText("chat.agentPicker")).toBeInTheDocument();
  });
});
