import { render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";

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

describe("Login password recovery hint", () => {
  it("shows the host CLI and administrator reset paths", () => {
    render(
      <MemoryRouter>
        <LoginPage />
      </MemoryRouter>,
    );
    expect(screen.getByTestId("login-forgot-password").textContent).toMatch(
      /octop user passwd/,
    );
    expect(screen.getByTestId("login-forgot-password").textContent).toMatch(
      /administrator|管理员/,
    );
  });
});
