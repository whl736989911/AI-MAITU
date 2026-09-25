/**
 * ChannelsPanel.test.tsx — manual create-flow default enablement.
 *
 * Regression test for the "channel born disabled" bug: the create drawer's
 * enable switch used to default OFF while the server-side create always
 * writes enabled=1. Saving then fired a follow-up PATCH {enabled: false},
 * producing a born-disabled row (created_at == updated_at) with no warning —
 * the bot looked "muted" from then on.
 *
 * Contract under test:
 *   - create drawer opens with the enable switch ON
 *   - saving a new channel sends exactly one POST (no PATCH churn)
 */

import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
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

describe("<ChannelsPanel /> create-flow default", () => {
  async function openTelegramCreateDrawer(user: UserEvent) {
    renderPanel(baselineUser);
    // Telegram is collapsed behind "更多通道" until expanded.
    await user.click(
      await screen.findByRole("button", {
        name: /channels\.showMoreChannels/,
      }),
    );
    // telegram has no quick-config path -> clicking its card opens the
    // manual create drawer directly.
    const card = (await screen.findAllByText("channels.label_telegram"))[0];
    await user.click(card);
  }

  it("opens the create drawer with the enable switch ON", async () => {
    const user = userEvent.setup(userOptions);
    await openTelegramCreateDrawer(user);

    // the drawer's "Enable channel" switch (Form.Item wires label<->control)
    const sw = await screen.findByLabelText("channels.enableChannel");
    expect(sw.getAttribute("aria-checked")).toBe("true");
  });

  it("saves a new channel with a single POST and no follow-up PATCH", async () => {
    const user = userEvent.setup(userOptions);
    await openTelegramCreateDrawer(user);

    await user.type(
      await screen.findByLabelText(/Bot Token/i),
      "123456:ABC-token",
    );
    await user.click(screen.getByRole("button", { name: "common.save" }));

    await waitFor(() => {
      const post = api.mock.calls.find(
        ([, init]) => (init as RequestInit | undefined)?.method === "POST",
      );
      expect(post).toBeDefined();
    });

    // server echoes the created row, enabled=1 == requested true ->
    // the "align enablement" PATCH must NOT fire
    const patch = api.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(patch).toBeUndefined();

    const [, init] = api.mock.calls.find(
      ([, i]) => (i as RequestInit | undefined)?.method === "POST",
    ) as [string, RequestInit];
    expect(JSON.parse(String(init.body))).toEqual({
      kind: "telegram",
      name: "telegram",
      config: expect.objectContaining({ bot_token: "123456:ABC-token" }),
    });
  });

  it("still honors a deliberate opt-out: unchecking fires the alignment PATCH", async () => {
    const user = userEvent.setup(userOptions);
    await openTelegramCreateDrawer(user);

    await user.type(
      await screen.findByLabelText(/Bot Token/i),
      "123456:ABC-token",
    );
    // user explicitly turns the switch off before saving
    await user.click(screen.getByLabelText("channels.enableChannel"));
    await user.click(screen.getByRole("button", { name: "common.save" }));

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
    renderPanel({ ...baselineUser, role: "admin", permissions: [] });
    await user.click(
      await screen.findByRole("button", {
        name: /channels\.showMoreChannels/,
      }),
    );
    await user.click((await screen.findAllByText("channels.label_discord"))[0]);
    await user.type(
      await screen.findByLabelText(/Bot Token/i),
      "discord-secret",
    );
    expect(
      (
        await screen.findByLabelText("channels.discord_allow_all_channels")
      ).getAttribute("aria-checked"),
    ).toBe("true");
    await user.click(
      screen.getByLabelText("channels.discord_allow_all_channels"),
    );
    await user.type(
      screen.getByLabelText("channels.discord_channel_ids"),
      "123456789012345678",
    );
    await user.type(
      screen.getByLabelText("channels.discord_user_ids"),
      "987654321098765432",
    );
    await user.click(screen.getByRole("button", { name: "common.save" }));

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
    expect(
      screen.queryByRole("button", { name: /channels\.showMoreChannels/ }),
    ).toBeNull();
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
    expect(
      await screen.findByRole("button", { name: /channels\.showMoreChannels/ }),
    ).toBeDefined();
  });
});
