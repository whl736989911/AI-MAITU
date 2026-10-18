import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const mocks = vi.hoisted(() => ({
  getBubblewrapStatus: vi.fn(),
  ensureBubblewrap: vi.fn(),
}));

vi.mock("react-i18next", () => {
  const labels: Record<string, string> = {
    "advancedSettings.bwrap.installDiagnose": "Install and diagnose",
  };
  const t = (key: string) => labels[key] ?? key;
  return { useTranslation: () => ({ t }) };
});

vi.mock("../../../api/modules/nativeSandbox", () => ({
  nativeSandboxApi: {
    getBubblewrapStatus: mocks.getBubblewrapStatus,
    ensureBubblewrap: mocks.ensureBubblewrap,
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn() },
}));

import BubblewrapSettings from "./BubblewrapSettings";

const status = (diagnosis: "missing_binary" | "userns_denied") => ({
  status: "degraded" as const,
  reason: diagnosis,
  detail: `detail-${diagnosis}`,
  diagnosis,
  commands: [`sudo install ${diagnosis}`],
});

beforeEach(() => {
  vi.clearAllMocks();
  mocks.getBubblewrapStatus.mockResolvedValue(status("missing_binary"));
  mocks.ensureBubblewrap.mockResolvedValue(status("userns_denied"));
});

describe("BubblewrapSettings", () => {
  it("reads status without installing, then updates diagnosis only after explicit action", async () => {
    render(<BubblewrapSettings />);

    expect(
      await screen.findByText("sudo install missing_binary"),
    ).toBeInTheDocument();
    expect(mocks.ensureBubblewrap).not.toHaveBeenCalled();

    await userEvent.click(
      screen.getByRole("button", {
        name: "Install and diagnose",
      }),
    );

    expect(
      await screen.findByText("sudo install userns_denied"),
    ).toBeInTheDocument();
    expect(screen.getByText("detail-userns_denied")).toBeInTheDocument();
    expect(mocks.ensureBubblewrap).toHaveBeenCalledTimes(1);
  });
});
