/**
 * FeatureWorkflowPanel.test.tsx — a refusal has to be readable, all of it.
 *
 * The server refuses an invalid definition with *every* problem joined into
 * ``details.reason`` and writes nothing. A save UI that answers that with one
 * generic sentence costs the author a round-trip per problem: fix one, save, be
 * told about the next. So the contract under test is:
 *
 *   - a refused save shows every problem the server listed, in the refusal it
 *     came with, and the document was actually sent for it to be refused;
 *   - a document the editor's own checks refuse is never sent at all — the save
 *     that cannot succeed does not leave the browser;
 *   - JSON that is not a document cannot become the form (nor be saved), which is
 *     the one direction the editor has to refuse rather than repair.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
}));

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    loading: vi.fn(() => vi.fn()),
  },
}));
vi.mock("./WorkflowHistoryPanel", () => ({
  default: () => null,
}));
vi.mock("../../../utils/confirmModal", () => ({
  showConfirmModal: vi.fn(),
}));

import { request } from "../../../api/request";
import type { FeatureWorkflow } from "../../../api/modules/featureWorkflow";
import { showConfirmModal } from "../../../utils/confirmModal";
import FeatureWorkflowPanel from "./FeatureWorkflowPanel";

const api = vi.mocked(request, true);

const DRAFT: FeatureWorkflow = {
  version: 1,
  status: "draft",
  steps: [{ id: "extract", name: "提取要点" }],
};

/** Render the panel and wait for its read, so the editor is on screen. */
async function renderLoaded(canWrite = true) {
  render(<FeatureWorkflowPanel agentId="feat-demo" canWrite={canWrite} />);
  await screen.findByRole("button", { name: "common.save" });
}

/** The saves the panel sent — a refusal is only a refusal if there was one. */
function sentSaves(): [string, RequestInit | undefined][] {
  return api.mock.calls.filter(([, init]) => init?.method === "PUT");
}

beforeEach(() => {
  vi.clearAllMocks();
  // A clean queue per test: a refusal left queued by an earlier test would be
  // consumed by the next one's read and look like an editor that cannot load.
  api.mockReset();
  api.mockResolvedValue({ workflow: null, error: null });
});

describe("<FeatureWorkflowPanel /> refusal", () => {
  it("shows every problem the server refused the definition for", async () => {
    const user = userEvent.setup();
    const problems = [
      "steps[0].prompt is required for an active workflow",
      "outputs[0].form must be one of markdown, json, text, file",
      "rules[0] must be a non-empty string",
    ];
    api.mockResolvedValueOnce({ workflow: DRAFT, error: null });
    api.mockRejectedValueOnce(
      new Error(
        `Request failed: 400 Bad Request - ${JSON.stringify({
          error: {
            code: "WORKFLOW_INVALID",
            message: "definition is invalid",
            details: { reason: problems.join("; ") },
          },
        })}`,
      ),
    );
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await screen.findByText("features.workflow.refusalServer");
    for (const problem of problems) {
      // At least once: every problem is in the refusal list, and the one about a
      // step is also marked on that step's editor — the list is never the only
      // place a problem is said.
      expect((await screen.findAllByText(problem)).length).toBeGreaterThan(0);
    }
    // The document was sent, or there would have been nothing to refuse.
    expect(sentSaves()).toHaveLength(1);
    expect(JSON.parse(String(sentSaves()[0]?.[1]?.body))).toEqual({
      workflow: DRAFT,
    });
  });

  it("never sends a document its own checks refuse", async () => {
    const user = userEvent.setup();
    // An active definition whose step says nothing: refused before any request.
    api.mockResolvedValue({
      workflow: {
        version: 1,
        status: "active",
        steps: [{ id: "extract", name: "提取要点" }],
      },
      error: null,
    });
    await renderLoaded();

    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      (
        await screen.findAllByText(
          "steps[0].prompt is required for an active workflow",
        )
      ).length,
    ).toBeGreaterThan(0);
    expect(sentSaves()).toHaveLength(0);
  });

  it("refuses JSON that is not a document instead of switching to the form", async () => {
    const user = userEvent.setup();
    api.mockResolvedValueOnce({ workflow: DRAFT, error: null });
    await renderLoaded();

    // antd Segmented radios use pointer-events:none on the input; click the label.
    await user.click(screen.getByText("features.workflow.modeJson"));
    const area = screen.getByLabelText("features.workflow.jsonArea");
    await user.clear(area);
    await user.type(area, "{{not a document");
    await user.click(screen.getByText("features.workflow.modeForm"));

    expect(
      await screen.findByText(/definition must be valid JSON/),
    ).toBeInTheDocument();
    // Still the JSON editor: the form never took the unparsable text.
    expect(
      screen.getByLabelText("features.workflow.jsonArea"),
    ).toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "common.save" }));
    expect(sentSaves()).toHaveLength(0);
  });
  it("requires the author to confirm before a draft becomes callable", async () => {
    api.mockResolvedValueOnce({
      workflow: {
        version: 1,
        status: "draft",
        steps: [{ id: "write", name: "Write", prompt: "Write the result" }],
      },
      error: null,
    });
    await renderLoaded();
    const user = userEvent.setup();

    await user.click(screen.getByText("features.workflow.statusActive"));
    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(sentSaves()).toHaveLength(0);
    expect(showConfirmModal).toHaveBeenCalledOnce();
    expect(vi.mocked(showConfirmModal).mock.calls[0][0].title).toBe(
      "features.workflow.publish",
    );
    await act(async () => {
      await vi.mocked(showConfirmModal).mock.calls[0][0].onOk?.(() => {});
    });
    await waitFor(() => expect(sentSaves()).toHaveLength(1));
  });
});
