import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { createInstance } from "i18next";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type * as I18nModule from "react-i18next";
import en from "../../locales/en.json";
import zh from "../../locales/zh.json";

const { brand } = vi.hoisted(() => ({
  brand: {
    name: { zh: "演示平台", en: "Demo Workspace" },
    logos: { wordmark_dark: "/dark.svg", wordmark_light: "/light.svg" },
  },
}));

const i18n = createInstance();

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof I18nModule>();
  return { ...actual, useTranslation: () => ({ t: i18n.t.bind(i18n), i18n }) };
});
vi.mock("../../context/BrandingContext", () => ({
  useBranding: () => brand,
}));
vi.mock("../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("../../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: () => Promise.resolve({ setup_required: false }),
    getOauthStatus: () => Promise.resolve({ providers: [] }),
    getCaptcha: () => Promise.resolve({ provider: "slider" }),
  },
}));
vi.mock("../../api", () => ({
  clearAuthToken: vi.fn(),
  setAuthToken: vi.fn(),
}));
vi.mock("../../utils/locale", () => ({
  applyGuestLocale: () => Promise.resolve(),
  applyUserLocale: () => Promise.resolve(),
}));
vi.mock("./CaptchaField", () => ({ default: () => null }));

import LoginPage from "./index";

beforeEach(async () => {
  await i18n.init({
    lng: "en",
    fallbackLng: "en",
    resources: { en: { translation: en }, zh: { translation: zh } },
  });
});

function renderLogin() {
  render(
    <MemoryRouter>
      <LoginPage />
    </MemoryRouter>,
  );
  return screen.getByTestId("login-forgot-password");
}

describe("Login password recovery", () => {
  it.each([
    ["en", "Demo Workspace", "administrator"],
    ["zh", "演示平台", "管理员"],
  ])(
    "uses the deployment identity for recovery in %s",
    async (locale, name, admin) => {
      await i18n.changeLanguage(locale);
      const hint = renderLogin();
      expect(hint).toHaveTextContent(name);
      expect(hint).toHaveTextContent(admin);
      expect(hint).not.toHaveTextContent(/octop|maitu|user passwd/i);
      expect(hint).not.toHaveTextContent(/\{\{/);
    },
  );
});
