import { afterEach, describe, expect, it, vi } from "vitest";
import { isNetworkFetchError } from "./networkError";

describe("isNetworkFetchError", () => {
  afterEach(() => vi.unstubAllGlobals());

  it("recognizes offline browser and fetch failures", () => {
    vi.stubGlobal("navigator", { onLine: false });
    expect(isNetworkFetchError(new Error("offline"))).toBe(true);
    vi.stubGlobal("navigator", { onLine: true });
    expect(isNetworkFetchError(new TypeError("Failed to fetch"))).toBe(true);
  });

  it("does not classify HTTP failures as network outages", () => {
    vi.stubGlobal("navigator", { onLine: true });
    expect(
      isNetworkFetchError(new Error("Request failed with status 503")),
    ).toBe(false);
  });
});
