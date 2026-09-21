import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";
import type { Feature, FeatureMeta } from "../../api/modules/features";

/**
 * The detail page is where a feature is used, so the settings entry has to stay
 * out of the way of everyone who is only there to run it: a member never sees it
 * and neither does an administrator when the definition belongs to the app.
 */

const { getFeature, getFeatureMeta, listRules, listCases } = vi.hoisted(() => ({
  getFeature: vi.fn(),
  getFeatureMeta: vi.fn(),
  listRules: vi.fn(),
  listCases: vi.fn(),
}));

vi.mock("../../api/modules/features", () => ({
  featuresApi: {
    getFeature,
    getFeatureMeta,
    listRules,
    listCases,
    listFeatures: vi.fn(),
    createFeature: vi.fn(),
    updateFeature: vi.fn(),
    deleteFeature: vi.fn(),
    runFeature: vi.fn(),
    finalizeTask: vi.fn(),
    promoteTask: vi.fn(),
    extractRules: vi.fn(),
    approveRule: vi.fn(),
    rejectRule: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import FeatureDetailPage from "./Detail";

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
  color: null,
  unit: "sales",
  output_kind: "markdown",
  permissions: {},
  input_schema: {
    type: "object",
    properties: { customer: { type: "string" } },
    required: ["customer"],
  },
  ui_schema: { order: ["customer"] },
  user_template: "{{inputs}}",
  system_prompt: null,
  agent: null,
};

function metaWith(bundled: string[]): FeatureMeta {
  return {
    units: ["sales"],
    icons: ["receipt"],
    output_kinds: ["markdown", "json", "text"],
    bundled_ids: bundled,
  };
}

function renderDetail(user: OctopUser) {
  return render(
    <MemoryRouter initialEntries={["/features/quote-draft"]}>
      <CurrentUserProvider user={user} setUser={vi.fn()}>
        <Routes>
          <Route path="/features/:id" element={<FeatureDetailPage />} />
        </Routes>
      </CurrentUserProvider>
    </MemoryRouter>,
  );
}

describe("<FeatureDetailPage /> settings entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    getFeature.mockResolvedValue(FEATURE);
    listRules.mockResolvedValue({ feature_id: FEATURE.id, rules: [] });
    listCases.mockResolvedValue({ feature_id: FEATURE.id, cases: [] });
  });

  it("lets an administrator open the settings of a workspace feature", async () => {
    getFeatureMeta.mockResolvedValue(metaWith([]));
    renderDetail(ADMIN);

    expect(
      await screen.findByRole("button", { name: "features.settingsEdit" }),
    ).toBeInTheDocument();
  });

  it("keeps the settings of a bundled feature out of reach", async () => {
    getFeatureMeta.mockResolvedValue(metaWith([FEATURE.id]));
    renderDetail(ADMIN);

    expect(
      await screen.findByRole("button", { name: "features.run" }),
    ).toBeInTheDocument();
    await waitFor(() => expect(getFeatureMeta).toHaveBeenCalledOnce());
    expect(
      screen.queryByRole("button", { name: "features.settingsEdit" }),
    ).toBeNull();
  });

  it("shows a member the run form only", async () => {
    getFeatureMeta.mockResolvedValue(metaWith([]));
    renderDetail(MEMBER);

    expect(
      await screen.findByRole("button", { name: "features.run" }),
    ).toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "features.settingsEdit" }),
    ).toBeNull();
    expect(getFeatureMeta).not.toHaveBeenCalled();
  });
});
