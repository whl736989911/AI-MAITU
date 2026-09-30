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
  it("keeps the form collapsed until its keyboard-accessible entry is expanded", async () => {
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

    const expand = screen.getByRole("button", { name: "chat.workflow.expandInputs" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    await user.click(expand);
    expect(screen.getByRole("button", { name: "chat.workflow.collapseInputs" }))
      .toHaveAttribute("aria-expanded", "true");
    expect(runButton()).toBeDisabled();
  });

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
    await user.click(screen.getByRole("button", { name: "chat.workflow.expandInputs" }));
    expect(runButton()).toBeDisabled();

    const customer = screen.getByRole("textbox");
    await user.type(customer, "ACME");
    expect(runButton()).toBeEnabled();
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
    await user.click(screen.getByRole("button", { name: "chat.workflow.expandInputs" }));

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
  it("keeps submitted values reachable from a compact, accessible run summary", async () => {
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
        featureName="Report"
        busy={false}
        onRun={vi.fn()}
      />,
    );

    const expand = screen.getByRole("button", { name: "chat.workflow.expandInputs" });
    expect(expand).toHaveAttribute("aria-expanded", "false");
    expect(screen.getByText("Report")).toBeInTheDocument();
    expect(screen.getByText("chat.workflow.inputCount")).toBeInTheDocument();
    expect(screen.queryByText("ACME")).not.toBeInTheDocument();
    await user.click(expand);
    expect(screen.getByText("ACME")).toBeInTheDocument();
    expect(screen.getByText("3")).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "chat.workflow.run" }))
      .not.toBeInTheDocument();
  });
  it("remembers whether a thread summary is expanded for the browser session", async () => {
    const user = userEvent.setup();
    window.sessionStorage.removeItem("workflow-input:persisted-thread");
    const props = {
      inputs: INPUTS,
      run: null,
      agentId: "feat-quote",
      stateKey: "persisted-thread",
      busy: false,
      onRun: vi.fn(),
    } as const;
    const first = render(<WorkflowInputCard {...props} />);
    await user.click(screen.getByRole("button", { name: "chat.workflow.expandInputs" }));
    first.unmount();

    render(<WorkflowInputCard {...props} />);
    expect(screen.getByRole("button", { name: "chat.workflow.collapseInputs" }))
      .toHaveAttribute("aria-expanded", "true");
    window.sessionStorage.removeItem("workflow-input:persisted-thread");
  });
});
