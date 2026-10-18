import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type * as RequestModule from "./request";

vi.mock("./config", () => ({ getApiUrl: (path: string) => `/api${path}` }));
vi.mock("../i18n", () => ({ default: { language: "en" } }));

function lockedResponse(code: string): Response {
  return new Response(JSON.stringify({ error: { code, message: "locked" } }), {
    status: 423,
    headers: { "content-type": "application/json" },
  });
}

describe("license lock request handling", () => {
  const fetchMock = vi.fn();
  const onLocked = vi.fn();
  let mod: typeof RequestModule;
  beforeEach(async () => {
    vi.resetModules();
    fetchMock.mockReset();
    onLocked.mockReset();
    vi.stubGlobal("fetch", fetchMock);
    mod = await import("./request");
    mod.setAuthToken("test-token");
    window.addEventListener(mod.LICENSE_LOCKED_EVENT, onLocked);
  });

  afterEach(() => {
    window.removeEventListener(mod.LICENSE_LOCKED_EVENT, onLocked);
    vi.unstubAllGlobals();
  });

  it("notifies the global gate when a business API returns the license lock", async () => {
    fetchMock.mockResolvedValue(lockedResponse("INSTANCE_LICENSE_LOCKED"));

    await expect(mod.request("/agents")).rejects.toThrow("423");

    expect(onLocked).toHaveBeenCalledTimes(1);
    expect(onLocked.mock.calls[0][0].detail).toEqual({ path: "/agents" });
  });

  it("does not treat unrelated 423 errors as license locks", async () => {
    fetchMock.mockResolvedValue(lockedResponse("RESOURCE_LOCKED"));

    await expect(mod.request("/agents")).rejects.toThrow("423");

    expect(onLocked).not.toHaveBeenCalled();
  });
});
