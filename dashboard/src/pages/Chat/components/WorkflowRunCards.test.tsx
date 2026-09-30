import { describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";

const { useWorkflowRunMock, useRunArtifactsMock } = vi.hoisted(() => ({
  useWorkflowRunMock: vi.fn(),
  useRunArtifactsMock: vi.fn(),
}));

vi.mock("../hooks/useWorkflowRun", () => ({
  useWorkflowRun: useWorkflowRunMock,
  useRunArtifacts: useRunArtifactsMock,
}));

vi.mock("./WorkflowInputCard", () => ({
  default: ({ run }: { run: unknown }) => (
    <div
      data-testid={run ? "submitted-workflow-input" : "workflow-input-form"}
    />
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

function renderCards() {
  return render(
    <>
      <div data-testid="chat-header" />
      <WorkflowRunCards
        agentId="feature"
        agentKind="feature"
        threadId="thread"
        busy={false}
        isStreaming={false}
        onRun={vi.fn()}
      />
    </>,
  );
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

    renderCards();

    expect(await screen.findByTestId("submitted-workflow-input")).toBeInTheDocument();
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

    renderCards();

    expect(screen.getByTestId("workflow-input-form")).toBeInTheDocument();
    expect(screen.queryByTestId("workflow-output")).not.toBeInTheDocument();
    expect(screen.getByTestId("chat-header")).toBeEmptyDOMElement();
  });
});
