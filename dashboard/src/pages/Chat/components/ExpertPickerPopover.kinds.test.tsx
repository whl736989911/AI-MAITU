/**
 * The `@` popover's two kinds of agent pick.
 *
 * Both kinds arrive in ``agents``; the popover labels the features' group and
 * leaves the experts' group unlabelled — the group this picker has always
 * listed — and draws that label only when this picker's own options hold a
 * feature. A caller whose pickable agents are all experts gets the picker
 * unchanged, words included.
 */

import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import ExpertPickerPopover from "./ExpertPickerPopover";
import type { ChatAgentOption } from "./ExpertAgentAvatar";
import { KIND_FEATURE } from "../../../utils/agentKind";

function pick(agentId: string, name: string, kind?: string): ChatAgentOption {
  return { agent_id: agentId, name, kind };
}

function renderPicker(agents: ChatAgentOption[]) {
  return render(
    <MemoryRouter>
      <ExpertPickerPopover
        agents={agents}
        selectedAgentIds={[]}
        onSelect={vi.fn()}
      />
    </MemoryRouter>,
  );
}

describe("ExpertPickerPopover kind groups", () => {
  it("draws an expert-only picker as the picker it has always been", () => {
    renderPicker([pick("A1", "Expert One"), pick("A2", "Expert Two")]);

    expect(screen.getByText("Expert One")).toBeInTheDocument();
    expect(screen.getByText("Expert Two")).toBeInTheDocument();
    // ``t("chat.featuresGroup", "功能")`` — the i18n test mock resolves a call
    // with a fallback to the fallback.
    expect(screen.queryByText("功能")).toBeNull();
    // No fallback given for the expert-only wording: the mock resolves the key
    // to itself, and the point is that the two-kind wording is not used.
    expect(screen.getByPlaceholderText("chat.expertPickerSearch")).toBeInTheDocument();
  });

  it("labels the features' group, below the experts, and says so in its words", () => {
    renderPicker([
      pick("A1", "Expert One"),
      pick("F1", "Weekly digest", KIND_FEATURE),
    ]);

    const heading = screen.getByText("功能");
    expect(
      screen
        .getByText("Expert One")
        .compareDocumentPosition(heading) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      heading.compareDocumentPosition(screen.getByText("Weekly digest")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    // ``t("chat.agentPickerSearch", "搜索专家与功能")``
    expect(screen.getByPlaceholderText("搜索专家与功能")).toBeInTheDocument();
  });

  it("draws a feature-only picker as the one group it has", () => {
    renderPicker([pick("F1", "Weekly digest", KIND_FEATURE)]);

    expect(screen.getByText("功能")).toBeInTheDocument();
    expect(screen.getByText("Weekly digest")).toBeInTheDocument();
    expect(screen.queryByText("Expert One")).toBeNull();
    // A list that offers only features names features, not experts it does not
    // offer: ``t("chat.featurePickerSearch", "搜索功能")``.
    expect(screen.getByPlaceholderText("搜索功能")).toBeInTheDocument();
  });
});
