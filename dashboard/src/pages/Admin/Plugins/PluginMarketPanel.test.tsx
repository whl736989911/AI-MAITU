/**
 * PluginMarketPanel.test.tsx — the shipped-plugin market must never leave a
 * section reading as if everything is fine when it is not.
 *
 * Regression test for the drawer's detail path: when `/plugins/market/<id>`
 * failed, the drawer kept the empty `tools` it seeds optimistically and rendered
 * the ordinary "install the plugin to see the tools it registers" hint — so an
 * already-installed plugin appeared to be missing an install, and the failure
 * only ever existed in a toast. The failure is now stated in the section, with
 * the card data (name / kind / requirements) still on screen, and retrying
 * recovers.
 *
 * Also pins the admin-only rule for the market's write action: without the
 * `plugins` permission the install button is absent — not rendered disabled —
 * while the read-only card stays browsable.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { OctopUser } from "../../../api/modules/auth";
import type {
  MarketPlugin,
  MarketPluginDetail,
} from "../../../api/modules/plugins";

const { marketList, marketGet, marketInstall } = vi.hoisted(() => ({
  marketList: vi.fn(),
  marketGet: vi.fn(),
  marketInstall: vi.fn(),
}));

vi.mock("../../../api/modules/plugins", () => ({
  pluginsApi: {
    marketList,
    marketGet,
    marketInstall,
    list: vi.fn(),
    install: vi.fn(),
    upload: vi.fn(),
    uninstall: vi.fn(),
    setEnabled: vi.fn(),
    reload: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn() },
}));

import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import { PluginMarketPanel } from "./PluginMarketPanel";

/** The ui locale in these tests is zh, so the card renders the zh label. */
const ZH_NAME = "抽签运势";

const ADMIN: OctopUser = {
  id: 1,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
};

/** Admin role revoked: the card may be read, the write action may not be shown. */
const READER: OctopUser = {
  id: 2,
  username: "alice",
  role: "user",
  display_name: null,
  locale: "zh",
  permissions: [],
};

/** One shipped plugin, installed but globally off — the market's main case. */
function plugin(over: Partial<MarketPlugin> = {}): MarketPlugin {
  return {
    id: "fortune",
    version: "0.2.0",
    name: { zh: ZH_NAME, en: "Fortune Draw" },
    description: { zh: "掷骰子", en: "Roll dice" },
    icon: "🔮",
    kind: "tool",
    requires: ["pillow"],
    installed: true,
    enabled: false,
    ...over,
  };
}

function detail(over: Partial<MarketPluginDetail> = {}): MarketPluginDetail {
  return { ...plugin(), tools: [], ...over };
}

function renderPanel(user: OctopUser, onInstalled?: () => void) {
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <PluginMarketPanel onInstalled={onInstalled} />
    </CurrentUserProvider>,
  );
}

/** The drawer's error box for a failed detail fetch. */
async function detailAlert(): Promise<HTMLElement> {
  const alert = await screen.findByRole("alert");
  expect(alert).toHaveTextContent("market detail unavailable");
  return alert;
}

describe("<PluginMarketPanel />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("states a failed detail fetch instead of a plugin that has no tools", async () => {
    marketList.mockResolvedValue({ items: [plugin()] });
    marketGet.mockRejectedValue(new Error("market detail unavailable"));

    renderPanel(ADMIN);
    await userEvent.click(await screen.findByText("plugins.viewDetails"));

    const alert = await detailAlert();
    // The misleading hint is what the failure used to look like.
    expect(
      screen.queryByText("plugins.marketToolsPending"),
    ).not.toBeInTheDocument();
    // Card data came from the list row, so it is still on screen.
    expect(screen.getByText("plugins.marketRequires")).toBeInTheDocument();
    expect(screen.getByText("pillow")).toBeInTheDocument();
    // And the failure can be retried from where it is shown.
    marketGet.mockResolvedValue(
      detail({ tools: [{ name: "draw_fortune", description: "Draw" }] }),
    );
    await userEvent.click(
      within(alert).getByRole("button", { name: "common.refresh" }),
    );

    expect(await screen.findByText("draw_fortune")).toBeInTheDocument();
    expect(
      screen.queryByText("market detail unavailable"),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByText("plugins.marketToolsPending"),
    ).not.toBeInTheDocument();
  });

  it("does not call a landed install failed when the drawer refresh fails", async () => {
    marketList.mockResolvedValue({ items: [plugin({ installed: false })] });
    marketGet.mockRejectedValue(new Error("market detail unavailable"));
    marketInstall.mockResolvedValue({
      ...plugin(),
      installed: true,
      enabled: true,
    });

    renderPanel(ADMIN);
    await userEvent.click(await screen.findByText("plugins.viewDetails"));
    await detailAlert();

    // Install from the open drawer, then fail only the follow-up re-read.
    await userEvent.click(
      within(screen.getByRole("dialog")).getByRole("button", {
        name: "plugins.marketInstall",
      }),
    );

    await detailAlert();
    expect(
      screen.queryByText("plugins.marketToolsPending"),
    ).not.toBeInTheDocument();
    expect(marketInstall).toHaveBeenCalledWith("fortune");
  });

  it("offers install to a plugin admin and only browsing without the permission", async () => {
    marketList.mockResolvedValue({ items: [plugin({ installed: false })] });

    const { unmount } = renderPanel(ADMIN);
    expect(
      await screen.findByRole("button", { name: "plugins.marketInstall" }),
    ).toBeEnabled();
    unmount();

    renderPanel(READER);
    await screen.findByText(ZH_NAME);
    expect(
      screen.queryByRole("button", { name: "plugins.marketInstall" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("plugins.viewDetails")).toBeInTheDocument();
  });
});
