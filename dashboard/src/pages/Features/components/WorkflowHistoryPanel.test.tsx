import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const api = vi.hoisted(() => ({
  runs: vi.fn(),
  changes: vi.fn(),
  revertChange: vi.fn(),
}));
vi.mock("../../../api/modules/featureWorkflow", () => ({
  featureWorkflowApi: api,
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import WorkflowHistoryPanel from "./WorkflowHistoryPanel";

const change = {
  id: "change-1",
  target: "definition",
  summary: "Require review",
  items: [],
  status: "applied",
  run_id: null,
  created_at: 1,
  reverted_at: null,
};

describe("<WorkflowHistoryPanel />", () => {
  it("shows only this caller's data and undoes an authorized change", async () => {
    api.runs.mockResolvedValue({
      runs: [
        {
          id: "run-1",
          created_at: 1,
          thread_id: "thread-1",
          inputs: { unit: "kg" },
        },
      ],
    });
    api.changes.mockResolvedValue({ changes: [change] });
    api.revertChange.mockResolvedValue({
      ...change,
      status: "reverted",
      reverted_at: 2,
    });
    const user = userEvent.setup();
    render(<WorkflowHistoryPanel agentId="feat-demo" canWrite />);

    expect(
      screen.queryByDisplayValue("Use metric units"),
    ).not.toBeInTheDocument();
    expect(await screen.findByText(/"unit":"kg"/)).toBeInTheDocument();
    expect(screen.getByText(/thread-1/)).toBeInTheDocument();
    expect(screen.getByText("Require review")).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "workflowChange.revert" }),
    );
    await user.click(screen.getByRole("button", { name: "OK" }));
    await waitFor(() =>
      expect(api.revertChange).toHaveBeenCalledWith("feat-demo", "change-1"),
    );
    await screen.findByText("workflowChange.reverted");
    expect(
      screen.queryByRole("button", { name: "workflowChange.revert" }),
    ).not.toBeInTheDocument();
  });

  it("does not offer a shared definition's undo to a caller", async () => {
    api.runs.mockResolvedValue({ runs: [] });
    api.changes.mockResolvedValue({ changes: [change] });
    render(<WorkflowHistoryPanel agentId="feat-demo" canWrite={false} />);
    await screen.findByText("Require review");
    expect(
      screen.queryByRole("button", { name: "workflowChange.revert" }),
    ).not.toBeInTheDocument();
  });
});
