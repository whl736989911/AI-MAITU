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
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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

beforeEach(() => {
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

async function openGrants() {
  await userEvent.click(
    await screen.findByRole("button", { name: "模块授权" }),
  );
}

describe("Admin → Org units: department module grants", () => {
  it("offers the grantable modules and marks the department's own set", async () => {
    stored = ["channels"];
    renderPage();
    await openGrants();

    const granted = await screen.findByRole("button", { name: "通道" });
    await waitFor(() =>
      expect(granted).toHaveAttribute("aria-pressed", "true"),
    );
    expect(screen.getByRole("button", { name: "工作台/终端" })).toHaveAttribute(
      "aria-pressed",
      "false",
    );
    // ``providers`` is neither stored nor grantable: offering it would only
    // produce a 403 on save.
    expect(screen.queryByRole("button", { name: "云端" })).toBeNull();
  });

  it("keeps a stored module the operator may not grant, and saves the whole set", async () => {
    stored = ["channels", "providers"];
    renderPage();
    await openGrants();

    const kept = await screen.findByRole("button", { name: "云端" });
    await waitFor(() => expect(kept).toHaveAttribute("aria-pressed", "true"));

    await userEvent.click(screen.getByRole("button", { name: "工作台/终端" }));
    await userEvent.click(screen.getByRole("button", { name: "保存授权" }));

    await waitFor(() => expect(saved).toHaveLength(1));
    expect(saved[0].body).toEqual({
      permissions: ["channels", "providers", "terminal"],
    });
  });

  it("revokes by clearing a box, since the body is the whole set", async () => {
    stored = ["channels", "terminal"];
    renderPage();
    await openGrants();

    const terminal = await screen.findByRole("button", {
      name: "工作台/终端",
    });
    await waitFor(() =>
      expect(terminal).toHaveAttribute("aria-pressed", "true"),
    );
    await userEvent.click(terminal);
    await userEvent.click(screen.getByRole("button", { name: "保存授权" }));

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

    expect(
      await screen.findByRole("button", { name: "新建部门" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "编辑" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "模块授权" }),
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "删除" }),
    ).toBeInTheDocument();
  });

  it("shows no grants control to an actor with no department to authorize", async () => {
    renderPage(EMPLOYEE);

    // The tree still renders — reads are the ``users`` key, not a role.
    expect(await screen.findByText("销售部")).toBeTruthy();
    expect(screen.queryByRole("button", { name: "模块授权" })).toBeNull();
  });
});
