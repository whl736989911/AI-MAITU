import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const mocks = vi.hoisted(() => ({
  getStatus: vi.fn(),
  getDetails: vi.fn(),
  claimReminder: vi.fn(),
  activate: vi.fn(),
  refresh: vi.fn(),
  importOffline: vi.fn(),
  getOfflineRequest: vi.fn(),
  user: { role: "admin", permissions: [] as string[] },
}));

vi.mock("../../api/modules/licensing", () => ({
  licensingApi: {
    getStatus: mocks.getStatus,
    getDetails: mocks.getDetails,
    claimReminder: mocks.claimReminder,
    activate: mocks.activate,
    refresh: mocks.refresh,
    getOfflineRequest: mocks.getOfflineRequest,
    importOffline: mocks.importOffline,
  },
}));
vi.mock("../../api/request", () => ({
  LICENSE_LOCKED_EVENT: "octop:license-locked",
}));
vi.mock("../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => mocks.user,
}));
vi.mock("../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("react-i18next", () => {
  const translations: Record<string, string> = {
    "license.lockedTitle": "License required",
    "license.lockedDescription": "Business features require a valid license.",
    "license.statusSummary": "{{state}} — {{reason}}",
    "license.state.UNACTIVATED": "Unactivated",
    "license.state.ACTIVE": "Active",
    "license.reason.unactivated":
      "No license has been activated for this instance.",
    "license.instanceId": "Instance ID",
    "license.adminActions": "Administrator license actions",
    "license.activate": "Activate license",
    "license.activationCode": "Activation code",
    "license.activationCodePlaceholder": "Enter an activation code.",
    "license.refreshStatus": "Refresh status",
    "license.syncOnline": "Synchronize online",
    "license.exportOfflineRequest": "Export offline request",
    "license.chooseOfflineFile": "Choose signed offline license file",
    "license.offlineImport": "Offline license file or signed token",
    "license.offlineImportPlaceholder": "Paste a signed offline license token.",
    "license.importOffline": "Import offline license",
    "license.statusFailed": "Could not read the license status.",
    "license.actionFailed": "The license action could not be completed.",
    "license.title": "License status",
    "license.remainingTime": "{{days}}d {{hours}}h {{minutes}}m",
  };
  const t = (key: string) => translations[key] ?? "Missing translation";
  const i18n = { language: "en" };
  return {
    useTranslation: () => ({ t, i18n }),
  };
});

import LicenseGate from "./index";

const activeSnapshot = {
  managed: true,
  state: "ACTIVE",
  business_allowed: true,
  reason: null,
  instance_id: "instance-1",
  license_id: "license-1",
  license_version: 1,
  license_expires_at: 1_900_000_000,
  credential_type: "online",
  expiring_soon: false,
  last_sync_at: null,
  sync_error: null,
};
const unmanagedStatus = {
  managed: false,
  business_allowed: true,
  state: null,
  reason: null,
  instance_id: "",
  license_id: null,
  license_version: null,
  license_expires_at: null,
  expiring_soon: false,
};

beforeEach(() => {
  vi.clearAllMocks();
  mocks.user = { role: "admin", permissions: [] };
  mocks.getDetails.mockResolvedValue(activeSnapshot);
  mocks.getStatus.mockResolvedValue(activeSnapshot);
  mocks.activate.mockImplementation(async () => {
    mocks.getDetails.mockResolvedValue(activeSnapshot);
    mocks.getStatus.mockResolvedValue(activeSnapshot);
    return activeSnapshot;
  });
  mocks.refresh.mockResolvedValue(activeSnapshot);
  mocks.importOffline.mockImplementation(async () => {
    mocks.getDetails.mockResolvedValue(activeSnapshot);
    mocks.getStatus.mockResolvedValue(activeSnapshot);
    return activeSnapshot;
  });
  mocks.getOfflineRequest.mockResolvedValue({
    instance_id: "instance-1",
    public_key_pem: "public-key",
    key_fingerprint: "fingerprint",
    proof: "proof",
  });
  mocks.claimReminder.mockResolvedValue({ show: false });
});

describe("LicenseGate", () => {
  it("opens business UI from the canonical unmanaged public status response", async () => {
    mocks.getStatus.mockResolvedValue(unmanagedStatus);

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    expect(await screen.findByText("business-shell")).toBeInTheDocument();
    expect(mocks.getStatus).toHaveBeenCalled();
    expect(mocks.getDetails).not.toHaveBeenCalled();
  });

  it("does not pin a healthy license status above the app", async () => {
    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    expect(await screen.findByText("business-shell")).toBeInTheDocument();
    expect(screen.queryByText("License status")).not.toBeInTheDocument();
    expect(screen.queryByText("Instance ID")).not.toBeInTheDocument();
  });

  it("hides business routes while locked and gives administrators activation controls", async () => {
    mocks.getDetails.mockResolvedValue({
      ...activeSnapshot,
      state: "UNACTIVATED",
      business_allowed: false,
      reason: "unactivated",
      license_id: null,
      license_version: null,
      license_expires_at: null,
      credential_type: null,
    });

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    expect(
      await screen.findByRole("heading", { name: "License required" }),
    ).toBeInTheDocument();
    expect(screen.queryByText("Missing translation")).not.toBeInTheDocument();
    expect(mocks.getStatus).toHaveBeenCalled();
    expect(mocks.getDetails).toHaveBeenCalled();
    expect(screen.queryByText("business-shell")).not.toBeInTheDocument();
    expect(screen.getByLabelText("Activation code")).toBeInTheDocument();
    expect(document.getElementById("license-offline-file")).toBeTruthy();
  });
  it("restores the business shell only after administrator activation succeeds", async () => {
    const user = userEvent.setup();
    mocks.getDetails.mockResolvedValue({
      ...activeSnapshot,
      state: "UNACTIVATED",
      business_allowed: false,
      reason: "unactivated",
      license_id: null,
      license_version: null,
      license_expires_at: null,
      credential_type: null,
    });

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    const field = await screen.findByLabelText("Activation code");
    await user.type(field, "ACTIVATION-CODE");
    await user.click(screen.getByRole("button", { name: "Activate license" }));

    expect(mocks.activate).toHaveBeenCalledWith("ACTIVATION-CODE");
    expect(await screen.findByText("business-shell")).toBeInTheDocument();
  });
  it("restores the business shell after importing a signed offline token", async () => {
    const user = userEvent.setup();
    mocks.getDetails.mockResolvedValue({
      ...activeSnapshot,
      state: "UNACTIVATED",
      business_allowed: false,
      reason: "unactivated",
      license_id: null,
      license_version: null,
      license_expires_at: null,
      credential_type: null,
    });

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    await user.type(
      await screen.findByLabelText("Offline license file or signed token"),
      "SIGNED-OFFLINE-TOKEN",
    );
    await user.click(
      screen.getByRole("button", { name: "Import offline license" }),
    );

    expect(mocks.importOffline).toHaveBeenCalledWith("SIGNED-OFFLINE-TOKEN");
    expect(await screen.findByText("business-shell")).toBeInTheDocument();
  });

  it("allows enterprise administrators to access canonical license management", async () => {
    mocks.user = { role: "enterprise_admin", permissions: [] };
    mocks.getDetails.mockResolvedValue({
      ...activeSnapshot,
      state: "UNACTIVATED",
      business_allowed: false,
      reason: "unactivated",
      license_id: null,
      license_version: null,
      license_expires_at: null,
      credential_type: null,
    });

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    expect(await screen.findByLabelText("Activation code")).toBeInTheDocument();
    expect(mocks.getDetails).toHaveBeenCalled();
  });
  it("checks the server-side daily reminder after authentication and on focus", async () => {
    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    await waitFor(() => expect(mocks.claimReminder).toHaveBeenCalledTimes(1));
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(mocks.claimReminder).toHaveBeenCalledTimes(2));
  });

  it("does not expose activation controls to non-administrators", async () => {
    mocks.user = { role: "user", permissions: [] };
    mocks.getStatus.mockResolvedValue({
      ...activeSnapshot,
      state: "LOCKED",
      business_allowed: false,
      reason: "revoked",
    });

    render(
      <LicenseGate>
        <div>business-shell</div>
      </LicenseGate>,
    );

    await waitFor(() => {
      expect(screen.queryByText("business-shell")).not.toBeInTheDocument();
    });
    expect(mocks.getDetails).not.toHaveBeenCalled();
    expect(document.getElementById("license-activation-code")).toBeNull();
  });
});
