import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useParams } from "react-router-dom";
import type { OctopUser } from "../../api/modules/auth";
import type { FeatureSummary } from "../../api/modules/features";
import { BRAND } from "../../brand.generated";

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

const QUOTE: FeatureSummary = {
  id: "quote-draft",
  version: 1,
  label: { zh: "报价单草稿", en: "Quote draft" },
  description: { zh: "生成报价单", en: "Draft a quote" },
  icon_name: "receipt",
  color: "#f97316",
  unit: "sales",
  output_kind: "markdown",
  permissions: {},
};

const INVOICE: FeatureSummary = {
  id: "invoice-chase",
  version: 1,
  label: { zh: "催款函", en: "Payment chase" },
  description: { zh: "生成催款函", en: "Chase an invoice" },
  icon_name: "mail",
  color: null,
  unit: "sales",
  output_kind: "text",
  permissions: {},
};

const SHIPMENT: FeatureSummary = {
  id: "shipment-track",
  version: 2,
  label: { zh: "物流跟踪", en: "Track shipment" },
  description: { zh: "汇总物流状态", en: "Summarize tracking" },
  icon_name: "truck",
  color: "#0ea5e9",
  unit: "ops",
  output_kind: "markdown",
  permissions: {},
};

function OpenedFeature() {
  const { id } = useParams();
  return <div>opened:{id}</div>;
}

/** The catalog with its detail route, so a card click is observable. */
function renderCatalog() {
  return render(
    <MemoryRouter initialEntries={["/features"]}>
      <CurrentUserProvider user={MEMBER} setUser={vi.fn()}>
        <Routes>
          <Route path="/features" element={<FeaturesPage />} />
          <Route path="/features/:id" element={<OpenedFeature />} />
        </Routes>
      </CurrentUserProvider>
    </MemoryRouter>,
  );
}

describe("<FeaturesPage /> catalog", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listFeatures.mockResolvedValue({
      features: [QUOTE, INVOICE, SHIPMENT],
      units: [
        { key: "sales", count: 2 },
        { key: "ops", count: 1 },
      ],
    });
  });

  it("keeps the unit grouping and its counts", async () => {
    renderCatalog();

    expect(
      await screen.findByRole("heading", { name: "sales" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("heading", { name: "ops" })).toBeInTheDocument();
    expect(screen.getByText("2")).toBeInTheDocument();
    expect(screen.getByText("1")).toBeInTheDocument();
  });

  it("opens the feature a card names", async () => {
    const user = userEvent.setup();
    renderCatalog();

    const card = await screen.findByRole("button", { name: /报价单草稿/ });
    await user.click(card);

    expect(await screen.findByText("opened:quote-draft")).toBeInTheDocument();
  });

  it("tints each card with the feature's own colour", async () => {
    renderCatalog();

    const tinted = await screen.findByRole("button", { name: /报价单草稿/ });
    // The brand accent stands in where a feature names no colour of its own.
    const plain = screen.getByRole("button", { name: /催款函/ });

    expect(tinted.getAttribute("style")).toContain("--feature-tint: #f97316");
    expect(plain.getAttribute("style")).toContain(
      `--feature-tint: ${BRAND.color.accent}`,
    );
  });
});
