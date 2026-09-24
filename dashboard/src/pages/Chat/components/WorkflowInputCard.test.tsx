/**
 * The run form: what makes 运行 available, and what one press sends.
 *
 * A run is one chat turn, so the two things that matter are the gate and the
 * payload: nothing may be sent while a declared ``required`` field is still
 * empty, and one press must hand the thread exactly one submission — the values
 * the definition asked for, and the files that were uploaded for it.
 */

import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

vi.mock("../../../hooks/useServerUploadLimit", () => ({
  useServerUploadLimit: () => ({
    maxUploadBytes: 10 * 1024 * 1024,
    maxUploadMb: 10,
    loading: false,
  }),
}));

import type { WorkflowInputs } from "../../../api/modules/featureWorkflow";
import WorkflowInputCard, {
  type WorkflowRunSubmission,
} from "./WorkflowInputCard";

const INPUTS: WorkflowInputs = {
  type: "object",
  required: ["customer_name"],
  properties: {
    customer_name: {
      type: "string",
      title: { zh: "客户名称", en: "Customer" },
    },
    count: { type: "integer", title: { zh: "数量", en: "Count" } },
  },
};

/** The 运行 button, found the way a caller finds it. */
function runButton(): HTMLElement {
  return screen.getByRole("button", { name: "chat.workflow.run" });
}

describe("<WorkflowInputCard />", () => {
  it("keeps 运行 disabled until every required field is answered", async () => {
    const user = userEvent.setup();
    render(
      <WorkflowInputCard
        inputs={INPUTS}
        run={null}
        agentId="feat-quote"
        busy={false}
        onRun={vi.fn()}
      />,
    );

    expect(runButton()).toBeDisabled();

    const customer = screen.getByRole("textbox");
    await user.type(customer, "ACME");
    expect(runButton()).toBeEnabled();

    // A blank is not an answer, however much was typed before it.
    await user.clear(customer);
    expect(runButton()).toBeDisabled();
  });

  it("hands the thread one submission: the values, and nothing else", async () => {
    const user = userEvent.setup();
    const onRun = vi.fn<(submission: WorkflowRunSubmission) => void>();
    render(
      <WorkflowInputCard
        inputs={INPUTS}
        run={null}
        agentId="feat-quote"
        busy={false}
        onRun={onRun}
      />,
    );

    await user.type(screen.getByRole("textbox"), "ACME");
    expect(onRun).not.toHaveBeenCalled();

    await user.click(runButton());

    expect(onRun).toHaveBeenCalledTimes(1);
    expect(onRun.mock.calls[0][0]).toEqual({
      attachments: [],
      payload: {
        // The optional field was left blank, so it is not submitted as an answer.
        inputs: { customer_name: "ACME" },
        attachments: [],
      },
    });
  });

  it("keeps submitted values in a compact summary and makes every value reachable", async () => {
    const user = userEvent.setup();
    render(
      <WorkflowInputCard
        inputs={INPUTS}
        run={{
          id: "run-1",
          createdAt: 1,
          inputs: { customer_name: "ACME", count: 3 },
        }}
        agentId="feat-quote"
        busy={false}
        onRun={vi.fn()}
      />,
    );

    expect(screen.getByText(/ACME/)).toBeInTheDocument();
    expect(screen.queryByText("3")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "chat.workflow.expandInputs" }));
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "chat.workflow.run" }),
    ).not.toBeInTheDocument();
  });
});
