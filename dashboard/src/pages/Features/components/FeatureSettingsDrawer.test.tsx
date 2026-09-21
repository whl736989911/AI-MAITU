import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  Feature,
  FeatureCapabilities,
  FeatureDefinitionBody,
  FeatureMeta,
} from "../../../api/modules/features";

/**
 * The settings drawer is the only writer of ``feature.json``, so what the user
 * does to the rows has to land in the request body: these cases drive the real
 * antd form (field list, row tools, footer) and read the payload the API mock
 * received. The pure mapping is covered in ``featureSettings.test.ts``.
 */

const { createFeature, updateFeature, deleteFeature, getFeatureCapabilities } =
  vi.hoisted(() => ({
    createFeature: vi.fn(),
    updateFeature: vi.fn(),
    deleteFeature: vi.fn(),
    getFeatureCapabilities: vi.fn(),
  }));

vi.mock("../../../api/modules/features", () => ({
  featuresApi: {
    createFeature,
    updateFeature,
    deleteFeature,
    getFeatureCapabilities,
    listFeatures: vi.fn(),
    getFeature: vi.fn(),
    getFeatureMeta: vi.fn(),
    runFeature: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import FeatureSettingsDrawer from "./FeatureSettingsDrawer";

// Every case here drives a real antd form; the 5s default is a coin flip once
// the suite runs its files in parallel.
vi.setConfig({ testTimeout: 30_000 });

const META: FeatureMeta = {
  units: ["general", "sales"],
  icons: ["receipt", "file-text"],
  output_kinds: ["markdown", "json", "text"],
  bundled_ids: [],
};

const FEATURE: Feature = {
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
      items: { type: "array", items: { type: "array", items: { type: "string" } } },
      deadline: { type: "string", format: "date" },
    },
    required: ["customer"],
  },
  ui_schema: { order: ["customer", "items", "deadline"], widgets: { customer: "textarea" } },
  user_template: "{{inputs}}",
  system_prompt: "You draft quotes.",
  agent: null,
};

/** What ``GET /features/_capabilities`` answers for this admin. */
const CAPABILITIES: FeatureCapabilities = {
  models: [{ ref: "openai/gpt-4o", label: "gpt-4o" }],
  tools: [
    { name: "browser_use", category: "web" },
    { name: "read_file", category: "files" },
  ],
  skills: ["meeting-notes"],
  subagents: ["researcher"],
  mcp_servers: [{ name: "github", label: "GitHub" }],
  knowledge_bases: [{ id: "kb-1", name: "报价口径" }],
};

/** Last write the mocked API saw, as ``[id, body]``. */
function lastUpdate(): [string, FeatureDefinitionBody] {
  const call = updateFeature.mock.calls.at(-1);
  return call as [string, FeatureDefinitionBody];
}

/** The drawer in edit mode, with a spy for every exit it can take. */
function renderDrawer(
  meta: FeatureMeta = META,
  callbacks: { onSaved?: (id: string, created: boolean) => void } = {},
) {
  return render(
    <FeatureSettingsDrawer
      open
      feature={FEATURE}
      meta={meta}
      onClose={vi.fn()}
      onSaved={callbacks.onSaved ?? vi.fn()}
      onDeleted={vi.fn()}
    />,
  );
}

describe("<FeatureSettingsDrawer />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  it("seeds from the definition and writes the edited document back", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    renderDrawer(META, { onSaved });

    expect(screen.getByDisplayValue("quote-draft")).toBeInTheDocument();
    expect(screen.getByDisplayValue("报价单草稿")).toBeInTheDocument();
    expect(screen.getByDisplayValue("You draft quotes.")).toBeInTheDocument();

    await user.click(
      screen.getByRole("button", { name: "features.settingsFieldsAdd" }),
    );
    const nameInputs = screen.getAllByPlaceholderText(
      "features.settingsFieldNamePlaceholder",
    );
    await user.type(nameInputs.at(-1)!, "note");
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [id, body] = lastUpdate();
    expect(id).toBe("quote-draft");
    expect(body.ui_schema.order).toEqual([
      "customer",
      "items",
      "deadline",
      "note",
    ]);
    expect(body.input_schema.required).toEqual(["customer"]);
    expect(body.input_schema.properties.note).toEqual({ type: "string" });
    // Parts the editor does not own survive the round trip.
    expect(body.input_schema.title).toEqual({ zh: "输入", en: "Inputs" });
    expect(body.ui_schema.widgets).toEqual({ customer: "textarea" });
    expect(body.prompt).toEqual({
      user_template: "{{inputs}}",
      system_prompt: "You draft quotes.",
    });
    expect(body.permissions).toEqual({
      allow_units: ["*"],
      allow_roles: ["member", "admin"],
    });
    expect(onSaved).toHaveBeenCalledWith("quote-draft", false);
  });

  it("keeps ui_schema.order in step when a row is moved", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(
      screen.getAllByRole("button", {
        name: "features.settingsFieldMoveDown",
      })[0],
    );
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [, body] = lastUpdate();
    expect(body.ui_schema.order).toEqual(["items", "customer", "deadline"]);
    expect(Object.keys(body.input_schema.properties)).toEqual([
      "items",
      "customer",
      "deadline",
    ]);
  });

  it("refuses to save two fields sharing one name", async () => {
    const user = userEvent.setup();
    renderDrawer();

    const nameInputs = screen.getAllByPlaceholderText(
      "features.settingsFieldNamePlaceholder",
    );
    await user.clear(nameInputs[2]);
    await user.type(nameInputs[2], "customer");
    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      await screen.findByText("features.settingsFieldNameDuplicate"),
    ).toBeInTheDocument();
    expect(updateFeature).not.toHaveBeenCalled();
  });

  it("offers deletion only for a definition the app does not ship", async () => {
    const user = userEvent.setup();
    const { rerender } = renderDrawer({ ...META, bundled_ids: ["quote-draft"] });

    expect(
      screen.queryByRole("button", { name: "features.settingsDelete" }),
    ).toBeNull();

    rerender(
      <FeatureSettingsDrawer
        open
        feature={FEATURE}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );
    await user.click(
      screen.getByRole("button", { name: "features.settingsDelete" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "common.delete" }),
    );

    await waitFor(() => expect(deleteFeature).toHaveBeenCalledWith("quote-draft"));
  });

  it("keeps a refused write on screen with the reason the server gave", async () => {
    const user = userEvent.setup();
    const onSaved = vi.fn();
    updateFeature.mockRejectedValue(
      new Error(
        'Request failed: 400 Bad Request - {"error":{"code":"FEATURE_INVALID",' +
          '"message":"invalid feature definition: input_schema.properties must be a non-empty object"}}',
      ),
    );
    renderDrawer(META, { onSaved });

    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      await screen.findByText(/input_schema\.properties must be a non-empty object/),
    ).toBeInTheDocument();
    expect(onSaved).not.toHaveBeenCalled();
  });

  it("asks for the capability choices only once the block is opened", async () => {
    const user = userEvent.setup();
    renderDrawer();

    // Opening the drawer must not start an agent: the choices that need one are
    // behind the disclosure.
    expect(getFeatureCapabilities).not.toHaveBeenCalled();

    await user.click(screen.getByText("features.settingsSectionCapability"));

    await waitFor(() => expect(getFeatureCapabilities).toHaveBeenCalledOnce());
    // The fields the choices feed only exist once they arrived.
    expect(
      await screen.findByText("features.settingsCapabilitySkills"),
    ).toBeInTheDocument();
  });

  it("keeps a declared capability layer that was never opened", async () => {
    const user = userEvent.setup();
    render(
      <FeatureSettingsDrawer
        open
        feature={{ ...FEATURE, agent: { model: "openai/gpt-4o", skills: [] } }}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    // Saving without opening the block must not drop what the definition
    // declares: its fields are unmounted, and antd only reports registered ones.
    expect(lastUpdate()[1].agent).toEqual({
      model: "openai/gpt-4o",
      skills: [],
    });
  });

  it("reports a failed capability load instead of offering empty lists", async () => {
    const user = userEvent.setup();
    getFeatureCapabilities.mockRejectedValue(
      new Error(
        'Request failed: 500 Internal Server Error - {"error":{"code":"AGENT_FAILED",' +
          '"message":"Agent 启动失败。"}}',
      ),
    );
    renderDrawer();

    await user.click(screen.getByText("features.settingsSectionCapability"));

    expect(
      await screen.findByText("features.settingsCapabilityLoadFailed"),
    ).toBeInTheDocument();
    // Empty lists would read as "this agent has none" and let the editor
    // declare a scope no run could honour.
    expect(
      screen.queryByText("features.settingsCapabilitySkills"),
    ).not.toBeInTheDocument();
  });

  it("writes the declared capability layer and drops what was left inherited", async () => {
    const user = userEvent.setup();
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
    render(
      <FeatureSettingsDrawer
        open
        feature={{
          ...FEATURE,
          agent: {
            model: "openai/gpt-4o",
            tools_disabled: ["browser_use"],
            skills: [],
          },
        }}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [, body] = lastUpdate();
    expect(body.agent).toEqual({
      model: "openai/gpt-4o",
      tools_disabled: ["browser_use"],
      // Declared as none, and still written as none.
      skills: [],
    });
  });
});

describe("<FeatureSettingsDrawer /> step skeleton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  /** The block's own disclosure, which is what mounts the step rows. */
  async function openSteps(user: ReturnType<typeof userEvent.setup>) {
    await user.click(screen.getByText("features.settingsSectionSteps"));
    return screen.findByRole("button", { name: "features.settingsStepsAdd" });
  }

  it("writes the step the author built, with orchestrate left unreachable", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(await openSteps(user));
    await user.type(screen.getByPlaceholderText("op_design"), "op_design");
    await user.type(
      screen.getByPlaceholderText("features.settingsStepNamePlaceholder"),
      "工序设计",
    );
    await user.type(screen.getByPlaceholderText("bom_rows"), "ops");
    await user.type(
      screen.getByLabelText("features.settingsStepOutputSchema"),
      "list",
    );
    await user.type(
      screen.getByLabelText("features.settingsStepPrompt"),
      "出工艺包",
    );

    // The self-decomposing mode is in the schema and not selectable: offering it
    // would promise a parallel decomposition this build refuses to run.
    const modeInput = document.querySelector<HTMLInputElement>(
      'input[id$="_mode"]',
    );
    expect(modeInput).not.toBeNull();
    await user.click(modeInput as HTMLInputElement);
    const orchestrate = await screen.findByTitle(
      "features.settingsStepModeOrchestrate",
    );
    expect(orchestrate.className).toContain("disabled");
    await user.click(orchestrate);

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [, body] = lastUpdate();
    expect(body.steps).toEqual([
      {
        id: "op_design",
        name: "工序设计",
        mode: "agent",
        inputs: [],
        output: { name: "ops", schema: "list" },
        prompt: "出工艺包",
        gate: "auto",
        on_failure: "abort",
      },
    ]);
  });

  it("refuses a loaded step this build cannot run, naming it", async () => {
    const user = userEvent.setup();
    render(
      <FeatureSettingsDrawer
        open
        feature={{
          ...FEATURE,
          steps: [
            {
              id: "op_design",
              name: "工序设计",
              mode: "orchestrate",
              inputs: [],
              output: { name: "ops", schema: "list" },
              prompt: "出工艺包",
              gate: "auto",
              on_failure: "abort",
            },
          ],
        }}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "common.save" }));

    // Reported next to the fields instead of only in the server's error body,
    // and nothing was written.
    expect(
      await screen.findByText("features.settingsStepUnsupportedMode"),
    ).toBeInTheDocument();
    expect(updateFeature).not.toHaveBeenCalled();
  });

  it("refuses a validate gate whose artifact could never pass", async () => {
    const user = userEvent.setup();
    render(
      <FeatureSettingsDrawer
        open
        feature={{
          ...FEATURE,
          steps: [
            {
              id: "self_check",
              name: "自检清单",
              mode: "agent",
              inputs: [],
              output: { name: "check", schema: "table:4cols" },
              prompt: "逐条核对",
              gate: "validate",
              allow_edit: false,
              on_failure: "abort",
            },
          ],
        }}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      await screen.findByText("features.settingsStepValidateSchemaRefused"),
    ).toBeInTheDocument();
    expect(updateFeature).not.toHaveBeenCalled();
  });

  it("reports a failed step-block load instead of offering an empty tool list", async () => {
    const user = userEvent.setup();
    getFeatureCapabilities.mockRejectedValue(
      new Error(
        'Request failed: 500 Internal Server Error - {"error":{"code":"AGENT_FAILED",' +
          '"message":"Agent 启动失败。"}}',
      ),
    );
    renderDrawer();

    await user.click(screen.getByText("features.settingsSectionSteps"));

    expect(
      await screen.findByText("features.settingsCapabilityLoadFailed"),
    ).toBeInTheDocument();
    // An empty tool list would read as "this step may use no tools" — a scope
    // nobody declared.
    expect(
      screen.queryByRole("button", { name: "features.settingsStepsAdd" }),
    ).toBeNull();
  });
});

describe("<FeatureSettingsDrawer /> scope placeholders", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  /** The placeholder the user sees on one scope select, found by its label. */
  function scopePlaceholder(labelKey: string): string | undefined {
    const item = screen.getByText(labelKey).closest(".ant-form-item");
    return (
      item?.querySelector(".ant-select-selection-placeholder")?.textContent ??
      undefined
    );
  }

  it("does not tell a declared-empty scope that it inherits", async () => {
    const user = userEvent.setup();
    render(
      <FeatureSettingsDrawer
        open
        feature={{ ...FEATURE, agent: { skills: [] } }}
        meta={META}
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onDeleted={vi.fn()}
      />,
    );

    await user.click(screen.getByText("features.settingsSectionCapability"));
    await screen.findByText("features.settingsCapabilitySkills");

    // "Skills: []" means the run uses none of them — the placeholder said
    // "inherit" and told the author the opposite of what the field does.
    expect(scopePlaceholder("features.settingsCapabilitySkills")).toBe(
      "features.settingsCapabilityScopeNone",
    );
  });

  it("keeps the inherit placeholder where the scope really is inherited", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.click(screen.getByText("features.settingsSectionCapability"));
    await screen.findByText("features.settingsCapabilitySkills");

    expect(scopePlaceholder("features.settingsCapabilitySkills")).toBe(
      "features.settingsCapabilityScopePlaceholder",
    );
  });
});
