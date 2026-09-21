import { describe, expect, it } from "vitest";
import type { Feature, FeatureStep } from "../../../api/modules/features";
import {
  artifactNamesBefore,
  emptyFormValues,
  featureToFormValues,
  formValuesToDefinition,
  incompleteCopyFields,
  isValidFeatureId,
  normalizeSchema,
  schemaForType,
  unsupportedSteps,
  validateGateProblems,
  type FeatureFormValues,
} from "./featureSettings";

/**
 * The settings editor rebuilds the whole definition on save, so the rows the
 * user sees are the only input: whatever they do to those rows has to reach
 * ``input_schema``, ``required`` and ``ui_schema.order`` together. These cases
 * hold that mapping still — an ``order`` that drifts from the properties (or a
 * dropped nested element) renders the run form wrong, and nothing else notices.
 */

function featureFixture(overrides: Partial<Feature> = {}): Feature {
  return {
    id: "quote-draft",
    version: 1,
    label: { zh: "报价单草稿", en: "Quote draft" },
    description: { zh: "生成报价单", en: "Draft a quote" },
    icon_name: "receipt",
    color: "#e5484d",
    unit: "sales",
    output_kind: "markdown",
    permissions: { allow_units: ["*"], allow_roles: ["member", "admin"] },
    input_schema: {
      type: "object",
      title: { zh: "输入", en: "Inputs" },
      properties: {
        customer: { type: "string", title: { zh: "客户", en: "Customer" } },
        items: {
          type: "array",
          items: {
            type: "array",
            items: { type: "string" },
          },
          description: { zh: "明细", en: "Line items" },
        },
        deadline: { type: "string", format: "date" },
      },
      required: ["customer"],
    },
    ui_schema: { order: ["customer", "items", "deadline"] },
    user_template: "{{inputs}}",
    system_prompt: "You draft quotes.",
    agent: null,
    ...overrides,
  };
}

/** The fixture loaded into the form, ready for an edit. */
function fixtureValues(): FeatureFormValues {
  return featureToFormValues(featureFixture());
}

describe("feature settings form ⇄ definition", () => {
  it("reads the fields in the order the run form renders them", () => {
    const values = featureToFormValues(
      featureFixture({
        // ``order`` disagrees with ``properties``; the run form follows ``order``.
        ui_schema: { order: ["deadline", "customer", "items"] },
      }),
    );

    expect(values.fields.map((field) => field.name)).toEqual([
      "deadline",
      "customer",
      "items",
    ]);
    expect(values.fields[1].required).toBe(true);
  });

  it("keeps order, properties and required in step when a field moves", () => {
    const values = fixtureValues();
    const [customer, items, deadline] = values.fields;
    const definition = formValuesToDefinition(
      { ...values, fields: [deadline, customer, items] },
      featureFixture(),
    );

    expect(definition.ui_schema.order).toEqual(["deadline", "customer", "items"]);
    expect(Object.keys(definition.input_schema.properties)).toEqual([
      "deadline",
      "customer",
      "items",
    ]);
    expect(definition.input_schema.required).toEqual(["customer"]);
    // A moved field keeps its own schema, options included.
    expect(definition.input_schema.properties.deadline).toEqual({
      type: "string",
      format: "date",
    });
  });

  it("drops everything a removed field owned", () => {
    const values = fixtureValues();
    const kept = values.fields.filter((field) => field.name !== "customer");
    const existing = featureFixture({
      input_schema: {
        type: "object",
        properties: {
          customer: { type: "string" },
          deadline: { type: "string", format: "date" },
        },
        required: ["customer"],
      },
      ui_schema: { order: ["customer", "deadline"], widgets: { customer: "textarea" } },
    });
    const definition = formValuesToDefinition({ ...values, fields: kept }, existing);

    expect(definition.input_schema.required ?? []).not.toContain("customer");
    expect(definition.input_schema.properties).not.toHaveProperty("customer");
    expect(definition.ui_schema.order).not.toContain("customer");
    expect(definition.ui_schema.widgets?.customer).toBeUndefined();
  });

  it("adds a field to the end and keeps an untouched widget override", () => {
    const values = fixtureValues();
    const definition = formValuesToDefinition(
      {
        ...values,
        fields: [
          ...values.fields,
          { name: "note", required: false, schema: { type: "string" } },
        ],
      },
      featureFixture({
        ui_schema: { order: ["customer"], widgets: { customer: "textarea" } },
      }),
    );

    expect(definition.ui_schema.order.at(-1)).toBe("note");
    expect(definition.ui_schema.widgets).toEqual({ customer: "textarea" });
  });

  it("keeps a nested array element schema through a round trip", () => {
    const values = fixtureValues();
    const definition = formValuesToDefinition(values, featureFixture());

    expect(definition.input_schema.properties.items.items).toEqual({
      type: "array",
      items: { type: "string" },
    });
    // Re-reading the written document yields the same editor state.
    const reloaded = featureToFormValues(featureFixture({
      input_schema: definition.input_schema,
      ui_schema: definition.ui_schema,
    }));
    expect(reloaded.fields).toEqual(values.fields);
  });

  it("declares a restriction only for the lists the user filled in", () => {
    const values = fixtureValues();
    const withoutRoles = formValuesToDefinition(
      { ...values, allowRoles: [] },
      null,
    );
    const empty = formValuesToDefinition(
      { ...values, allowUnits: [], allowRoles: [] },
      null,
    );

    expect(withoutRoles.permissions).toEqual({ allow_units: ["*"] });
    expect(empty.permissions).toEqual({});
  });

  it("sends a blank prompt as an absent one", () => {
    const values = fixtureValues();
    const definition = formValuesToDefinition(
      { ...values, systemPrompt: "   ", userTemplate: " {{inputs}} " },
      null,
    );

    expect(definition.prompt).toEqual({
      user_template: "{{inputs}}",
      system_prompt: null,
    });
    expect(definition.color).toBe("#e5484d");
  });

  it("starts a new feature without invented restrictions or colour", () => {
    const definition = formValuesToDefinition(
      emptyFormValues({
        units: ["sales"],
        icons: ["receipt"],
        output_kinds: ["markdown", "json", "text"],
        bundled_ids: [],
      }),
      null,
    );

    expect(definition.color).toBeNull();
    expect(definition.output).toEqual({ kind: "markdown" });
    expect(definition.permissions).toEqual({});
  });

  it("forgets the options a new type cannot carry", () => {
    const asText = normalizeSchema({
      type: "string",
      format: "textarea",
      enum: ["a", "b"],
      title: { zh: "标题", en: "Title" },
    });
    const asNumber = normalizeSchema(schemaForType(asText, "number"));
    const asList = normalizeSchema(schemaForType(asText, "array"));

    expect(asNumber).toEqual({
      type: "number",
      title: { zh: "标题", en: "Title" },
    });
    // A scalar turned into a list becomes a list of that scalar.
    expect(asList).toEqual({
      type: "array",
      items: { type: "string" },
      title: { zh: "标题", en: "Title" },
    });
  });

  it("names the rows whose bilingual copy is half-filled", () => {
    const values = fixtureValues();
    const halved = values.fields.map((field) =>
      field.name === "deadline"
        ? { ...field, schema: { ...field.schema, title: { zh: "截止", en: "" } } }
        : field,
    );

    expect(incompleteCopyFields(halved)).toEqual(["deadline"]);
    expect(incompleteCopyFields(values.fields)).toEqual([]);
  });

  it("gives an array the element the editor shows when the definition omits it", () => {
    const values = featureToFormValues(
      featureFixture({
        input_schema: { type: "object", properties: { tags: { type: "array" } } },
      }),
    );
    const definition = formValuesToDefinition(values, null);

    expect(definition.input_schema.properties.tags).toEqual({
      type: "array",
      items: { type: "string" },
    });
  });

  it("rejects ids the server would refuse as a directory name", () => {
    expect(isValidFeatureId("quote-draft")).toBe(true);
    expect(isValidFeatureId("quote_draft-v2")).toBe(true);
    expect(isValidFeatureId("")).toBe(false);
    expect(isValidFeatureId("quote/draft")).toBe(false);
    expect(isValidFeatureId("quote draft")).toBe(false);
    expect(isValidFeatureId("Quote-Draft")).toBe(false);
    expect(isValidFeatureId("-quote")).toBe(false);
    expect(isValidFeatureId("q".repeat(65))).toBe(false);
  });
});

describe("capability layer ⇄ form", () => {
  /** The definition's own capability block, as the editor holds it. */
  function withCapability(agent: Partial<Feature["agent"]>): Feature {
    return featureFixture({ agent: agent as Feature["agent"] });
  }

  it("writes no layer at all when nothing is declared", () => {
    const definition = formValuesToDefinition(fixtureValues(), null);

    expect(definition.agent).toBeNull();
  });

  it("keeps inheritance and an explicit none apart", () => {
    const values = featureToFormValues(
      withCapability({ skills: [], subagents: ["writer"] }),
    );

    expect(values.skills).toEqual([]);
    expect(values.subagents).toEqual(["writer"]);
    expect(values.mcp_servers).toBeUndefined();

    const definition = formValuesToDefinition(values, null);

    // ``skills: []`` means "no skills in this run"; an absent key means the
    // caller's own agent decides — collapsing either one widens the run.
    expect(definition.agent).toEqual({ skills: [], subagents: ["writer"] });
  });

  it("carries a declared model and the runtime knobs back out", () => {
    const values = featureToFormValues(
      withCapability({
        model: "openai/gpt-4o",
        temperature: 0.3,
        max_tokens: 1024,
        max_iters: 40,
        max_input_length: 64_000,
        tools_disabled: ["browser_use"],
      }),
    );

    expect(values.agentModel).toBe("openai/gpt-4o");
    expect(formValuesToDefinition(values, null).agent).toEqual({
      model: "openai/gpt-4o",
      temperature: 0.3,
      max_tokens: 1024,
      max_iters: 40,
      max_input_length: 64_000,
      tools_disabled: ["browser_use"],
    });
  });

  it("reads an inherited model as the picker's auto value", () => {
    const values = featureToFormValues(withCapability({ temperature: 0.1 }));

    expect(values.agentModel).toBe("");
    expect(formValuesToDefinition(values, null).agent).toEqual({
      temperature: 0.1,
    });
  });

  it("drops the keys the author cleared rather than writing nulls", () => {
    const values = featureToFormValues(
      withCapability({
        model: "openai/gpt-4o",
        temperature: 0.3,
        knowledge_base_ids: ["kb-1"],
      }),
    );

    const definition = formValuesToDefinition(
      {
        ...values,
        agentModel: "",
        temperature: null,
        knowledge_base_ids: undefined,
      },
      null,
    );

    expect(definition.agent).toBeNull();
  });
});

/**
 * The step skeleton (design 7.10) rides through the same form. What matters here
 * is the omitted-vs-declared difference the run depends on: an inherited tool
 * scope is not "no tools", an automatic gate carries no ``allow_edit``, and a
 * step key this build cannot name is never deleted by a save that did not show it.
 */
describe("feature settings form ⇄ step skeleton", () => {
  function stepFixture(overrides: Partial<FeatureStep> = {}): FeatureStep {
    return {
      id: "extract_l1",
      name: "提取 L1",
      mode: "agent",
      inputs: ["bom_rows"],
      output: { name: "l1_rows", schema: "table:12cols" },
      prompt: "读出层级列，过滤 L1",
      gate: "auto",
      on_failure: "abort",
      ...overrides,
    };
  }

  function withSteps(steps: FeatureStep[]): FeatureFormValues {
    return { ...fixtureValues(), steps };
  }

  it("round-trips the skeleton in order, with what each step consumes and produces", () => {
    const definition = formValuesToDefinition(
      withSteps([
        stepFixture({ id: "read_bom", name: "读 BOM", inputs: [], gate: "auto" }),
        stepFixture({ gate: "confirm", allow_edit: true }),
      ]),
      null,
    );

    expect(definition.steps?.map((step) => step.id)).toEqual([
      "read_bom",
      "extract_l1",
    ]);
    expect(definition.steps?.[1]).toMatchObject({
      inputs: ["bom_rows"],
      output: { name: "l1_rows", schema: "table:12cols" },
      gate: "confirm",
      allow_edit: true,
    });
  });

  it("keeps a declared tool scope apart from an inherited one", () => {
    const [declared, inherited] = formValuesToDefinition(
      withSteps([
        stepFixture({ id: "a", tools: [] }),
        stepFixture({ id: "b", tools: undefined }),
      ]),
      null,
    ).steps ?? [];

    // "This step may use no tools" and "leave the run's tools alone" are two
    // different runs; collapsing either into the other widens or narrows it.
    expect(declared.tools).toEqual([]);
    expect("tools" in inherited).toBe(false);
  });

  it("writes the human-edit switch only for a gate that can stop for a person", () => {
    const steps = formValuesToDefinition(
      withSteps([
        stepFixture({ id: "auto_step", gate: "auto", allow_edit: true }),
        stepFixture({ id: "confirm_step", gate: "confirm", allow_edit: false }),
        stepFixture({ id: "validate_step", gate: "validate", allow_edit: true }),
      ]),
      null,
    ).steps ?? [];

    // The server refuses ``allow_edit`` on an automatic gate as an unused field.
    expect("allow_edit" in steps[0]).toBe(false);
    expect(steps[1].allow_edit).toBe(false);
    expect(steps[2].allow_edit).toBe(true);
  });

  it("keeps a step key this build cannot name", () => {
    const exotic = {
      ...stepFixture(),
      retry_limit: 3,
    } as FeatureStep;
    const written = formValuesToDefinition(withSteps([exotic]), null).steps?.[0];

    expect((written as Record<string, unknown>).retry_limit).toBe(3);
  });

  it("writes no steps key at all when the author declared none", () => {
    const definition = formValuesToDefinition(withSteps([]), null);

    // Absence is what "single-shot" already means; an empty list would claim
    // the editor had looked at a skeleton that is not there.
    expect("steps" in definition).toBe(false);
  });

  it("names the steps this build cannot run", () => {
    const unsupported = unsupportedSteps([
      stepFixture({ id: "op_design", mode: "orchestrate" }),
      stepFixture({ id: "tooling", agent_role: "刀具选型" }),
      stepFixture({ id: "fine" }),
    ]);

    expect(unsupported.orchestrate).toEqual(["op_design"]);
    expect(unsupported.agentRole).toEqual(["tooling"]);
  });

  it("names a validate gate whose artifact could never pass", () => {
    const problems = validateGateProblems([
      stepFixture({ id: "self_check", gate: "validate", output: { name: "check", schema: "table:4cols" } }),
      stepFixture({ id: "self_check_ok", gate: "validate", output: { name: "check", schema: "object" } }),
      stepFixture({ id: "no_gate" }),
    ]);

    expect(problems).toEqual(["self_check"]);
  });

  it("suggests only the artifacts earlier steps produce", () => {
    const steps = [
      stepFixture({ id: "a", output: { name: "bom_rows", schema: "table" } }),
      stepFixture({ id: "b", output: { name: "drawing", schema: "text" } }),
      stepFixture({ id: "c" }),
    ];

    expect(artifactNamesBefore(steps, 0)).toEqual([]);
    expect(artifactNamesBefore(steps, 2)).toEqual(["bom_rows", "drawing"]);
  });
});
