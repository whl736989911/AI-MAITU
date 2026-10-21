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
import { render, screen, waitFor, within } from "@testing-library/react";
import userEventLibrary, {
  PointerEventsCheckLevel,
  type UserEvent,
} from "@testing-library/user-event";
import type { OctopUser } from "../../../api/modules/auth";
import type { OctopAgent } from "../../../context/AgentContext";
import zh from "../../../locales/zh.json";
import type { PermissionCatalogItem } from "../../../components/PermissionPicker";

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
let permissionCatalog: PermissionCatalogItem[] = [];
let userEvent: UserEvent;

beforeEach(() => {
  userEvent = userEventLibrary.setup({
    pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
    delay: null,
  });
  permissionCatalog = [];
  requestMock.mockReset();
  requestMock.mockImplementation(async (url: string): Promise<unknown> => {
    if (url === "/users") return USERS;
    if (url === "/agents?scope=all") return agents;
    if (url === "/users/permissions") return permissionCatalog;
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
function normalizedText(element: HTMLElement): string {
  return element.textContent?.replace(/\s+/g, " ").trim() ?? "";
}

async function findVisibleColumnHeader(
  table: HTMLElement,
  name: string,
): Promise<HTMLElement> {
  let header: HTMLElement | undefined;
  await waitFor(() => {
    header = within(table)
      .getAllByRole("columnheader", { hidden: true })
      .find(
        (candidate) =>
          candidate.closest('[aria-hidden="true"]') === null &&
          normalizedText(candidate) === name,
      );
    expect(header).toBeDefined();
  });
  expect(header).toBeVisible();
  expect(header).toHaveAccessibleName(name);
  return header!;
}

function findButtonByAccessibleLabel(
  root: HTMLElement,
  name: string,
): HTMLElement | undefined {
  return within(root)
    .getAllByRole("button", { hidden: true })
    .find(
      (candidate) =>
        candidate.closest('[aria-hidden="true"]') === null &&
        (
          candidate.getAttribute("aria-label") ?? normalizedText(candidate)
        ).replace(/\s+/g, "") === name.replace(/\s+/g, ""),
    );
}

async function expectCountButton(
  root: HTMLElement,
  name: string,
): Promise<void> {
  let button: HTMLElement | undefined;
  await waitFor(() => {
    button = findButtonByAccessibleLabel(root, name);
    expect(button).toBeDefined();
  });
  expect(button).toBeVisible();
  expect(button).toHaveAccessibleName(name);
}

function findButtonByText(
  root: HTMLElement,
  text: string,
): HTMLElement | undefined {
  return within(root)
    .getAllByRole("button", { hidden: true })
    .find(
      (candidate) =>
        candidate.closest('[aria-hidden="true"]') === null &&
        normalizedText(candidate) === text,
    );
}

function findRadioByText(
  root: HTMLElement,
  text: string,
): HTMLElement | undefined {
  return within(root)
    .getAllByRole("radio", { hidden: true })
    .find((candidate) => {
      const form = candidate.closest("form");
      return (
        candidate.closest('[aria-hidden="true"]') === null &&
        normalizedText(candidate).startsWith(text) &&
        form !== null &&
        within(form).queryAllByRole("textbox", { hidden: true }).length > 0
      );
    });
}
describe("Admin → Users agent columns", () => {
  it("draws only the experts column when the deployment has no features", async () => {
    agents = [EXPERT, agent("reviewer", "agent", 1)];
    renderPanel();

    const tableElement = await screen.findByRole("table", { hidden: true });
    expect(tableElement).toBeVisible();
    await findVisibleColumnHeader(tableElement, "专家");
    // Bob owns nothing, but the column is about the deployment — his row reads
    // 0 rather than the column dropping out from under alice.
    await expectCountButton(tableElement, "专家 2");
    await expectCountButton(tableElement, "专家 0");
    expect(
      within(tableElement)
        .getAllByRole("columnheader", { hidden: true })
        .some(
          (header) =>
            header.closest('[aria-hidden="true"]') === null &&
            normalizedText(header) === "功能",
        ),
    ).toBe(false);
    expect(findButtonByAccessibleLabel(tableElement, "功能 0")).toBeUndefined();
  });

  it("draws only the features column when the deployment has no experts", async () => {
    agents = [FEATURE];
    renderPanel();

    const tableElement = await screen.findByRole("table", { hidden: true });
    expect(tableElement).toBeVisible();
    expect(await findVisibleColumnHeader(tableElement, "功能")).toBeVisible();
    await expectCountButton(tableElement, "功能 1");
    await expectCountButton(tableElement, "功能 0");
    expect(
      within(tableElement)
        .getAllByRole("columnheader", { hidden: true })
        .some(
          (header) =>
            header.closest('[aria-hidden="true"]') === null &&
            normalizedText(header) === "专家",
        ),
    ).toBe(false);
    expect(findButtonByAccessibleLabel(tableElement, "专家 0")).toBeUndefined();
  });

  it("draws both columns, each counting its own kind, when both exist", async () => {
    agents = [EXPERT, FEATURE, agent("second-feature", "feature", 1)];
    renderPanel();

    const tableElement = await screen.findByRole("table", { hidden: true });
    expect(tableElement).toBeVisible();
    expect(await findVisibleColumnHeader(tableElement, "专家")).toBeVisible();
    expect(await findVisibleColumnHeader(tableElement, "功能")).toBeVisible();
    // Alice's two kinds are told apart inside one row.
    await expectCountButton(tableElement, "专家 1");
    await expectCountButton(tableElement, "功能 2");
    // And a user with neither still reads 0 / 0, not a missing row.
    await expectCountButton(tableElement, "专家 0");
    await expectCountButton(tableElement, "功能 0");
  });

  it("carries both tallies on a card when both kinds exist", async () => {
    agents = [EXPERT, FEATURE];
    renderPanel();
    const tableElement = await screen.findByRole("table", { hidden: true });
    expect(tableElement).toBeVisible();
    await findVisibleColumnHeader(tableElement, "专家");

    await userEvent.click(screen.getByText("卡片"));

    // One tally per kind, per card: alice's, then bob's all-zero pair. Either
    // button opens the one drawer listing that user's agents.
    await expectCountButton(document.body, "专家 1");
    await expectCountButton(document.body, "功能 1");
    await expectCountButton(document.body, "专家 0");
    await expectCountButton(document.body, "功能 0");
  });

  it("drops a kind's tally from the cards too when the deployment has no such kind", async () => {
    agents = [EXPERT];
    renderPanel();
    const tableElement = await screen.findByRole("table", { hidden: true });
    expect(tableElement).toBeVisible();
    await findVisibleColumnHeader(tableElement, "专家");

    await userEvent.click(screen.getByText("卡片"));

    await expectCountButton(document.body, "专家 1");
    expect(
      screen
        .getAllByRole("button", { hidden: true })
        .some(
          (button) =>
            button.closest('[aria-hidden="true"]') === null &&
            button.getAttribute("aria-label")?.startsWith("功能"),
        ),
    ).toBe(false);
  });
});

describe("Admin → Users org-unit requirements", () => {
  it("does not offer an org unit when creating an enterprise administrator", async () => {
    renderPanel();

    const newUser = findButtonByText(document.body, "新建用户");
    expect(newUser).toBeDefined();
    expect(newUser).toBeVisible();
    expect(newUser).toHaveAccessibleName("新建用户");
    await userEvent.click(newUser!);
    const drawer = screen.getByRole("dialog", { hidden: true });
    expect(drawer).toBeVisible();
    expect(drawer).toHaveAccessibleName("新建用户");
    const role = findRadioByText(drawer, "企业管理员");
    expect(role).toBeDefined();
    expect(role).toBeVisible();
    expect(role).toHaveAccessibleName(/^企业管理员/);
    const form = role!.closest("form");
    expect(form).not.toBeNull();
    await userEvent.click(role!);
    expect(role).toHaveAttribute("aria-checked", "true");

    expect(
      within(form as HTMLFormElement).queryAllByRole("combobox", {
        hidden: true,
      }),
    ).toHaveLength(0);
  });

  it("keeps the org unit required for employees and unit administrators", async () => {
    renderPanel();

    const newUser = findButtonByText(document.body, "新建用户");
    expect(newUser).toBeDefined();
    expect(newUser).toBeVisible();
    expect(newUser).toHaveAccessibleName("新建用户");
    await userEvent.click(newUser!);
    const drawer = screen.getByRole("dialog", { hidden: true });
    expect(drawer).toBeVisible();
    expect(drawer).toHaveAccessibleName("新建用户");
    const rolePicker = findRadioByText(drawer, "部门管理员");
    expect(rolePicker).toBeDefined();
    expect(rolePicker).toBeVisible();
    expect(rolePicker).toHaveAccessibleName(/^部门管理员/);
    const form = rolePicker!.closest("form");
    expect(form).not.toBeNull();
    const comboboxes = within(form as HTMLFormElement).getAllByRole(
      "combobox",
      { hidden: true },
    );
    expect(comboboxes).toHaveLength(1);
    const orgUnit = comboboxes[0];
    expect(orgUnit).toBeVisible();
    expect(orgUnit).toHaveAccessibleName("所属组织单元");
    expect(orgUnit).toHaveAttribute("aria-required", "true");

    await userEvent.click(rolePicker!);
    expect(rolePicker).toHaveAttribute("aria-checked", "true");
    expect(orgUnit).toHaveAttribute("aria-required", "true");
  });
});

describe("Admin → Users team permissions", () => {
  it("offers Teams in the new-user form without granting it by default", async () => {
    permissionCatalog = [
      { key: "experts", category: "settings", label: "专家", can_grant: true },
      { key: "teams", category: "settings", label: "团队", can_grant: true },
    ];
    renderPanel();
    const newUser = findButtonByText(document.body, "新建用户");
    expect(newUser).toBeDefined();
    expect(newUser).toBeVisible();
    expect(newUser).toHaveAccessibleName("新建用户");
    await userEvent.click(newUser!);

    const drawerElement = screen.getByRole("dialog", { hidden: true });
    expect(drawerElement).toBeVisible();
    expect(drawerElement).toHaveAccessibleName("新建用户");
    const experts = findButtonByText(drawerElement, "专家");
    const teams = findButtonByText(drawerElement, "团队");
    expect(experts).toBeDefined();
    expect(teams).toBeDefined();
    expect(experts).toHaveAccessibleName("专家");
    expect(teams).toHaveAccessibleName("团队");
    expect(experts).toHaveAttribute("aria-pressed", "true");
    expect(teams).toHaveAttribute("aria-pressed", "false");

    await userEvent.click(teams!);
    expect(teams).toHaveAttribute("aria-pressed", "true");
    await userEvent.click(experts!);
    expect(experts).toHaveAttribute("aria-pressed", "false");
    expect(teams).toHaveAttribute("aria-pressed", "false");
  });
});
