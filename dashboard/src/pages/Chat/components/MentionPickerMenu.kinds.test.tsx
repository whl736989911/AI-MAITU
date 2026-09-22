/**
 * The `@` menu's two kinds of agent pick.
 *
 * Both kinds arrive in ``agents``; the menu draws each under its own section —
 * the experts under the section it has always drawn, the features under a new
 * one — and draws a kind's section only when this picker's own options hold one.
 * A caller whose pickable agents are all experts gets the one agent section the
 * menu has always drawn.
 */

import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MentionPickerMenu, { buildMentionItems } from "./MentionPickerMenu";
import type { ChatAgentOption } from "./ExpertAgentAvatar";
import { KIND_FEATURE } from "../../../utils/agentKind";

function pick(agentId: string, name: string, kind?: string): ChatAgentOption {
  return { agent_id: agentId, name, kind };
}

function renderMenu(agents: ChatAgentOption[]) {
  // The menu is handed the picks already built from the same option list, so
  // the fixture builds them the way the composer does.
  const items = buildMentionItems("", [], agents, [], []);
  return render(
    <MentionPickerMenu
      items={items}
      query=""
      agents={agents}
      activeIndex={0}
      onSelect={vi.fn()}
      onHover={vi.fn()}
    />,
  );
}

describe("MentionPickerMenu kind sections", () => {
  it("draws an expert-only picker as the one agent section it has always been", () => {
    renderMenu([pick("A1", "Expert One"), pick("A2", "Expert Two")]);

    // ``t("mention.experts", "Experts")`` — the i18n test mock resolves a call
    // with a fallback to the fallback.
    expect(screen.getByText("Experts")).toBeInTheDocument();
    expect(screen.getByText("Expert One")).toBeInTheDocument();
    expect(screen.getByText("Expert Two")).toBeInTheDocument();
    expect(screen.queryByText("Features")).toBeNull();
  });

  it("splits the two kinds into their own sections", () => {
    renderMenu([
      pick("A1", "Expert One"),
      pick("F1", "Weekly digest", KIND_FEATURE),
    ]);

    const experts = screen.getByText("Experts");
    const features = screen.getByText("Features");
    expect(
      experts.compareDocumentPosition(features) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
    expect(
      features.compareDocumentPosition(screen.getByText("Weekly digest")) &
        Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it("draws a feature-only picker as the one group it has", () => {
    renderMenu([pick("F1", "Weekly digest", KIND_FEATURE)]);

    expect(screen.getByText("Features")).toBeInTheDocument();
    expect(screen.getByText("Weekly digest")).toBeInTheDocument();
    expect(screen.queryByText("Experts")).toBeNull();
  });
});
