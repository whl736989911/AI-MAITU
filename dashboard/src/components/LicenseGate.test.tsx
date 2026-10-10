import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import LicenseGate from "./LicenseGate";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  activate: vi.fn(),
  refresh: vi.fn(),
  me: vi.fn(),
  token: vi.fn(),
}));

vi.mock("../api/modules/license", () => ({
  licenseApi: {
    status: mocks.status,
    activate: mocks.activate,
    refresh: mocks.refresh,
  },
}));
vi.mock("../api/modules/auth", () => ({ authApi: { me: mocks.me } }));
vi.mock("../api/request", () => ({
  getAuthToken: mocks.token,
  INSTANCE_LICENSE_LOCKED_EVENT: "octop:instance-license-locked",
}));

const locked = {
  state: "LOCKED",
  allowed: false,
  reason: "unactivated",
  license_expires_at: null,
  valid_until: null,
  contact: "licensing@example.test",
  timezone: "Asia/Tokyo",
};

function renderGate() {
  return render(
    <MemoryRouter>
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>
    </MemoryRouter>,
  );
}

describe("LicenseGate", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.token.mockReturnValue("");
    mocks.status.mockResolvedValue(locked);
  });

  it("keeps business UI unmounted while locked and presents support and server timezone", async () => {
    renderGate();

    expect(await screen.findByText("license.title")).toBeInTheDocument();
    expect(screen.queryByText("business-shell")).not.toBeInTheDocument();
    expect(screen.getByText("licensing@example.test")).toBeInTheDocument();
    expect(screen.getByText("Asia/Tokyo")).toBeInTheDocument();
  });

  it("only remounts business UI after a server-confirmed successful activation", async () => {
    const user = userEvent.setup();
    mocks.token.mockReturnValue("session-token");
    mocks.me.mockResolvedValue({ role: "enterprise_admin" });
    mocks.activate.mockResolvedValue({});
    mocks.status.mockResolvedValueOnce(locked).mockResolvedValueOnce({
      ...locked,
      state: "ACTIVE",
      allowed: true,
      reason: null,
    });

    renderGate();
    const codeField = await screen.findByRole("textbox", {
      name: "license.activationCode",
    });
    await user.type(codeField, "REAL-ACTIVATION-CODE");
    await user.click(screen.getByRole("button", { name: "license.activate" }));

    expect(await screen.findByText("business-shell")).toBeInTheDocument();
  });

  it("unmounts business UI immediately on a locked response and confirms public status", async () => {
    mocks.status
      .mockResolvedValueOnce({
        ...locked,
        state: "ACTIVE",
        allowed: true,
        reason: null,
      })
      .mockResolvedValueOnce(locked);

    renderGate();
    expect(await screen.findByText("business-shell")).toBeInTheDocument();
    window.dispatchEvent(new Event("octop:instance-license-locked"));

    expect(await screen.findByText("license.title")).toBeInTheDocument();
    expect(screen.queryByText("business-shell")).not.toBeInTheDocument();
  });

  it("does not offer activation to a non-administrator", async () => {
    mocks.token.mockReturnValue("user-token");
    mocks.me.mockResolvedValue({ role: "user" });

    renderGate();
    expect(await screen.findByText("license.title")).toBeInTheDocument();
    await waitFor(() => expect(mocks.me).toHaveBeenCalledTimes(1));
    expect(
      screen.queryByRole("button", { name: "license.activate" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("license.adminRequired")).toBeInTheDocument();
  });
});
