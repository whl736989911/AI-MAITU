/**
 * ChannelsPanel.test.tsx — channel create-flow behavior.
 *
 * Tests the visible enablement default, intentional opt-out behavior, and
 * preservation of Discord access policy IDs at the API boundary.
 */

import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import {
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent, {
  PointerEventsCheckLevel,
  type UserEvent,
} from "@testing-library/user-event";

/**
 * Interaction options for jsdom. Same measurement as the other dialog
 * suites: user-event's default ``pointerEventsCheck`` re-walks the target's
 * ancestors through ``getComputedStyle`` for every dispatched event, and
 * jsdom prices each call at ~2 ms against antd's ~1.3k runtime-injected CSS
 * rules — 20-30 ms once the whole suite is competing for the CPU. The direct
 * ``userEvent.click``/``type`` API also builds a fresh instance per call, so
 * nothing is ever cached. ``EachTarget`` keeps the pointer-events guard but
 * caches it per element, and ``delay: null`` drops the real ``setTimeout``
 * between events.
 */
const userOptions = {
  pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
  delay: null,
};

vi.mock("../../../api/request", () => ({
  request: vi.fn(),
}));

import { request } from "../../../api/request";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import type { OctopUser } from "../../../api/modules/auth";
import { message } from "../../../utils/antdMessage";
import ChannelsPanel from "./ChannelsPanel";

const api = vi.mocked(request, true);

/** A baseline holder: every channel type, so the catalogue is the full one. */
const baselineUser = {
  id: 2,
  username: "member",
  role: "user",
  permissions: ["channels", "channel_telegram"],
} as OctopUser;

function renderPanel(user: OctopUser) {
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <ChannelsPanel agentId="ag1" />
    </CurrentUserProvider>,
  );
}

beforeEach(() => {
  vi.clearAllMocks();
  // GET list -> empty; POST create -> server-echoed row with enabled=1
  api.mockImplementation(async (_url: string, init?: RequestInit) => {
    if (init?.method === "POST") {
      return { id: "c1", kind: "telegram", name: "telegram", enabled: true };
    }
    return [];
  });
});

// Static fallback messages own timers outside RTL's component roots.
afterEach(async () => {
  await act(async () => {
    message.destroy();
  });
});

describe("<ChannelsPanel /> create-flow default", () => {
  async function openTelegramCreateDrawer(user: UserEvent) {
    const { container } = renderPanel(baselineUser);
    const panel = within(container);
    const showMore = (
      await panel.findByText(/channels\.showMoreChannels/)
    ).closest("button")!;
    expect(showMore).toBeVisible();
    expect(showMore).toHaveAccessibleName(/channels\.showMoreChannels/);
    await user.click(showMore);
    // telegram has no quick-config path -> clicking its card opens the
    // manual create drawer directly.
    const card = (await screen.findAllByText("channels.label_telegram"))[0];
    await user.click(card);
    return findChannelDrawer();
  }

  async function findChannelDrawer() {
    const dialog = await screen.findByRole("dialog", { hidden: true });
    expect(dialog).toBeVisible();
    return within(dialog);
  }

  it("opens the create drawer with the enable switch ON", async () => {
    const user = userEvent.setup(userOptions);
    const drawer = await openTelegramCreateDrawer(user);

    // The enable control remains visible and enabled by default.
    const sw = await drawer.findByLabelText("channels.enableChannel");
    expect(sw).toBeVisible();
    expect(sw.getAttribute("aria-checked")).toBe("true");
  });

  it("still honors a deliberate opt-out: unchecking fires the alignment PATCH", async () => {
    const user = userEvent.setup(userOptions);
    const drawer = await openTelegramCreateDrawer(user);

    await user.click(await drawer.findByLabelText(/Bot Token/i));
    await user.paste("123456:ABC-token");
    // user explicitly turns the switch off before saving
    const enabled = drawer.getByLabelText("channels.enableChannel");
    await user.click(enabled);
    expect(enabled).toHaveAttribute("aria-checked", "false");
    const saveButton = drawer.getByText("common.save").closest("button")!;
    expect(saveButton).toBeVisible();
    expect(saveButton).toHaveAccessibleName("common.save");
    await user.click(saveButton);

    await waitFor(() => {
      const patch = api.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
      );
      expect(patch).toBeDefined();
      expect(String((patch![1] as RequestInit).body)).toBe(
        JSON.stringify({ enabled: false }),
      );
    });
  });
  it("creates Discord with access policy IDs preserved as strings", async () => {
    const user = userEvent.setup(userOptions);
    const { container } = renderPanel({
      ...baselineUser,
      role: "admin",
      permissions: [],
    });
    const panel = within(container);
    const showMore = (
      await panel.findByText(/channels\.showMoreChannels/)
    ).closest("button")!;
    expect(showMore).toBeVisible();
    expect(showMore).toHaveAccessibleName(/channels\.showMoreChannels/);
    await user.click(showMore);
    await user.click((await screen.findAllByText("channels.label_discord"))[0]);
    const drawer = await findChannelDrawer();
    fireEvent.change(await drawer.findByLabelText(/Bot Token/i), {
      target: { value: "discord-secret" },
    });
    expect(
      (
        await drawer.findByLabelText("channels.discord_allow_all_channels")
      ).getAttribute("aria-checked"),
    ).toBe("true");
    await user.click(
      drawer.getByLabelText("channels.discord_allow_all_channels"),
    );
    fireEvent.change(drawer.getByLabelText("channels.discord_channel_ids"), {
      target: { value: "123456789012345678" },
    });
    fireEvent.change(drawer.getByLabelText("channels.discord_user_ids"), {
      target: { value: "987654321098765432" },
    });
    const saveButton = drawer.getByText("common.save").closest("button")!;
    expect(saveButton).toBeVisible();
    expect(saveButton).toHaveAccessibleName("common.save");
    await user.click(saveButton);
    await waitFor(() => {
      const post = api.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
      expect(JSON.parse(String((post![1] as RequestInit).body))).toEqual({
        kind: "discord",
        name: "discord",
        config: expect.objectContaining({
          bot_token: "discord-secret",
          allow_all_channels: false,
          allowed_channel_ids: ["123456789012345678"],
          allowed_user_ids: ["987654321098765432"],
        }),
      });
    });
  });
});

describe("<ChannelsPanel /> channel types the account may use", () => {
  it("offers only the types the account holds a key for", async () => {
    renderPanel({
      ...baselineUser,
      permissions: ["channels", "channel_feishu"],
    });

    expect(
      (await screen.findAllByText("channels.label_feishu")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("channels.label_wecom")).toBeNull();
    // The collapsed "更多通道" bucket is drawn from the authorized set too.
    expect(screen.queryByText(/channels\.showMoreChannels/)).toBeNull();
  });

  it("says so when no channel type is authorized at all", async () => {
    renderPanel({ ...baselineUser, permissions: ["channels"] });

    expect(
      (await screen.findAllByText("channels.noAuthorizedTypes")).length,
    ).toBeGreaterThan(0);
    expect(screen.queryByText("channels.label_feishu")).toBeNull();
  });

  it("gives a system administrator every type through the role bypass", async () => {
    renderPanel({ ...baselineUser, role: "admin", permissions: [] });

    expect(
      (await screen.findAllByText("channels.label_feishu")).length,
    ).toBeGreaterThan(0);
    const showMore = (
      await screen.findByText(/channels\.showMoreChannels/)
    ).closest("button")!;
    expect(showMore).toBeVisible();
    expect(showMore).toHaveAccessibleName(/channels\.showMoreChannels/);
  });
});
