/**
 * Admin → Users draws the agent counts as **two** columns — 「专家」 and
 * 「功能」 — one per ``kind``, and only for the kinds this deployment holds at
 * all. A kind nobody holds anywhere is not a column of zeroes under a header
 * promising agents that do not exist; it is no column.
 *
 * The rule is deliberately read off the instance rather than off the rows on
 * screen: the table is paged, and a column that came and went with the page
 * would be a different table on every page.
 *
 * The assertions are about the copy a Chinese-locale admin reads, so ``t`` is
 * answered from the shipped ``zh.json`` — the bundle imported below, before the
 * panel that looks its headers up through it. The other side of the same
 * decision is pinned in ``tests/unit/i18n/test_admin_users_expert_naming.py``.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { OctopUser } from "../../../api/modules/auth";
import type { OctopAgent } from "../../../context/AgentContext";
import zh from "../../../locales/zh.json";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../../../api/request", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../../../api/request")>();
  return { ...actual, request: requestMock };
});

vi.mock("@/utils/antdMessage", () => ({
  message: {
    success: vi.fn(),
    error: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

/** ``t`` as i18next answers it: bundle first, then the key's own fallback. */
vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof import("react-i18next")>();

  const lookup = (key: string): string | undefined => {
    let node: unknown = zh;
    for (const part of key.split(".")) {
      if (!node || typeof node !== "object") return undefined;
      node = (node as Record<string, unknown>)[part];
    }
    return typeof node === "string" ? node : undefined;
  };
  const interpolate = (
    template: string,
    vars: Record<string, unknown> | undefined,
  ) =>
    vars
      ? template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
          name in vars ? String(vars[name]) : match,
        )
      : template;

  type TOptions = Record<string, unknown> & { defaultValue?: string };
  // One object, as the real hook hands out: a fresh ``t`` per render would
  // make every ``useCallback(…, [t])`` in the panel change identity on every
  // render, which loops the page's load effect.
  const api = {
    t: (key: string, options?: string | TOptions, extra?: TOptions): string => {
      const vars = options && typeof options === "object" ? options : extra;
      const found = lookup(key);
      if (found !== undefined) return interpolate(found, vars);
      if (typeof options === "string") return interpolate(options, vars);
      if (options?.defaultValue) {
        return interpolate(options.defaultValue, vars);
      }
      return key;
    },
    i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
  };
  return { ...actual, useTranslation: () => api };
});

import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import UsersListPanel from "./UsersListPanel";

const ADMIN: OctopUser = {
  id: 99,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
  permissions: ["users"],
};

/** Two accounts, so a page holds an owner with agents beside one without. */
const USERS = [
  {
    id: 1,
    username: "alice",
    role: "user" as const,
    display_name: "Alice",
    email: "alice@example.com",
    disabled: false,
  },
  {
    id: 2,
    username: "bob",
    role: "user" as const,
    display_name: "Bob",
    email: null,
    disabled: false,
  },
];

function agent(agentId: string, kind: string, userId: number): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    user_id: userId,
    name: agentId,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: "sparkles",
    icon_url: null,
    color: null,
    config: {},
    is_shared: false,
    is_owner: true,
  };
}

/** Alice's pair; which of them exist is what each test varies. */
const EXPERT = agent("assistant", "agent", 1);
const FEATURE = agent("weekly-report", "feature", 1);

let agents: OctopAgent[] = [];

beforeEach(() => {
  requestMock.mockReset();
  requestMock.mockImplementation(async (url: string): Promise<unknown> => {
    if (url === "/users") return USERS;
    if (url === "/agents?scope=all") return agents;
    if (url === "/users/permissions") return [];
    if (url === "/org-units") return { units: [] };
    if (url === "/filesystem/defaults") {
      return {
        home: "/home/octop",
        default_root_dir: "/home/octop",
        allow_outside_home: false,
        tree_root: "/home/octop",
        in_container: false,
      };
    }
    if (url === "/settings/timezone") return { timezone: "UTC" };
    if (url === "/auth/me") return ADMIN;
    throw new Error(`unexpected request: ${url}`);
  });
});

function renderPanel() {
  return render(
    <CurrentUserProvider user={ADMIN} setUser={() => undefined}>
      <UsersListPanel />
    </CurrentUserProvider>,
  );
}

describe("Admin → Users agent columns", () => {
  it("draws only the experts column when the deployment has no features", async () => {
    agents = [EXPERT, agent("reviewer", "agent", 1)];
    renderPanel();

    expect(
      await screen.findByRole("columnheader", { name: "专家" }),
    ).toBeInTheDocument();
    // Bob owns nothing, but the column is about the deployment — his row reads
    // 0 rather than the column dropping out from under alice.
    expect(await screen.findByRole("button", { name: "专家 2" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "专家 0" })).toBeVisible();
    expect(screen.queryByRole("columnheader", { name: "功能" })).toBeNull();
    expect(screen.queryByRole("button", { name: "功能 0" })).toBeNull();
  });

  it("draws only the features column when the deployment has no experts", async () => {
    agents = [FEATURE];
    renderPanel();

    expect(
      await screen.findByRole("columnheader", { name: "功能" }),
    ).toBeInTheDocument();
    expect(await screen.findByRole("button", { name: "功能 1" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "功能 0" })).toBeVisible();
    expect(screen.queryByRole("columnheader", { name: "专家" })).toBeNull();
    expect(screen.queryByRole("button", { name: "专家 0" })).toBeNull();
  });

  it("draws both columns, each counting its own kind, when both exist", async () => {
    agents = [EXPERT, FEATURE, agent("second-feature", "feature", 1)];
    renderPanel();

    expect(
      await screen.findByRole("columnheader", { name: "专家" }),
    ).toBeInTheDocument();
    expect(
      await screen.findByRole("columnheader", { name: "功能" }),
    ).toBeInTheDocument();
    // Alice's two kinds are told apart inside one row.
    expect(await screen.findByRole("button", { name: "专家 1" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "功能 2" })).toBeVisible();
    // And a user with neither still reads 0 / 0, not a missing row.
    expect(await screen.findByRole("button", { name: "专家 0" })).toBeVisible();
    expect(await screen.findByRole("button", { name: "功能 0" })).toBeVisible();
  });

  it("carries both tallies on a card when both kinds exist", async () => {
    agents = [EXPERT, FEATURE];
    renderPanel();
    await screen.findByRole("columnheader", { name: "专家" });

    await userEvent.click(screen.getByText("卡片"));

    // One tally per kind, per card: alice's, then bob's all-zero pair. Either
    // button opens the one drawer listing that user's agents.
    expect(
      await screen.findByRole("button", { name: /^专家\s*1$/ }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^功能\s*1$/ }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^专家\s*0$/ }),
    ).toBeVisible();
    expect(
      await screen.findByRole("button", { name: /^功能\s*0$/ }),
    ).toBeVisible();
  });

  it("drops a kind's tally from the cards too when the deployment has no such kind", async () => {
    agents = [EXPERT];
    renderPanel();
    await screen.findByRole("columnheader", { name: "专家" });

    await userEvent.click(screen.getByText("卡片"));

    expect(
      await screen.findByRole("button", { name: /^专家\s*1$/ }),
    ).toBeVisible();
    expect(screen.queryByRole("button", { name: /^功能/ })).toBeNull();
  });
});
