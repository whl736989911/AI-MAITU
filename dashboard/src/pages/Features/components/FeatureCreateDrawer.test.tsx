import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type {
  FeatureDefinitionBody,
  FeatureMeta,
} from "../../../api/modules/features";

/**
 * The new-feature entry point is a light drawer: the definition essentials only.
 * The blocks it leaves out are the two that borrow the caller's *agent* (the
 * capability choices and the step skeleton), and that is not cosmetic — asking
 * for them starts an agent, and a definition nobody has saved yet must not cost
 * one. What the author does not set here is set on the settings page the create
 * hands off to.
 */

const { createFeature, updateFeature, getFeatureCapabilities } = vi.hoisted(
  () => ({
    createFeature: vi.fn(),
    updateFeature: vi.fn(),
    getFeatureCapabilities: vi.fn(),
  }),
);

vi.mock("../../../api/modules/features", () => ({
  featuresApi: {
    listFeatures: vi.fn(),
    getFeature: vi.fn(),
    getFeatureMeta: vi.fn(),
    getFeatureCapabilities,
    createFeature,
    updateFeature,
    deleteFeature: vi.fn(),
    runFeature: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import FeatureCreateDrawer from "./FeatureCreateDrawer";

vi.setConfig({ testTimeout: 30_000 });

const META: FeatureMeta = {
  units: ["general", "sales"],
  icons: ["receipt", "file-text"],
  output_kinds: ["markdown", "json", "text"],
  bundled_ids: [],
};

function renderDrawer() {
  return render(
    <FeatureCreateDrawer
      open
      meta={META}
      onClose={vi.fn()}
      onCreated={vi.fn()}
    />,
  );
}

describe("<FeatureCreateDrawer />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    createFeature.mockResolvedValue({ feature_id: "ops-brief" });
    updateFeature.mockResolvedValue({ feature_id: "ops-brief" });
    getFeatureCapabilities.mockResolvedValue({});
  });

  it("creates the definition from the essentials and never asks for capability choices", async () => {
    const user = userEvent.setup();
    const onCreated = vi.fn();
    render(
      <FeatureCreateDrawer
        open
        meta={META}
        onClose={vi.fn()}
        onCreated={onCreated}
      />,
    );

    // The two blocks that cost an agent start are not on offer here at all.
    expect(screen.queryByText("features.settingsSectionCapability")).toBeNull();
    expect(screen.queryByText("features.settingsSectionSteps")).toBeNull();

    await user.type(screen.getByPlaceholderText("quote-draft"), "ops-brief");
    await user.type(
      screen.getByLabelText("features.settingsLabelZh"),
      "工序简报",
    );
    await user.type(
      screen.getByLabelText("features.settingsLabelEn"),
      "Ops brief",
    );
    await user.type(
      screen.getByLabelText("features.settingsDescriptionZh"),
      "生成工序简报",
    );
    await user.type(
      screen.getByLabelText("features.settingsDescriptionEn"),
      "Draft an ops brief",
    );
    await user.type(
      screen.getByPlaceholderText("features.settingsFieldNamePlaceholder"),
      "工艺单",
    );
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => expect(createFeature).toHaveBeenCalledOnce());
    const body = createFeature.mock.calls[0][0] as FeatureDefinitionBody;
    expect(body.id).toBe("ops-brief");
    expect(body.label).toEqual({ zh: "工序简报", en: "Ops brief" });
    expect(body.input_schema.properties).toEqual({
      工艺单: { type: "string" },
    });
    expect(body.ui_schema.order).toEqual(["工艺单"]);
    expect(body.prompt.user_template).toBe("{{inputs}}");
    // Nothing the drawer was never shown may be invented on the way out.
    expect(body.agent).toBeNull();
    expect(body.steps).toBeUndefined();
    expect(getFeatureCapabilities).not.toHaveBeenCalled();
    expect(onCreated).toHaveBeenCalledWith("ops-brief");
  });

  it("keeps the id rules a new definition has to satisfy", async () => {
    const user = userEvent.setup();
    renderDrawer();

    await user.type(screen.getByPlaceholderText("quote-draft"), "Quote Draft");
    await user.click(screen.getByRole("button", { name: "common.save" }));

    expect(
      await screen.findByText("features.settingsIdInvalid"),
    ).toBeInTheDocument();
    expect(createFeature).not.toHaveBeenCalled();
  });
});
