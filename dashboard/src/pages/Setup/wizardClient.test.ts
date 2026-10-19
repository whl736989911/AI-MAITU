import { beforeEach, describe, expect, it, vi } from "vitest";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));
vi.mock("../../api/request", () => ({ request: requestMock }));

import { wizardApi, wizardSession } from "./wizardClient";

describe("setup wizard bearer authorization", () => {
  beforeEach(() => {
    requestMock.mockReset();
    requestMock.mockResolvedValue({ ok: true, driver: "sqlite" });
    sessionStorage.clear();
  });

  it("sends the wizard token when probing a database", async () => {
    await wizardApi.testDatabase({ driver: "sqlite" }, "wizard-token");
    expect(requestMock).toHaveBeenCalledWith(
      "/setup/test-database",
      expect.objectContaining({
        headers: { Authorization: "Bearer wizard-token" },
      }),
    );
  });

  it("sends the wizard token when applying a database", async () => {
    await wizardApi.applyDatabase({ driver: "sqlite" }, "wizard-token");
    expect(requestMock).toHaveBeenCalledWith(
      "/setup/database",
      expect.objectContaining({
        headers: { Authorization: "Bearer wizard-token" },
      }),
    );
  });

  it("sends the wizard token when saving license configuration", async () => {
    await wizardApi.saveLicenseConfig(
      { center_url: "https://license.example.test" },
      "wizard-token",
    );
    expect(requestMock).toHaveBeenCalledWith(
      "/setup/license-config",
      expect.objectContaining({
        headers: { Authorization: "Bearer wizard-token" },
      }),
    );
  });

  it("keeps wizard and setup JWT tokens independent", () => {
    wizardSession.saveToken("wizard");
    wizardSession.saveSetupJwt("jwt");
    expect(wizardSession.loadToken()).toBe("wizard");
    expect(wizardSession.loadSetupJwt()).toBe("jwt");
  });
});
