import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";

/**
 * Writing a definition belongs to an administrator, and to features the
 * workspace owns rather than to the ones the app ships. Both rules hide the
 * entry point instead of offering a control that can only be refused, so the
 * pages are what these cases pin.
 */

const { listFeatures, getFeatureMeta } = vi.hoisted(() => ({
  listFeatures: vi.fn(),
  getFeatureMeta: vi.fn(),
}));

vi.mock("../../api/modules/features", () => ({
  featuresApi: {
    listFeatures,
    getFeatureMeta,
    getFeature: vi.fn(),
    createFeature: vi.fn(),
    updateFeature: vi.fn(),
    deleteFeature: vi.fn(),
    runFeature: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn() },
}));

import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import FeaturesPage from "./index";

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

function renderPage(user: OctopUser) {
  return render(
    <MemoryRouter>
      <CurrentUserProvider user={user} setUser={vi.fn()}>
        <FeaturesPage />
      </CurrentUserProvider>
    </MemoryRouter>,
  );
}

describe("<FeaturesPage /> settings entry", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listFeatures.mockResolvedValue({ features: [], units: [] });
    getFeatureMeta.mockResolvedValue({
      units: [],
      icons: [],
      output_kinds: ["markdown", "json", "text"],
      bundled_ids: [],
    });
  });

  it("offers authoring to an administrator", async () => {
    renderPage(ADMIN);

    expect(
      await screen.findByRole("button", { name: "features.settingsNew" }),
    ).toBeInTheDocument();
    // The definition-format choices are a writer's call only.
    await waitFor(() => expect(getFeatureMeta).toHaveBeenCalledOnce());
  });

  it("offers a member nothing but running features", async () => {
    renderPage(MEMBER);

    await waitFor(() => expect(listFeatures).toHaveBeenCalledOnce());
    expect(
      screen.queryByRole("button", { name: "features.settingsNew" }),
    ).toBeNull();
    expect(getFeatureMeta).not.toHaveBeenCalled();
  });

  it("keeps authoring shut until the format choices are in", async () => {
    // No choices means an editor whose unit, icon and output pickers are empty:
    // the entry point waits for the answer rather than opening half-built.
    getFeatureMeta.mockRejectedValue(
      new Error("Request failed: 500 Internal Server Error"),
    );
    renderPage(ADMIN);

    await waitFor(() => expect(getFeatureMeta).toHaveBeenCalledOnce());
    expect(
      screen.queryByRole("button", { name: "features.settingsNew" }),
    ).toBeNull();
  });
});
