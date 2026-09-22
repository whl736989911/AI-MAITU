import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent, { type UserEvent } from "@testing-library/user-event";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";
import type {
  Feature,
  FeatureCapabilities,
  FeatureDefinitionBody,
  FeatureMeta,
} from "../../api/modules/features";

/**
 * The settings page is the only writer of ``feature.json``, so what the author
 * does to the blocks has to land in the request body: these cases drive the real
 * antd form through the real URL tabs and read the payload the API mock
 * received. The pure mapping is covered in ``featureSettings.test.ts``.
 *
 * Two properties of the page are pinned here because nothing else can pin them:
 * a block that is never opened still travels with the save (the panels and the
 * disclosures are mounted lazily), and the capability choices — the ones that
 * cost an agent start — are asked for only once somebody looks at them.
 */

const {
  getFeature,
  getFeatureMeta,
  createFeature,
  updateFeature,
  deleteFeature,
  getFeatureCapabilities,
} = vi.hoisted(() => ({
  getFeature: vi.fn(),
  getFeatureMeta: vi.fn(),
  createFeature: vi.fn(),
  updateFeature: vi.fn(),
  deleteFeature: vi.fn(),
  getFeatureCapabilities: vi.fn(),
}));

vi.mock("../../api/modules/features", () => ({
  featuresApi: {
    listFeatures: vi.fn(),
    getFeature,
    getFeatureMeta,
    getFeatureCapabilities,
    createFeature,
    updateFeature,
    deleteFeature,
    runFeature: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import FeatureSettingsPage from "./Settings";

// Every case here drives a real antd form; the 5s default is a coin flip once
// the suite runs its files in parallel.
vi.setConfig({ testTimeout: 30_000 });

const META: FeatureMeta = {
  units: ["general", "sales"],
  icons: ["receipt", "file-text"],
  output_kinds: ["markdown", "json", "text"],
  bundled_ids: [],
};

const ADMIN: OctopUser = {
  id: 1,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
};

const MEMBER: OctopUser = {
  id: 2,
  username: "li",
  role: "user",
  display_name: null,
  locale: "zh",
  permissions: ["features"],
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
      items: {
        type: "array",
        items: { type: "array", items: { type: "string" } },
      },
      deadline: { type: "string", format: "date" },
    },
    required: ["customer"],
  },
  ui_schema: {
    order: ["customer", "items", "deadline"],
    widgets: { customer: "textarea" },
  },
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

/** The settings route, with the run page mounted as the guard's landing site. */
function renderSettings(user: OctopUser = ADMIN) {
  return render(
    <MemoryRouter initialEntries={["/features/quote-draft/settings"]}>
      <CurrentUserProvider user={user} setUser={vi.fn()}>
        <Routes>
          <Route path="/features" element={<div>feature-catalog</div>} />
          <Route path="/features/:id" element={<div>feature-run-page</div>} />
          {/* The app registers one route for the bare path and every tab. */}
          <Route
            path="/features/:id/settings/*"
            element={<FeatureSettingsPage />}
          />
        </Routes>
      </CurrentUserProvider>
    </MemoryRouter>,
  );
}

/**
 * The definition is on screen: the id field only exists once the page has
 * loaded the definition *and* the format choices, so this waits past the 1s
 * default — a loaded suite is easily slower than that.
 */
async function waitForEditor() {
  return screen.findByDisplayValue("quote-draft", undefined, {
    timeout: 15_000,
  });
}

describe("<FeatureSettingsPage />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    // A tab the previous case opened must not decide where this one starts.
    localStorage.clear();
    getFeature.mockResolvedValue(FEATURE);
    getFeatureMeta.mockResolvedValue(META);
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  it("seeds from the definition and writes the edited document back", async () => {
    const user = userEvent.setup();
    renderSettings();

    await waitForEditor();
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
    // The page re-reads what it wrote, so the header and the form show the
    // stored definition rather than what was typed.
    await waitFor(() => expect(getFeature).toHaveBeenCalledTimes(2));
  });

  it("keeps ui_schema.order in step when a row is moved", async () => {
    const user = userEvent.setup();
    renderSettings();

    await waitForEditor();
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
    renderSettings();

    await waitForEditor();
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

  it("deletes the definition an author asks to delete", async () => {
    const user = userEvent.setup();
    renderSettings();

    await waitForEditor();
    await user.click(
      screen.getByRole("button", { name: "features.settingsDelete" }),
    );
    await user.click(
      await screen.findByRole("button", { name: "common.delete" }),
    );

    await waitFor(() =>
      expect(deleteFeature).toHaveBeenCalledWith("quote-draft"),
    );
  });

  it("keeps a refused write on screen with the reason the server gave", async () => {
    const user = userEvent.setup();
    updateFeature.mockRejectedValue(
      new Error(
        'Request failed: 400 Bad Request - {"error":{"code":"FEATURE_INVALID",' +
          '"message":"invalid feature definition: input_schema.properties must be a non-empty object"}}',
      ),
    );
    renderSettings();

    await waitForEditor();
    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      await screen.findByText(
        /input_schema\.properties must be a non-empty object/,
      ),
    ).toBeInTheDocument();
  });

  it("asks for the capability choices only once the block is opened", async () => {
    const user = userEvent.setup();
    renderSettings();

    await waitForEditor();
    // Opening the settings page must not start an agent: the choices that need
    // one are behind the disclosure.
    expect(getFeatureCapabilities).not.toHaveBeenCalled();

    await user.click(screen.getByText("features.settingsSectionCapability"));

    await waitFor(() => expect(getFeatureCapabilities).toHaveBeenCalledOnce());
    // For the definition's own agent, not the caller's: a declared scope is
    // intersected with the run agent, so the choices have to be the ones a run
    // of *this* feature would really have (S1's run-agent switch).
    expect(getFeatureCapabilities).toHaveBeenCalledWith("quote-draft");
    // The fields the choices feed only exist once they arrived.
    expect(
      await screen.findByText("features.settingsCapabilitySkills"),
    ).toBeInTheDocument();
  });

  it("keeps a declared capability layer that was never opened", async () => {
    const user = userEvent.setup();
    getFeature.mockResolvedValue({
      ...FEATURE,
      agent: { model: "openai/gpt-4o", skills: [] },
    });
    renderSettings();

    await waitForEditor();
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
    renderSettings();

    await waitForEditor();
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
    getFeature.mockResolvedValue({
      ...FEATURE,
      agent: {
        model: "openai/gpt-4o",
        tools_disabled: ["browser_use"],
        skills: [],
      },
    });
    renderSettings();

    await waitForEditor();
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

  it("writes a step the author never opened the step tab to see", async () => {
    const user = userEvent.setup();
    getFeature.mockResolvedValue({
      ...FEATURE,
      steps: [
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
      ],
    });
    renderSettings();

    await waitForEditor();
    // The step panel has never been mounted — the definition tab is the one on
    // screen — and the save still has to carry the skeleton with it.
    expect(
      screen.queryByText("features.settingsSectionSteps"),
    ).not.toBeInTheDocument();

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    expect(lastUpdate()[1].steps).toEqual([
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
});

describe("<FeatureSettingsPage /> step skeleton", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    getFeature.mockResolvedValue(FEATURE);
    getFeatureMeta.mockResolvedValue(META);
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  /** The step tab, mounted and scrolled past its own disclosure. */
  async function openSteps(user: UserEvent) {
    await waitForEditor();
    await user.click(screen.getByText("features.settingsTabSteps"));
    await user.click(screen.getByText("features.settingsSectionSteps"));
    return screen.findByRole("button", { name: "features.settingsStepsAdd" });
  }

  /**
   * Reveal the block without adding a row: the Add button is the proof the rows
   * are mounted, so a case that starts from a declared step must not click it —
   * clicking would leave a second, empty row whose required fields refuse the
   * save.
   */
  async function revealSteps(user: UserEvent) {
    await waitForEditor();
    await user.click(screen.getByText("features.settingsTabSteps"));
    await user.click(screen.getByText("features.settingsSectionSteps"));
    await screen.findByRole("button", { name: "features.settingsStepsAdd" });
  }

  it("writes the step the author built, with the mode the author chose", async () => {
    const user = userEvent.setup();
    renderSettings();

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

    // The self-decomposing mode is the author's call: both modes are on offer
    // and picking one is what gets written.
    const modeInput =
      document.querySelector<HTMLInputElement>('input[id$="_mode"]');
    expect(modeInput).not.toBeNull();
    await user.click(modeInput as HTMLInputElement);
    const orchestrate = await screen.findByTitle(
      "features.settingsStepModeOrchestrate",
    );
    await user.click(orchestrate);

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [, body] = lastUpdate();
    expect(body.steps).toEqual([
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
    ]);
  });

  it("writes an orchestrate step running as its named subagent, without a refusal", async () => {
    const user = userEvent.setup();
    getFeature.mockResolvedValue({
      ...FEATURE,
      steps: [
        {
          id: "op_design",
          name: "工序设计",
          mode: "orchestrate",
          agent_role: "engineering/engineering-code-reviewer",
          inputs: [],
          output: { name: "ops", schema: "list" },
          prompt: "出工艺包",
          gate: "auto",
          on_failure: "abort",
        },
      ],
    });
    renderSettings();

    await revealSteps(user);
    // How a step runs is writable, so nothing here calls the mode or the role
    // unimplemented: the editor warns about neither.
    expect(screen.queryByText("features.settingsStepModeRefused")).toBeNull();
    expect(screen.queryByText(/settingsStepAgentRoleRefused/)).toBeNull();

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    const [, body] = lastUpdate();
    expect(body.steps?.[0].mode).toBe("orchestrate");
    expect(body.steps?.[0].agent_role).toBe(
      "engineering/engineering-code-reviewer",
    );
  });

  it("lets the author pick the subagent a step runs as", async () => {
    const user = userEvent.setup();
    getFeature.mockResolvedValue({
      ...FEATURE,
      steps: [
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
      ],
    });
    renderSettings();

    await revealSteps(user);
    // The roles on offer are the caller's own subagents, so the picker can only
    // name one the run could actually start.
    await user.click(screen.getByLabelText("features.settingsStepAgentRole"));
    await user.click(await screen.findByTitle("researcher"));

    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(updateFeature).toHaveBeenCalledOnce());
    // Picking a role adds it to the step and changes nothing else about it.
    expect(lastUpdate()[1].steps).toEqual([
      {
        id: "op_design",
        name: "工序设计",
        mode: "agent",
        agent_role: "researcher",
        inputs: [],
        output: { name: "ops", schema: "list" },
        prompt: "出工艺包",
        gate: "auto",
        on_failure: "abort",
      },
    ]);
  });

  it("refuses a validate gate whose artifact could never pass", async () => {
    const user = userEvent.setup();
    getFeature.mockResolvedValue({
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
    });
    renderSettings();

    await waitForEditor();
    // Saved from the definition tab: a step the author cannot see is exactly
    // the one a silent write would drop.
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
    renderSettings();

    await waitForEditor();
    await user.click(screen.getByText("features.settingsTabSteps"));
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

describe("<FeatureSettingsPage /> scope placeholders", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    getFeature.mockResolvedValue(FEATURE);
    getFeatureMeta.mockResolvedValue(META);
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
    getFeature.mockResolvedValue({
      ...FEATURE,
      agent: { skills: [] },
    });
    renderSettings();

    await waitForEditor();
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
    renderSettings();

    await waitForEditor();
    await user.click(screen.getByText("features.settingsSectionCapability"));
    await screen.findByText("features.settingsCapabilitySkills");

    expect(scopePlaceholder("features.settingsCapabilitySkills")).toBe(
      "features.settingsCapabilityScopePlaceholder",
    );
  });
});

describe("<FeatureSettingsPage /> entry gates", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    getFeature.mockResolvedValue(FEATURE);
    getFeatureMeta.mockResolvedValue(META);
    updateFeature.mockResolvedValue({ feature_id: "quote-draft" });
    createFeature.mockResolvedValue({ feature_id: "quote-draft" });
    deleteFeature.mockResolvedValue(undefined);
    getFeatureCapabilities.mockResolvedValue(CAPABILITIES);
  });

  it("sends a member who reaches the URL straight back to the run page", async () => {
    renderSettings(MEMBER);

    expect(await screen.findByText("feature-run-page")).toBeInTheDocument();
    // The definition-format choices are a writer's call only.
    expect(getFeatureMeta).not.toHaveBeenCalled();
    expect(screen.queryByDisplayValue("quote-draft")).toBeNull();
  });

  it("sends an administrator to the run page for a bundled definition", async () => {
    getFeatureMeta.mockResolvedValue({ ...META, bundled_ids: ["quote-draft"] });
    renderSettings();

    expect(await screen.findByText("feature-run-page")).toBeInTheDocument();
    await waitFor(() => expect(getFeatureMeta).toHaveBeenCalledOnce());
    expect(screen.queryByDisplayValue("quote-draft")).toBeNull();
    expect(
      screen.queryByRole("button", { name: "features.settingsDelete" }),
    ).toBeNull();
  });
});
