import { describe, expect, it } from "vitest";
import { getApiUrlForBase, getWsUrlForBase } from "./config";

describe("API URLs under configured base paths", () => {
  it("keeps root deployment API paths unchanged", () => {
    expect(getApiUrlForBase("/auth/me", "")).toBe("/api/auth/me");
    expect(getWsUrlForBase("/agents/a/chat/ws", "", "https:", "app.test")).toBe(
      "wss://app.test/api/agents/a/chat/ws",
    );
  });

  it("prefixes API and WebSocket URLs once under a subpath", () => {
    expect(getApiUrlForBase("auth/me", "/maitu/")).toBe("/maitu/api/auth/me");
    expect(
      getWsUrlForBase("/agents/a/chat/ws", "/maitu/", "https:", "app.test"),
    ).toBe("wss://app.test/maitu/api/agents/a/chat/ws");
  });

  it("retains explicitly configured API origins", () => {
    expect(getApiUrlForBase("/models", "https://api.test/")).toBe(
      "https://api.test/api/models",
    );
    expect(
      getWsUrlForBase("/stream", "http://api.test", "http:", "app.test"),
    ).toBe("ws://api.test/api/stream");
  });
});
