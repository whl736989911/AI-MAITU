import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router-dom";

vi.mock("../../context/ThemeContext", () => ({
  useTheme: () => ({ isDark: false }),
}));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  const i18n = { language: "en" };
  return { useTranslation: () => ({ t, i18n }) };
});

import { authApi, type AuthStatus } from "../../api/modules/auth";
import SetupPage from "./index";
import { STEP_FINISH, wizardApi, wizardSession } from "./wizardClient";

const pendingStatus: AuthStatus = {
  setup_required: true,
  setup_done: false,
  enabled: true,
  has_password: true,
  admin_created: true,
  database_bound: true,
  wizard_password_required: true,
  wizard_password_exists: true,
};

function renderSetup() {
  render(
    <MemoryRouter initialEntries={["/setup"]}>
      <Routes>
        <Route path="/setup" element={<SetupPage />} />
        <Route
          path="/login"
          element={<div>installation-completed-login</div>}
        />
      </Routes>
    </MemoryRouter>,
  );
}

describe("setup recovery after the administrator exists", () => {
  beforeEach(() => {
    wizardSession.clearAll();
    vi.spyOn(authApi, "getAuthStatus").mockResolvedValue(pendingStatus);
    wizardSession.saveStep(STEP_FINISH);
    vi.spyOn(wizardApi, "validateToken").mockResolvedValue({ valid: true });
    vi.spyOn(wizardApi, "finish").mockResolvedValue({ ok: true });
  });

  afterEach(() => {
    for (const api of [authApi, wizardApi]) {
      for (const method of Object.values(api)) {
        if (vi.isMockFunction(method)) method.mockRestore();
      }
    }
    wizardSession.clearAll();
  });

  it("requires the bootstrap password before resuming without stored credentials", async () => {
    vi.spyOn(wizardApi, "verifyPassword").mockResolvedValue({
      wizard_token: "recovered-wizard-token",
      expires_in: 3600,
    });
    renderSetup();

    const password = await screen.findByLabelText("wizard.password.label");
    expect(wizardApi.finish).not.toHaveBeenCalled();
    fireEvent.change(password, { target: { value: "BootstrapSecret12" } });
    fireEvent.click(
      screen.getByRole("button", { name: "wizard.password.verifyButton" }),
    );

    expect(
      await screen.findByText("installation-completed-login"),
    ).toBeInTheDocument();
  });

  it("resumes through begin when the deployment explicitly disables the bootstrap password", async () => {
    vi.mocked(authApi.getAuthStatus).mockResolvedValue({
      ...pendingStatus,
      wizard_password_required: false,
      wizard_password_exists: false,
    });
    vi.spyOn(wizardApi, "begin").mockResolvedValue({
      wizard_token: "passwordless-wizard-token",
      expires_in: 3600,
    });
    renderSetup();

    expect(
      await screen.findByText("installation-completed-login"),
    ).toBeInTheDocument();
  });
});
