/**
 * workflowDocument.test.ts — the editor's copy of the server's rules has to agree
 * with the server.
 *
 * ``validateWorkflowDocument`` exists so a save that cannot succeed never leaves the
 * browser, and its worth depends entirely on it saying the *same* things as
 * ``octop/infra/agents/feature_workflow.py``: a rule the editor does not know makes
 * the field-level list a lie, and a rule it invents refuses definitions the server
 * would have taken. So the cases here are the ones the two implementations could
 * drift apart on (the active-only requirements, ``depends_on`` pointing forward,
 * unsupported keys, bilingual pairs) plus the normalization the JSON tab shows.
 */

import { describe, expect, it } from "vitest";

import {
  emptyWorkflow,
  normalizeWorkflow,
  problemOwner,
  readWorkflowJson,
  sectionProblems,
  slugifyStepId,
  stepProblems,
  validateWorkflowDocument,
} from "./workflowDocument";

/** A definition that is complete in the way an active one has to be. */
const ACTIVE = {
  version: 1,
  status: "active",
  inputs: {
    type: "object",
    required: ["customer"],
    properties: {
      customer: {
        type: "string",
        title: { zh: "客户名称", en: "Customer" },
      },
    },
  },
  steps: [
    { id: "extract", name: "提取要点", prompt: "读出 {{inputs}}" },
    {
      id: "quote",
      name: "生成报价单",
      prompt: "按要点报价",
      depends_on: ["extract"],
    },
  ],
  outputs: [{ name: "报价单", form: "markdown", path: "outputs/quote.md" }],
  rules: ["金额逐行核对"],
};

describe("validateWorkflowDocument", () => {
  it("accepts a definition the server accepts", () => {
    expect(validateWorkflowDocument(ACTIVE)).toEqual([]);
    expect(validateWorkflowDocument(emptyWorkflow())).toEqual([]);
  });

  it("reports what an active definition owes that a draft does not", () => {
    const bare = {
      version: 1,
      status: "active",
      steps: [{ name: "提取要点" }],
    };
    expect(validateWorkflowDocument(bare)).toEqual([
      "steps[0].id is required for an active workflow",
      "steps[0].prompt is required for an active workflow",
    ]);
    // The same document as a draft says nothing about ids or prompts.
    expect(validateWorkflowDocument({ ...bare, status: "draft" })).toEqual([]);
  });

  it("refuses a dependency on a step that has not run yet", () => {
    const forward = {
      ...ACTIVE,
      steps: [
        { id: "extract", name: "提取要点", prompt: "…", depends_on: ["quote"] },
        { id: "quote", name: "报价", prompt: "…" },
      ],
    };
    expect(validateWorkflowDocument(forward)).toEqual([
      "steps[0].depends_on must name an earlier step ('quote')",
    ]);
  });

  it("refuses unknown keys and half-filled text pairs", () => {
    const problems = validateWorkflowDocument({
      version: 1,
      steps: [{ name: "提取要点", gate: "sometimes" }],
      inputs: {
        type: "object",
        properties: { customer: { type: "string", title: { zh: "客户名称" } } },
      },
      owner: "someone",
    });
    expect(problems).toEqual([
      "definition has unsupported keys: owner",
      "inputs.properties.customer.title must be a non-empty {'zh': …, 'en': …} pair",
      "steps[0].gate must be one of auto, confirm",
    ]);
  });
});

describe("normalizeWorkflow", () => {
  it("drops empty optional parts without dropping what was typed into them", () => {
    const normalized = normalizeWorkflow({
      version: 1,
      status: "draft",
      inputs: {
        type: "object",
        properties: {
          "": { type: "string", title: { zh: "名称", en: "Name" } },
        },
      },
      steps: [{ id: "", name: " 提取要点 ", prompt: "  ", depends_on: [] }],
    });
    expect(normalized).toEqual({
      version: 1,
      status: "draft",
      // The half-typed field keeps its (still empty) name: a field is its name, and
      // one silently dropped would be a form field the author thinks they declared.
      inputs: {
        type: "object",
        properties: {
          "": { type: "string", title: { zh: "名称", en: "Name" } },
        },
      },
      steps: [{ name: "提取要点" }],
    });
  });
});

describe("slugifyStepId", () => {
  it("derives the same legal ids the server derives", () => {
    expect(slugifyStepId("Extract Key Points")).toBe("extract_key_points");
    expect(slugifyStepId("3rd pass", 1)).toBe("step_3rd_pass");
    // Nothing ASCII to build from — the step's position names it instead.
    expect(slugifyStepId("提取要点", 3)).toBe("step_3");
    expect(slugifyStepId("提取要点")).toBe("step");
  });
});

describe("problemOwner", () => {
  it("attributes a problem to the step it names, so the outline can mark it", () => {
    const problems = [
      "steps[1].name is required",
      "steps[1].id duplicates steps[0].id",
      "steps may hold at most 24 entries",
      "rules[0] must be a non-empty string",
    ];
    expect(stepProblems(problems, 1)).toHaveLength(2);
    expect(sectionProblems(problems, "steps")).toEqual([
      "steps may hold at most 24 entries",
    ]);
    expect(sectionProblems(problems, "rules")).toHaveLength(1);
    expect(problemOwner("version must be 1").section).toBe("document");
  });
});

describe("readWorkflowJson", () => {
  it("answers with the document and, when it is not one, every reason why", () => {
    expect(readWorkflowJson(JSON.stringify(ACTIVE))).toEqual({
      workflow: ACTIVE,
      problems: [],
    });
    const broken = readWorkflowJson('{"version": 2, "steps": "one"}');
    expect(broken.workflow).toBeNull();
    expect(broken.problems).toEqual([
      "version must be 1",
      "steps must be an array",
    ]);
  });
});
