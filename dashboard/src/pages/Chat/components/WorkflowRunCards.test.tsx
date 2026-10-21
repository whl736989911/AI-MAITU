import { describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { ChatAttachment } from "../hooks/useChat";
import type { FeatureRunPayload } from "../utils/featureRun";

const { useWorkflowRunMock, useRunArtifactsMock } = vi.hoisted(() => ({
  useWorkflowRunMock: vi.fn(),
  useRunArtifactsMock: vi.fn(),
}));

vi.mock("../hooks/useWorkflowRun", () => ({
  useWorkflowRun: useWorkflowRunMock,
  useRunArtifacts: useRunArtifactsMock,
}));

vi.mock("./WorkflowInputCard", () => ({
  default: ({
    run,
    onRun,
  }: {
    run: unknown;
    onRun: (submission: {
      attachments: unknown[];
      payload: { inputs: Record<string, string> };
    }) => void;
  }) => (
    <div>
      <div
        data-testid={run ? "submitted-workflow-input" : "workflow-input-form"}
      />
      <button
        type="button"
        onClick={() =>
          onRun({
            attachments: [],
            payload: { inputs: { customer: "ACME" } },
          })
        }
      >
        run
      </button>
    </div>
  ),
}));

vi.mock("./WorkflowOutputCard", () => ({
  default: () => <div data-testid="workflow-output" />,
}));

import WorkflowRunCards from "./WorkflowRunCards";

const inputs = {
  type: "object" as const,
  required: [],
  properties: { customer: { type: "string" as const } },
};

type RunHandler = (
  text: string,
  attachments: ChatAttachment[],
  payload: FeatureRunPayload,
) => boolean;

function renderCards(onRun: RunHandler) {
  return render(
    <>
      <div data-testid="chat-header" />
      <WorkflowRunCards
        agentId="feature"
        agentKind="feature"
        threadId="thread"
        busy={false}
        isStreaming={false}
        onRun={onRun}
      />
    </>,
  );
}

function mockRun(markSubmitted: (inputs: Record<string, unknown>) => void) {
  useWorkflowRunMock.mockReturnValue({
    inputs,
    definition: null,
    run: null,
    loading: false,
    markSubmitted,
  });
  useRunArtifactsMock.mockReturnValue([]);
}

describe("WorkflowRunCards placement", () => {
  it("keeps the submitted input summary and artifacts together in the dock", async () => {
    useWorkflowRunMock.mockReturnValue({
      inputs,
      definition: null,
      run: { id: "run-1", createdAt: 1, inputs: { customer: "ACME" } },
      loading: false,
      markSubmitted: vi.fn(),
    });
    useRunArtifactsMock.mockReturnValue([]);

    renderCards(vi.fn(() => true));

    expect(
      await screen.findByTestId("submitted-workflow-input"),
    ).toBeInTheDocument();
    expect(screen.getByTestId("workflow-output")).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-input-form")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-header")).toBeEmptyDOMElement();
  });

  it("keeps an unsubmitted form in the workflow dock without output", () => {
    useWorkflowRunMock.mockReturnValue({
      inputs,
      definition: null,
      run: null,
      loading: false,
      markSubmitted: vi.fn(),
    });
    useRunArtifactsMock.mockReturnValue([]);

    renderCards(vi.fn(() => true));

    expect(screen.getByTestId("workflow-input-form")).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-output")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-header")).toBeEmptyDOMElement();
  });
});

describe("WorkflowRunCards submission acceptance", () => {
  it("marks the run submitted only after the send is accepted", async () => {
    const user = userEvent.setup();
    const markSubmitted = vi.fn();
    mockRun(markSubmitted);
    const onRun = vi.fn(() => true);

    renderCards(onRun);
    await user.click(screen.getByRole("button", { name: "run" }));

    expect(onRun).toHaveBeenCalledTimes(1);
    expect(markSubmitted).toHaveBeenCalledWith({ customer: "ACME" });
  });

  it("keeps the form editable when the send is rejected", async () => {
    const user = userEvent.setup();
    const markSubmitted = vi.fn();
    mockRun(markSubmitted);
    const onRun = vi.fn(() => false);

    renderCards(onRun);
    await user.click(screen.getByRole("button", { name: "run" }));

    expect(onRun).toHaveBeenCalledTimes(1);
    expect(markSubmitted).not.toHaveBeenCalled();
  });
});
