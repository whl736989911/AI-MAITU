import { describe, expect, it } from "vitest";
import type { Feature } from "../../../api/modules/features";
import {
  emptyFormValues,
  featureToFormValues,
  formValuesToDefinition,
  incompleteCopyFields,
  isValidFeatureId,
  normalizeSchema,
  schemaForType,
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
