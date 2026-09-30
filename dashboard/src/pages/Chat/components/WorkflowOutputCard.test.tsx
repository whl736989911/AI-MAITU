/**
 * What a run handed back, under the names the definition gave it.
 *
 * The files come from the thread's artifact list as workspace paths, and the
 * definition's ``outputs`` name what the caller asked for — so the card's job is
 * to pair the two: a produced file shown under its declared name (and still open
 * for preview and download), and a file nothing declared shown as itself.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import { ChatFilePreviewProvider } from "../ChatFilePreviewContext";
import WorkflowOutputCard from "./WorkflowOutputCard";

const QUOTE = "/ws/outputs/quote.md";
const NOTES = "/ws/notes.txt";

/** The card as the chat renders it: inside the preview context it opens files in. */
function renderCard(
  files: string[],
  onPreview: (path: string) => void = () => undefined,
) {
  return render(
    <ChatFilePreviewProvider
      openFilePreview={onPreview}
      openKnowledgeCitation={() => undefined}
    >
      <WorkflowOutputCard
        agentId="feat-quote"
        files={files}
        outputs={[
          {
            name: "报价单",
            form: "markdown",
            path: "outputs/quote.md",
            description: {
              zh: "给客户的报价",
              en: "The quote for the customer",
            },
          },
        ]}
      />
    </ChatFilePreviewProvider>,
  );
}

describe("<WorkflowOutputCard />", () => {
  it("renders artifacts as unboxed content and reveals their details on demand", async () => {
    const user = userEvent.setup();
    renderCard([QUOTE, NOTES]);

    expect(screen.getByText("chat.workflow.outputCount")).toBeInTheDocument();
    expect(screen.getByText("报价单")).toBeInTheDocument();
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.queryByText(QUOTE)).not.toBeInTheDocument();
    const expand = screen.getByRole("button", { name: "chat.workflow.expandDetails" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    await user.click(expand);

    expect(screen.getByText(QUOTE)).toBeInTheDocument();
    expect(screen.getByText("给客户的报价")).toBeInTheDocument();
    expect(screen.getAllByRole("button", { name: "common.preview" })).toHaveLength(2);
    expect(screen.getAllByRole("button", { name: "common.download" })).toHaveLength(2);
  });
  it("keeps the empty state and keyboard-accessible details control on one compact row", () => {
    renderCard([]);
    const empty = screen.getByText("chat.workflow.noOutputs");
    expect(empty.parentElement).toContainElement(
      screen.getByRole("button", { name: "chat.workflow.expandDetails" }),
    );
    expect(screen.queryByRole("button", { name: "common.download" })).not.toBeInTheDocument();
  });
  it("opens a produced file in the chat's file panel", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderCard([QUOTE, NOTES], onPreview);

    await user.click(
      (await screen.findAllByRole("button", { name: "common.preview" }))[0],
    );

    // The path is the workspace one the panel and the download use — not the
    // label the card put on it.
    expect(onPreview).toHaveBeenCalledWith(QUOTE);
  });

  it("normalizes output mount URIs before opening workspace files", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    const expectedPaths = [
      "output/苏州本周天气预报_20260930.md",
      "output/forecast.md",
      "output/summary.txt",
    ];
    renderCard(
      [
        "file:///output/%E8%8B%8F%E5%B7%9E%E6%9C%AC%E5%91%A8%E5%A4%A9%E6%B0%94%E9%A2%84%E6%8A%A5_20260930.md",
        "/output/forecast.md",
        "output\\summary.txt",
      ],
      onPreview,
    );

    const previews = await screen.findAllByRole("button", {
      name: "common.preview",
    });
    for (const preview of previews) await user.click(preview);

    expect(onPreview.mock.calls.map(([path]) => path)).toEqual(expectedPaths);
  });
});
