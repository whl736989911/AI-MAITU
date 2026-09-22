import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ExpertCard, type ExpertSummary } from "./ExpertCard";

/**
 * The template card is one click target, so it has to be operable without a
 * mouse — ``user.tab()`` only reaches it (and ``{Enter}`` only fires it) if the
 * card really is a button rather than a div with a click handler.
 */

const EXPERT: ExpertSummary = {
  id: "quote-expert",
  label: { zh: "报价专家", en: "Quote expert" },
  description: { zh: "起草报价单", en: "Draft a quote" },
  icon_name: "receipt",
  color: "#f97316",
};

describe("<ExpertCard />", () => {
  it("opens the expert from the keyboard", async () => {
    const user = userEvent.setup();
    const onCreate = vi.fn();
    render(
      <ExpertCard
        expert={EXPERT}
        lang="zh"
        isInstalled={false}
        onCreate={onCreate}
      />,
    );

    await user.tab();
    await user.keyboard("{Enter}");

    expect(onCreate).toHaveBeenCalledWith(EXPERT);
  });

  it("carries the create hint, and a badge once the expert is installed", () => {
    const { rerender } = render(
      <ExpertCard
        expert={EXPERT}
        lang="zh"
        isInstalled={false}
        onCreate={vi.fn()}
      />,
    );

    expect(screen.getByText("experts.createFromTemplate")).toBeInTheDocument();
    expect(screen.queryByText("experts.installedBadge")).toBeNull();

    rerender(
      <ExpertCard expert={EXPERT} lang="zh" isInstalled onCreate={vi.fn()} />,
    );

    expect(screen.getByText("experts.installedBadge")).toBeInTheDocument();
  });
});
