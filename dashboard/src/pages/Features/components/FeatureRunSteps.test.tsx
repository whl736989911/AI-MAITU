import { beforeEach, describe, expect, it, vi } from "vitest";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  FeatureRunAudit,
  FeatureStepRun,
} from "../../../api/modules/features";

/**
 * The run view is where a person meets a stopped run, so these cases drive the
 * real controls and read what the API mock received: approving continues the
 * *same* run and carries only what was actually edited, a draft that is not JSON
 * is refused instead of being sent as text, a rerun with fixes injects the
 * artifact it was given, and the audit shows a human change with both sides.
 */

const { getFeatureRun, approveFeatureRun, rewindFeatureRun, getFeatureRunAudit } =
  vi.hoisted(() => ({
    getFeatureRun: vi.fn(),
    approveFeatureRun: vi.fn(),
    rewindFeatureRun: vi.fn(),
    getFeatureRunAudit: vi.fn(),
  }));

vi.mock("../../../api/modules/features", () => ({
  featuresApi: {
    getFeatureRun,
    approveFeatureRun,
    rewindFeatureRun,
    getFeatureRunAudit,
  },
}));

const { message } = vi.hoisted(() => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

vi.mock("@/utils/antdMessage", () => ({ message }));

import FeatureRunSteps from "./FeatureRunSteps";

const TASK = "task-bom-1";

/** A run stopped at the human gate, with the artifact it is about to release. */
function awaitingGateRun(
  overrides: Partial<FeatureStepRun> = {},
): FeatureStepRun {
  return {
    task_id: TASK,
    feature_id: "bom-extract",
    status: "awaiting_gate",
    current_step: "confirm_l1",
    steps: [
      {
        id: "extract_l1",
        name: "提取 L1",
        seq: 0,
        status: "succeeded",
        gate: "auto",
        on_failure: "abort",
        mode: "agent",
        artifacts: [
          { name: "l1_rows", schema: "table:12cols", value: [["A", "钢", 2]] },
        ],
        // Epoch seconds: the unit the run tables actually store.
        started_at: Math.floor(Date.now() / 1000),
        ended_at: Math.floor(Date.now() / 1000),
        error: null,
        attempts: 1,
        voided: false,
      },
      {
        id: "confirm_l1",
        name: "确认 L1",
        seq: 1,
        status: "running",
        gate: "confirm",
        on_failure: "abort",
        mode: "agent",
        artifacts: [
          { name: "l1_rows", schema: "table:12cols", value: [["A", "钢", 2]] },
        ],
        started_at: Math.floor(Date.now() / 1000),
        ended_at: null,
        error: null,
        attempts: 1,
        voided: false,
      },
      {
        id: "op_design",
        name: "工序设计",
        seq: 2,
        status: "pending",
        gate: "auto",
        on_failure: "abort",
        mode: "agent",
        artifacts: [],
        started_at: null,
        ended_at: null,
        error: null,
        attempts: 0,
        voided: false,
      },
    ],
    pending_gate: {
      step_id: "confirm_l1",
      name: "确认 L1",
      gate: "confirm",
      allow_edit: true,
      artifacts: [
        { name: "l1_rows", schema: "table:12cols", value: [["A", "钢", 2]] },
      ],
    },
    output: null,
    output_kind: "markdown",
    ...overrides,
  };
}

/**
 * Which steps of the definition allow a human write. ``confirm_l1`` is the step
 * the engine's own scenario declares it on; ``extract_l1`` deliberately does not.
 */
const EDITABLE_STEPS = { confirm_l1: true, extract_l1: false, op_design: false };


function renderPanel(
  run: FeatureStepRun = awaitingGateRun(),
  editableSteps: Record<string, boolean> = EDITABLE_STEPS,
) {
  const onRunChange = vi.fn();
  render(
    <FeatureRunSteps
      featureId="bom-extract"
      run={run}
      onRunChange={onRunChange}
      editableSteps={editableSteps}
    />,
  );
  return { onRunChange };
}

describe("<FeatureRunSteps />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("shows every step's own state and what the gate is about to release", () => {
    renderPanel();

    expect(screen.getByText("提取 L1")).toBeInTheDocument();
    expect(screen.getByText("确认 L1")).toBeInTheDocument();
    expect(screen.getByText("工序设计")).toBeInTheDocument();
    expect(screen.getByText("features.runStepStatusSucceeded")).toBeInTheDocument();
    expect(screen.getByText("features.runStepStatusPending")).toBeInTheDocument();
    // The gate names what it releases, and the artifact carries its schema.
    expect(screen.getByText("features.runGateDeliverables")).toBeInTheDocument();
    expect(screen.getAllByText("l1_rows").length).toBeGreaterThan(0);
    expect(screen.getAllByText("table:12cols").length).toBeGreaterThan(0);
  });

  it("continues the same run, carrying only what the person changed", async () => {
    const user = userEvent.setup();
    const next = awaitingGateRun({ status: "succeeded", pending_gate: null });
    approveFeatureRun.mockResolvedValue(next);
    const { onRunChange } = renderPanel();

    // Changed straight through the textarea: the value is JSON, and typing
    // bracket by bracket only tests the test.
    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: '[["A","钢",3]]' },
    });
    await user.click(
      screen.getByRole("button", { name: "features.runGateApprove" }),
    );

    await waitFor(() => expect(approveFeatureRun).toHaveBeenCalledOnce());
    // The same task id: approving releases the run that stopped, it does not
    // start another one.
    expect(approveFeatureRun).toHaveBeenCalledWith("bom-extract", TASK, {
      l1_rows: [["A", "钢", 3]],
    });
    expect(onRunChange).toHaveBeenCalledWith(next);
  });

  it("approves a gate nobody edited without inventing a change", async () => {
    const user = userEvent.setup();
    approveFeatureRun.mockResolvedValue(awaitingGateRun({ pending_gate: null }));
    renderPanel();

    await user.click(
      screen.getByRole("button", { name: "features.runGateApprove" }),
    );

    await waitFor(() => expect(approveFeatureRun).toHaveBeenCalledOnce());
    expect(approveFeatureRun).toHaveBeenCalledWith("bom-extract", TASK, undefined);
  });

  it("refuses a draft that is not JSON instead of sending it as text", async () => {
    const user = userEvent.setup();
    renderPanel();

    fireEvent.change(screen.getByRole("textbox"), {
      target: { value: "客户名写全称" },
    });
    await user.click(
      screen.getByRole("button", { name: "features.runGateApprove" }),
    );

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    // Refused as itself, naming the artifact it could not read.
    expect(message.error).toHaveBeenCalledWith("features.runInvalidJson");
    expect(approveFeatureRun).not.toHaveBeenCalled();
  });

  it("shows a gate that allows no editing as read-only", () => {
    const run = awaitingGateRun();
    renderPanel({
      ...run,
      pending_gate: { ...run.pending_gate!, allow_edit: false },
    });

    expect(screen.getByText("features.runGateReadOnly")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    // The artifacts are still shown: the person is asked to release them.
    expect(screen.getByText(/\[\s*\[\s*"A",\s*"钢",\s*2\s*\]\s*\]/)).toBeInTheDocument();
  });

  it("offers a correction only for artifacts whose step allows one", async () => {
    const user = userEvent.setup();
    const run = awaitingGateRun();
    const rewound = awaitingGateRun({ status: "running", pending_gate: null });
    rewindFeatureRun.mockResolvedValue(rewound);
    // The target has to be a step that already ran, with two earlier producers:
    // one that refuses edits and one that allows them.
    const { onRunChange } = renderPanel({
      ...run,
      steps: [
        {
          ...run.steps[0],
          artifacts: [{ name: "raw_rows", schema: "text", value: "原始行" }],
        },
        { ...run.steps[1], status: "succeeded" },
        {
          ...run.steps[2],
          status: "succeeded",
          artifacts: [{ name: "ops", schema: "list", value: ["下料"] }],
        },
        { ...run.steps[2], id: "self_check", name: "自检清单验证", seq: 3, status: "running" },
      ],
    });

    // Two steps have a correctable artifact before them (extract_l1 does not
    // allow edits, confirm_l1 does); the run is stopped at the last of them.
    await user.click(
      screen.getAllByRole("button", { name: "features.runStepRewindFix" })[1],
    );

    const dialog = within(await screen.findByRole("dialog"));
    expect(dialog.getAllByText("features.runRewindStepReadOnly")).toHaveLength(2);
    expect(dialog.getByText("原始行")).toBeInTheDocument();
    expect(dialog.getAllByRole("textbox")).toHaveLength(1);

    fireEvent.change(dialog.getByRole("textbox"), {
      target: { value: '[["A","钢",9]]' },
    });
    await user.click(
      dialog.getByRole("button", { name: "features.runRewindConfirm" }),
    );

    await waitFor(() => expect(rewindFeatureRun).toHaveBeenCalledOnce());
    // The correction names the artifact of the step that allows one — and nothing
    // from the step that does not, which the server would refuse as a whole.
    expect(rewindFeatureRun).toHaveBeenCalledWith(
      "bom-extract",
      TASK,
      "self_check",
      { l1_rows: [["A", "钢", 9]] },
    );
    expect(onRunChange).toHaveBeenCalledWith(rewound);
  });

  it("hides the correction entry where no earlier step allows an edit", () => {
    renderPanel(awaitingGateRun(), { extract_l1: false, confirm_l1: false });

    // A doomed request is not offered: the plain rerun is still there.
    expect(
      screen.queryByRole("button", { name: "features.runStepRewindFix" }),
    ).toBeNull();
    expect(
      screen.getAllByRole("button", { name: "features.runStepRewind" }).length,
    ).toBeGreaterThan(0);
  });

  it("reruns from a step without edits when nothing was changed", async () => {
    const user = userEvent.setup();
    rewindFeatureRun.mockResolvedValue(awaitingGateRun());
    renderPanel();

    await user.click(
      screen.getAllByRole("button", { name: "features.runStepRewind" })[1],
    );
    await user.click(
      await screen.findByRole("button", { name: "features.runRewindConfirm" }),
    );

    await waitFor(() => expect(rewindFeatureRun).toHaveBeenCalledOnce());
    expect(rewindFeatureRun).toHaveBeenCalledWith(
      "bom-extract",
      TASK,
      "confirm_l1",
      undefined,
    );
  });

  it("shows a failed run as delivered-nothing, with the step's own error", () => {
    renderPanel(
      awaitingGateRun({
        status: "failed",
        pending_gate: null,
        steps: [
          {
            ...awaitingGateRun().steps[0],
            status: "failed",
            error: "总装图与 BOM 的层级列不一致",
          },
        ],
      }),
    );

    expect(screen.getByText("features.runFailedTitle")).toBeInTheDocument();
    expect(
      screen.getByText("总装图与 BOM 的层级列不一致"),
    ).toBeInTheDocument();
  });

  it("shows an escalated run as one that asked instead of deciding", () => {
    renderPanel(
      awaitingGateRun({
        status: "escalated",
        pending_gate: null,
        steps: [
          {
            ...awaitingGateRun().steps[0],
            status: "escalated",
            error: "第 5 项在 BOM 与总装图中冲突，请裁定",
          },
        ],
      }),
    );

    expect(screen.getByText("features.runEscalatedTitle")).toBeInTheDocument();
    expect(
      screen.getByText("第 5 项在 BOM 与总装图中冲突，请裁定"),
    ).toBeInTheDocument();
  });

  it("shows what a person changed at a gate, before and after", async () => {
    const user = userEvent.setup();
    const audit: FeatureRunAudit = {
      task_id: TASK,
      feature_id: "bom-extract",
      status: "awaiting_gate",
      snapshot: { model: "openai/gpt-4o" },
      steps: [
        {
          ...awaitingGateRun().steps[1],
          inputs: [{ name: "bom_rows", schema: "table", value: [["L1", "A"]] }],
          human_edits: [
            {
              artifact: "l1_rows",
              kind: "edit",
              before: "切削速度 120",
              after: "切削速度 100",
              by_user_id: 7,
              at: Math.floor(Date.now() / 1000),
              source: "approve",
            },
          ],
        },
      ],
    };
    getFeatureRunAudit.mockResolvedValue(audit);
    renderPanel();

    await user.click(
      screen.getByRole("button", { name: "features.runAuditOpen" }),
    );

    expect(await screen.findByText("features.runAuditHumanEdits")).toBeInTheDocument();
    expect(screen.getByText("features.runAuditBefore")).toBeInTheDocument();
    expect(screen.getByText("features.runAuditAfter")).toBeInTheDocument();
    // Both sides of the correction, exactly as the audit recorded them.
    expect(screen.getByText("切削速度 120")).toBeInTheDocument();
    expect(screen.getByText("切削速度 100")).toBeInTheDocument();
    expect(screen.getByText("features.runAuditKindEdit")).toBeInTheDocument();
    // Seconds read as milliseconds would have rendered as 1970.
    expect(screen.queryByText(/1970/)).toBeNull();
    expect(getFeatureRunAudit).toHaveBeenCalledWith("bom-extract", TASK);
  });

  it("shows what a rerun discarded as discarded, not as an edit to nothing", async () => {
    const user = userEvent.setup();
    const audit: FeatureRunAudit = {
      task_id: TASK,
      feature_id: "bom-extract",
      status: "succeeded",
      snapshot: {},
      steps: [
        {
          ...awaitingGateRun().steps[2],
          inputs: [{ name: "l1_rows", schema: "list", value: ["L1 001 机架"] }],
          artifacts: [
            { name: "ops", schema: "list", value: ["下料", "机加工", "检验"] },
          ],
          human_edits: [
            {
              artifact: "ops",
              kind: "void",
              before: ["下料", "机加工"],
              after: null,
              by_user_id: 1,
              at: Math.floor(Date.now() / 1000),
              source: "rewind",
            },
          ],
        },
      ],
    };
    getFeatureRunAudit.mockResolvedValue(audit);
    renderPanel();

    await user.click(
      screen.getByRole("button", { name: "features.runAuditOpen" }),
    );

    expect(
      await screen.findByText("features.runAuditKindVoid"),
    ).toBeInTheDocument();
    // The discarded value is on screen, and the other side says it is gone.
    expect(screen.getByText("features.runAuditBeforeVoided")).toBeInTheDocument();
    expect(screen.getByText("features.runAuditVoided")).toBeInTheDocument();
    // The discarded value itself is on screen: the rerun replaced it, nobody
    // edited it into nothing.
    expect(
      screen.getByText(/\[\s*"下料",\s*"机加工"\s*\]/),
    ).toBeInTheDocument();
  });

  it("keeps a refused edit on screen with the reason the server gave", async () => {
    const user = userEvent.setup();
    approveFeatureRun.mockRejectedValue(
      new Error(
        'Request failed: 400 Bad Request - {"error":{"code":"FEATURE_STEP_INVALID",' +
          '"message":"artifact l1_rows must stay a table:12cols"}}',
      ),
    );
    renderPanel();

    await user.click(
      screen.getByRole("button", { name: "features.runGateApprove" }),
    );

    await waitFor(() => expect(message.error).toHaveBeenCalled());
    expect(message.error).toHaveBeenCalledWith(
      expect.stringContaining("must stay a table:12cols"),
    );
  });
});
