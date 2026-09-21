import { beforeEach, describe, expect, it, vi } from "vitest";

const { request } = vi.hoisted(() => ({ request: vi.fn() }));

vi.mock("../request", () => ({ request }));

import { featuresApi } from "./features";

/**
 * The run endpoints are the contract with the step engine, so their paths,
 * verbs and bodies are pinned here: a wrong path is a 404 nobody sees until a
 * run is stuck at a gate with no way to release it.
 */

beforeEach(() => {
  request.mockClear();
});

describe("featuresApi run endpoints", () => {
  it("reads the step state of one run", () => {
    featuresApi.getFeatureRun("bom extract", "task-1");

    expect(request).toHaveBeenCalledWith("/features/bom%20extract/runs/task-1");
  });

  it("approves the gate, with the edits when there are any", () => {
    featuresApi.approveFeatureRun("bom", "task-1", { l1_rows: [["A", "钢", 3]] });
    featuresApi.approveFeatureRun("bom", "task-1");

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/features/bom/runs/task-1/approve",
      {
        method: "POST",
        body: JSON.stringify({ edits: { l1_rows: [["A", "钢", 3]] } }),
      },
    );
    // No edits means no ``edits`` key: the gate is released as it stands.
    expect(request).toHaveBeenNthCalledWith(2, "/features/bom/runs/task-1/approve", {
      method: "POST",
      body: JSON.stringify({}),
    });
  });

  it("rewinds to a named step, with a correction or without one", () => {
    featuresApi.rewindFeatureRun("bom", "task-1", "op_design", {
      l1_rows: [["A", "钢", 9]],
    });
    featuresApi.rewindFeatureRun("bom", "task-1", "op_design");

    expect(request).toHaveBeenNthCalledWith(
      1,
      "/features/bom/runs/task-1/rewind",
      {
        method: "POST",
        body: JSON.stringify({
          to_step: "op_design",
          edits: { l1_rows: [["A", "钢", 9]] },
        }),
      },
    );
    // A plain rerun names the step and injects nothing.
    expect(request).toHaveBeenNthCalledWith(2, "/features/bom/runs/task-1/rewind", {
      method: "POST",
      body: JSON.stringify({ to_step: "op_design" }),
    });
  });

  it("reads the audit of one run", () => {
    featuresApi.getFeatureRunAudit("bom", "task-1");

    expect(request).toHaveBeenCalledWith("/features/bom/runs/task-1/audit");
  });
});
