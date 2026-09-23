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
  it("shows the produced files, declared ones under their declared name", () => {
    renderCard([QUOTE, NOTES]);

    // The declared deliverable is read by the name the caller asked for, and the
    // file it came from is still visible — it has to be findable in the workspace.
    expect(screen.getByText("报价单")).toBeInTheDocument();
    expect(screen.getByText(QUOTE)).toBeInTheDocument();
    expect(screen.getByText("给客户的报价")).toBeInTheDocument();

    // A file nothing declared keeps its own name, and the card counts what it shows.
    expect(screen.getByText("notes.txt")).toBeInTheDocument();
    expect(screen.getByText("chat.workflow.outputCount")).toBeInTheDocument();

    expect(
      screen.getAllByRole("button", { name: "common.preview" }),
    ).toHaveLength(2);
    expect(
      screen.getAllByRole("button", { name: "common.download" }),
    ).toHaveLength(2);
  });

  it("opens a produced file in the chat's file panel", async () => {
    const user = userEvent.setup();
    const onPreview = vi.fn();
    renderCard([QUOTE, NOTES], onPreview);

    await user.click(
      screen.getAllByRole("button", { name: "common.preview" })[0],
    );

    // The path is the workspace one the panel and the download use — not the
    // label the card put on it.
    expect(onPreview).toHaveBeenCalledWith(QUOTE);
  });
});
