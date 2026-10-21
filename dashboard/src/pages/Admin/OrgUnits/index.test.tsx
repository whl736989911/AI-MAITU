/**
 * Admin → Org units, the department's module grants (design §2.1/§2.3).
 *
 * A department's keys are what every member of its subtree gains, and
 * ``PUT /api/org-units/{key}/permissions`` replaces the whole set — so the two
 * things worth pinning are the ones a wrong picker would get wrong silently:
 * it must not offer a module this operator may not hand out, and it must not
 * drop a stored module it never displayed. The copy is the Chinese-locale one,
 * answered from the shipped ``zh.json``.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor, within } from "@testing-library/react";
import userEventLibrary, {
  PointerEventsCheckLevel,
  type UserEvent,
} from "@testing-library/user-event";
import type { OctopUser } from "../../../api/modules/auth";
import type * as I18nModule from "react-i18next";
import type * as RequestModule from "../../../api/request";
import zh from "../../../locales/zh.json";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../../../api/request", async (importOriginal) => {
  const actual = await importOriginal<typeof RequestModule>();
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

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof I18nModule>();

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
  const api = {
    t: (key: string, options?: string | TOptions, extra?: TOptions): string => {
      const vars = options && typeof options === "object" ? options : extra;
      const found = lookup(key);
      if (found !== undefined) return interpolate(found, vars);
      if (typeof options === "string") return interpolate(options, vars);
      if (options?.defaultValue) return interpolate(options.defaultValue, vars);
      return key;
    },
    i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
  };
  return { ...actual, useTranslation: () => api };
});

import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import AdminOrgUnitsPage from "./index";

const ADMIN: OctopUser = {
  id: 99,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
  permissions: ["users"],
};

/** An employee holding ``users``: no reach, so no department to authorize. */
const EMPLOYEE: OctopUser = {
  ...ADMIN,
  id: 12,
  username: "dana",
  role: "user",
};

const UNITS = [
  {
    key: "acme-sales",
    label: { zh: "销售部", en: "Sales" },
    parent_key: null,
  },
];

/** ``GET /api/users/permissions``: what the operator may hand out, and what not. */
const CATALOG = [
  {
    key: "channels",
    category: "settings",
    label: "通道",
    can_grant: true,
  },
  {
    key: "terminal",
    category: "control",
    label: "工作台/终端",
    can_grant: true,
  },
  {
    key: "providers",
    category: "admin",
    label: "云端",
    page: "models",
    page_label: "模型",
    can_grant: false,
  },
];

let stored: string[] = [];
let saved: { url: string; method: string; body: unknown }[] = [];
let userEvent: UserEvent;

beforeEach(() => {
  userEvent = userEventLibrary.setup({
    pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
    delay: null,
  });
  requestMock.mockReset();
  stored = [];
  saved = [];
  requestMock.mockImplementation(
    async (url: string, init?: { method?: string; body?: string }) => {
      if (url === "/org-units") return { units: UNITS };
      if (url === "/users") return [];
      if (url === "/users/permissions") return CATALOG;
      if (url === "/org-units/acme-sales/permissions") {
        if (init?.method === "PUT") {
          saved.push({
            url,
            method: "PUT",
            body: init.body ? JSON.parse(init.body) : null,
          });
          stored = JSON.parse(init.body ?? "{}").permissions ?? [];
        }
        return { unit_key: "acme-sales", permissions: stored };
      }
      throw new Error(`unexpected request: ${url}`);
    },
  );
});

function renderPage(user: OctopUser = ADMIN) {
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <AdminOrgUnitsPage />
    </CurrentUserProvider>,
  );
}
function findButtonByText(
  root: HTMLElement,
  text: string,
): HTMLElement | undefined {
  return within(root)
    .getAllByRole("button", { hidden: true })
    .find(
      (button) =>
        button.closest('[aria-hidden="true"]') === null &&
        (button.getAttribute("aria-label") ?? button.textContent)
          ?.replace(/\s+/g, " ")
          .trim() === text,
    );
}

async function waitForButtonByText(
  root: HTMLElement,
  text: string,
): Promise<HTMLElement> {
  let button: HTMLElement | undefined;
  await waitFor(() => {
    button = findButtonByText(root, text);
    expect(button).toBeDefined();
  });
  return button!;
}

function expectButtonName(button: HTMLElement, name: string): void {
  expect(button).toBeVisible();
  expect(button).toHaveAccessibleName(name);
}

async function openGrants() {
  const grantsButton = await waitForButtonByText(document.body, "模块授权");
  expectButtonName(grantsButton, "模块授权");
  await waitFor(() => {
    expect(grantsButton).toBeEnabled();
    expect(window.getComputedStyle(grantsButton).pointerEvents).not.toBe(
      "none",
    );
  });
  await userEvent.click(grantsButton);
  const dialog = await screen.findByRole("dialog", { hidden: true });
  expect(dialog).toBeVisible();
  return dialog;
}

describe("Admin → Org units: department module grants", () => {
  it("offers the grantable modules and marks the department's own set", async () => {
    stored = ["channels"];
    renderPage();
    const dialog = await openGrants();

    const granted = await waitForButtonByText(dialog, "通道");
    expectButtonName(granted, "通道");
    await waitFor(() =>
      expect(granted).toHaveAttribute("aria-pressed", "true"),
    );
    const terminal = findButtonByText(dialog, "工作台/终端");
    expect(terminal).toBeDefined();
    expectButtonName(terminal!, "工作台/终端");
    expect(terminal).toHaveAttribute("aria-pressed", "false");
    // ``providers`` is neither stored nor grantable: offering it would only
    // produce a 403 on save.
    expect(findButtonByText(dialog, "云端")).toBeUndefined();
  });

  it("keeps a stored module the operator may not grant, and saves the whole set", async () => {
    stored = ["channels", "providers"];
    renderPage();
    const dialog = await openGrants();
    const kept = await waitForButtonByText(dialog, "云端");
    expect(kept).toBeDefined();
    expectButtonName(kept!, "云端");
    await waitFor(() => expect(kept).toHaveAttribute("aria-pressed", "true"));

    const terminal = findButtonByText(dialog, "工作台/终端");
    const saveButton = findButtonByText(dialog, "保存授权");
    expect(terminal).toBeDefined();
    expect(saveButton).toBeDefined();
    expectButtonName(terminal!, "工作台/终端");
    expectButtonName(saveButton!, "保存授权");
    await userEvent.click(terminal!);
    await userEvent.click(saveButton!);

    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].body).toEqual({
      permissions: ["channels", "providers", "terminal"],
    });
  });

  it("revokes by clearing a box, since the body is the whole set", async () => {
    stored = ["channels", "terminal"];
    renderPage();
    const dialog = await openGrants();
    const terminal = await waitForButtonByText(dialog, "工作台/终端");
    expect(terminal).toBeDefined();
    expectButtonName(terminal!, "工作台/终端");
    await waitFor(() =>
      expect(terminal).toHaveAttribute("aria-pressed", "true"),
    );
    await userEvent.click(terminal!);
    const saveButton = findButtonByText(dialog, "保存授权");
    expect(saveButton).toBeDefined();
    expectButtonName(saveButton!, "保存授权");
    await userEvent.click(saveButton!);

    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].body).toEqual({ permissions: ["channels"] });
  });

  it("shows department management controls to an enterprise administrator", async () => {
    const enterpriseAdmin: OctopUser = {
      ...ADMIN,
      id: 44,
      username: "enterprise-admin",
      role: "enterprise_admin",
    };
    renderPage(enterpriseAdmin);
    await screen.findByText("销售部");

    for (const name of ["新建部门", "编辑", "模块授权", "删除"]) {
      const button = findButtonByText(document.body, name);
      expect(button).toBeDefined();
      expectButtonName(button!, name);
    }
  });

  it("shows no grants control to an actor with no department to authorize", async () => {
    renderPage(EMPLOYEE);

    // The tree still renders — reads are the ``users`` key, not a role.
    expect(await screen.findByText("销售部")).toBeTruthy();
    expect(findButtonByText(document.body, "模块授权")).toBeUndefined();
  });
});
