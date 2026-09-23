import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { revertChange } = vi.hoisted(() => ({ revertChange: vi.fn() }));
vi.mock("../../../api/modules/featureWorkflow", () => ({
  featureWorkflowApi: { revertChange },
}));

import WorkflowChangeCard, {
  collectWorkflowChanges,
} from "./WorkflowChangeCard";

const change = {
  change_id: "change-1",
  target: "definition" as const,
  summary: "Add a review gate",
  items: [{ path: "/steps/0/gate", before: "auto", after: "confirm" }],
  status: "applied" as const,
};

describe("workflow change cards", () => {
  it("shows a successful tool result only once and ignores a refused change", () => {
    const output = JSON.stringify(change);
    expect(
      collectWorkflowChanges([
        { toolData: { name: "feature_workflow_change", output } },
        { toolData: { name: "feature_workflow_change", output } },
        {
          toolData: {
            name: "feature_workflow_change",
            output: '{"error":"conflict"}',
          },
        },
      ]),
    ).toEqual([change]);
  });

  it("updates an applied change only after the server confirms its undo", async () => {
    const { promise, resolve } = Promise.withResolvers<{ status: string }>();
    revertChange.mockReturnValueOnce(promise);
    const user = userEvent.setup();
    render(<WorkflowChangeCard agentId="feat-demo" change={change} canUndo />);

    expect(screen.getByText("Add a review gate")).toBeInTheDocument();
    expect(screen.getByText(/\/steps\/0\/gate/)).toBeInTheDocument();
    await user.click(
      screen.getByRole("button", { name: "workflowChange.revert" }),
    );
    await user.click(screen.getByRole("button", { name: "OK" }));
    expect(revertChange).toHaveBeenCalledWith("feat-demo", "change-1");
    expect(screen.getByText("workflowChange.applied")).toBeInTheDocument();

    resolve({ status: "reverted" });
    await waitFor(() =>
      expect(screen.getByText("workflowChange.reverted")).toBeInTheDocument(),
    );
    expect(
      screen.queryByRole("button", { name: "workflowChange.revert" }),
    ).not.toBeInTheDocument();
  });
});
