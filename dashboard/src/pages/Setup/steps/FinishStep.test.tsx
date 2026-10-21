import { beforeEach, describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";

const mocks = vi.hoisted(() => ({
  finish: vi.fn(),
  login: vi.fn(),
  resolveToken: vi.fn(),
  clearAll: vi.fn(),
  loadSetupJwt: vi.fn(),
  navigate: vi.fn(),
}));

vi.mock("../../../api/modules/auth", () => ({
  authApi: { login: mocks.login },
}));
vi.mock("../../../api/request", () => ({ setAuthToken: vi.fn() }));
vi.mock("../../../i18n", () => ({ refreshServerLabels: vi.fn() }));
vi.mock("../../../utils/locale", () => ({ applyUserLocale: vi.fn() }));
vi.mock("react-router-dom", () => ({ useNavigate: () => mocks.navigate }));
vi.mock("react-i18next", () => {
  const t = (key: string) => key;
  return { useTranslation: () => ({ t }) };
});
vi.mock("../wizardClient", () => ({
  wizardApi: { finish: mocks.finish },
  wizardSession: {
    clearAll: mocks.clearAll,
    loadSetupJwt: mocks.loadSetupJwt,
  },
  resolveSetupProbeToken: mocks.resolveToken,
}));

import FinishStep from "./FinishStep";

describe("<FinishStep />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.resolveToken.mockResolvedValue("wizard-token");
    mocks.loadSetupJwt.mockReturnValue(null);
    mocks.finish.mockRejectedValue(new Error("bootstrap failed"));
    mocks.login.mockResolvedValue({
      access_token: "admin-token",
      user: { locale: "en" },
    });
  });

  it("keeps setup state and credentials available when finish fails, then retries", async () => {
    render(
      <FinishStep
        adminCreds={{ username: "admin", password: "secret" }}
        providerDraft={null}
      />,
    );

    expect(mocks.login).not.toHaveBeenCalled();
    expect(await screen.findByText("wizard.finish.failed")).toBeInTheDocument();
    expect(mocks.clearAll).not.toHaveBeenCalled();
    expect(mocks.navigate).not.toHaveBeenCalled();

    mocks.finish.mockResolvedValue({ ok: true });
    fireEvent.click(screen.getByRole("button", { name: "common.retry" }));
    await waitFor(() => expect(mocks.finish).toHaveBeenCalledTimes(2));
    await waitFor(() =>
      expect(mocks.login).toHaveBeenCalledWith("admin", "secret"),
    );
    await waitFor(() => expect(mocks.clearAll).toHaveBeenCalledTimes(1));
    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith("/chat", { replace: true }),
    );
  });

  it("resumes a restored administrator session without returning to login", async () => {
    mocks.finish.mockResolvedValue({ ok: true });
    mocks.loadSetupJwt.mockReturnValue("restored-admin-token");
    render(<FinishStep adminCreds={null} providerDraft={null} />);

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith("/chat", { replace: true }),
    );
    expect(mocks.login).not.toHaveBeenCalled();
  });

  it("returns to login only after finishing when no admin credentials remain", async () => {
    mocks.finish.mockResolvedValue({ ok: true });
    render(<FinishStep adminCreds={null} providerDraft={null} />);

    await waitFor(() =>
      expect(mocks.navigate).toHaveBeenCalledWith("/login", { replace: true }),
    );
    expect(mocks.clearAll).toHaveBeenCalledOnce();
  });
});
