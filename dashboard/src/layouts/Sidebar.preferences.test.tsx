import { render, screen, waitFor, within } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../api/modules/auth";
import {
  preferencesApi,
  type UserPreferences,
} from "../api/modules/preferences";
import { CurrentUserProvider } from "../hooks/useCurrentUser";
import Sidebar from "./Sidebar";

const state = vi.hoisted(() => ({
  layoutMode: "classic" as "classic" | "minimal",
  minimalPane: "settings" as "records" | "settings",
}));

vi.mock("../hooks/useServerCapabilities", () => ({
  useServerCapabilities: () => ({ mobileEnabled: false }),
}));
vi.mock("../pages/Chat/hooks/useChatSidebarState", () => ({
  useChatSidebarOpen: () => [false, vi.fn()],
}));
vi.mock("../api/modules/preferences", () => ({
  preferencesApi: { get: vi.fn(), patch: vi.fn() },
}));
vi.mock("../context/LayoutModeContext", () => ({
  useLayoutMode: () => ({
    layoutMode: state.layoutMode,
    minimalPane: state.minimalPane,
    setMinimalPane: vi.fn(),
  }),
}));
vi.mock("../components/AvatarDropdown", () => ({ default: () => null }));
vi.mock("../components/CurrentVersionBadge", () => ({ default: () => null }));
vi.mock("./MinimalRecordsHost", () => ({ default: () => null }));

const user: OctopUser = {
  id: 7,
  username: "member",
  role: "user",
  display_name: null,
  locale: "en",
  permissions: [
    "features",
    "experts",
    "teams",
    "acp",
    "connectors",
    "channels",
    "knowledge_bases",
  ],
};

const storedPreferences = {
  order: [
    "token-usage",
    "chat",
    "features",
    "connectors",
    "channels",
    "knowledge-bases",
    "personalization",
    "acp",
    "admin-users",
  ],
  hidden: ["features", "personalization", "acp"],
};

function renderSidebar(
  layoutMode: "classic" | "minimal",
  minimalPane: "records" | "settings" = "settings",
) {
  state.layoutMode = layoutMode;
  state.minimalPane = minimalPane;
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <MemoryRouter initialEntries={["/chat"]}>
        <Sidebar
          selectedKey="chat"
          collapsed={false}
          onToggle={() => undefined}
        />
      </MemoryRouter>
    </CurrentUserProvider>,
  );
}

function navButtons(container: HTMLElement): string[] {
  return within(container)
    .getAllByRole("button")
    .map((button) => button.textContent ?? "")
    .filter((text) => text.startsWith("nav.") || text.startsWith("chat."));
}

beforeEach(() => {
  vi.clearAllMocks();
  vi.mocked(preferencesApi.get).mockResolvedValue({
    locale: "en",
    remote_browser_bookmarks: [],
    preferred_model: null,
    model_reasoning: {},
    sidebar: storedPreferences,
  } satisfies UserPreferences);
});

describe("Sidebar persisted navigation preferences", () => {
  it.each([
    ["classic expanded navigation", "classic", "settings"],
    ["minimal expanded primary pane", "minimal", "records"],
    ["minimal expanded settings pane", "minimal", "settings"],
  ] as const)(
    "applies hidden and order preferences in %s",
    async (_name, layout, pane) => {
      const { container } = renderSidebar(layout, pane);

      const chat = await screen.findByRole("button", { name: /nav\.chat/ });
      await waitFor(() => {
        expect(
          screen.queryByRole("button", { name: /nav\.features/ }),
        ).toBeNull();
        expect(
          screen.queryByRole("button", { name: /nav\.personalization/ }),
        ).toBeNull();
        expect(screen.queryByRole("button", { name: /nav\.acp/ })).toBeNull();
      });
      expect(chat).toBeInTheDocument();
      // Admin navigation is ACL-denied for this user and must not be reconstructed
      // from a preference entry that names it.
      expect(
        screen.queryByRole("button", { name: /nav\.adminUsers/ }),
      ).toBeNull();

      const visibleNavigation = navButtons(container);
      if (layout === "classic") {
        expect(visibleNavigation.indexOf("nav.tokenUsage")).toBeLessThan(
          visibleNavigation.indexOf("nav.chat"),
        );
      } else if (pane === "records") {
        expect(visibleNavigation.indexOf("nav.tokenUsage")).toBeLessThan(
          visibleNavigation.indexOf("nav.chat"),
        );
      } else {
        expect(visibleNavigation.indexOf("nav.connectors")).toBeLessThan(
          visibleNavigation.indexOf("nav.channels"),
        );
      }
    },
  );
});
