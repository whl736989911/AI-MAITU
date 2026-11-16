import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));

vi.mock("../i18n", () => ({
  default: { language: "zh" },
}));

function unauthorizedResponse(): Response {
  return new Response("{}", {
    status: 401,
    headers: { "content-type": "application/json" },
  });
}

describe("401 handling", () => {
  const replace = vi.fn();
  let mod: typeof import("./request");

  beforeEach(async () => {
    vi.resetModules();
    localStorage.clear();
    replace.mockClear();
    Object.defineProperty(window, "location", {
      configurable: true,
      value: { pathname: "/chat", replace },
    });
    vi.stubGlobal(
      "fetch",
      vi.fn(() => Promise.resolve(unauthorizedResponse())),
    );
    mod = await import("./request");
    mod.setAuthToken("expired-token");
  });

  it("lets an in-app listener take over the redirect instead of reloading", async () => {
    const listener = vi.fn((event: Event) => event.preventDefault());
    window.addEventListener(mod.UNAUTHORIZED_EVENT, listener);

    await expect(mod.request("/agents")).rejects.toThrow();

    expect(listener).toHaveBeenCalledOnce();
    expect(replace).not.toHaveBeenCalled();
    expect(mod.getAuthToken()).toBe("");

    window.removeEventListener(mod.UNAUTHORIZED_EVENT, listener);
  });

  it("falls back to a full-page navigation when nothing handles the event", async () => {
    await expect(mod.request("/agents")).rejects.toThrow();

    expect(replace).toHaveBeenCalledWith("/login");
  });

  it("redirects only once when several requests fail concurrently", async () => {
    const listener = vi.fn((event: Event) => event.preventDefault());
    window.addEventListener(mod.UNAUTHORIZED_EVENT, listener);

    await Promise.allSettled([
      mod.request("/agents"),
      mod.request("/skills"),
      mod.request("/cron"),
    ]);

    expect(listener).toHaveBeenCalledOnce();

    window.removeEventListener(mod.UNAUTHORIZED_EVENT, listener);
  });
  it("ignores late 401 and access-expired responses after switching accounts", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    const accountToken = (sub: string, nonce: string) =>
      `header.${btoa(JSON.stringify({ sub }))
        .replace(/=/g, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_")}.${nonce}`;

    mod.setAuthToken(accountToken("account-a", "old"));
    const unauthorized = mod.request("/agents");
    mod.setAuthToken(accountToken("account-b", "current"));
    resolveResponse(unauthorizedResponse());

    await expect(unauthorized).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(accountToken("account-b", "current"));
    expect(replace).not.toHaveBeenCalled();

    let resolveExpiry!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveExpiry = resolve;
          }),
      ),
    );
    mod.setAuthToken(accountToken("account-a", "old-expired"));
    const expired = mod.request("/agents");
    mod.setAuthToken(accountToken("account-b", "current"));
    resolveExpiry(
      new Response(JSON.stringify({ error: { code: "ACCESS_EXPIRED" } }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(expired).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(accountToken("account-b", "current"));
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores an expired response after logout and same-account re-login", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    const originalToken = `header.${btoa(JSON.stringify({ sub: "account-a" }))
      .replace(/=/g, "")
      .replace(/\+/g, "-")
      .replace(/\//g, "_")}.original`;
    mod.setAuthToken(originalToken);
    const pending = mod.request("/agents");
    mod.clearAuthToken();
    mod.setAuthToken(originalToken);
    resolveResponse(
      new Response(JSON.stringify({ error: { code: "ACCESS_EXPIRED" } }), {
        status: 403,
        headers: { "content-type": "application/json" },
      }),
    );

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(originalToken);
    expect(replace).not.toHaveBeenCalled();
  });

  it("ignores a stale /auth/me 401 after same-account token renewal", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    const tokenFor = (nonce: string) =>
      `header.${btoa(JSON.stringify({ sub: "account-a" }))
        .replace(/=/g, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_")}.${nonce}`;
    const renewed = tokenFor("renewed");
    mod.setAuthToken(tokenFor("old"));
    const pending = mod.request("/auth/me");
    mod.setAuthToken(renewed);
    resolveResponse(unauthorizedResponse());

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(renewed);
    expect(replace).not.toHaveBeenCalled();
  });

  it("does not restore a token from a late response after logout", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    mod.setAuthToken("account-a-token");
    const pending = mod.request("/agents");
    mod.clearAuthToken();
    resolveResponse(
      new Response("{}", {
        status: 200,
        headers: {
          [mod.ACCESS_TOKEN_RESPONSE_HEADER]: "late-account-a-token",
          "content-type": "application/json",
        },
      }),
    );

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe("");
  });
  it("does not apply a late renewal to another active account", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    const tokenFor = (sub: string, nonce: string) =>
      `header.${btoa(JSON.stringify({ sub }))
        .replace(/=/g, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_")}.${nonce}`;
    mod.setAuthToken(tokenFor("account-a", "old"));
    const pending = mod.request("/agents");
    mod.setAuthToken(tokenFor("account-b", "current"));
    resolveResponse(
      new Response("{}", {
        status: 200,
        headers: {
          [mod.ACCESS_TOKEN_RESPONSE_HEADER]: tokenFor("account-a", "renewed"),
          "content-type": "application/json",
        },
      }),
    );

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(tokenFor("account-b", "current"));
  });

  it("keeps a same-account sliding renewal", async () => {
    const tokenFor = (sub: string, nonce: string) =>
      `header.${btoa(JSON.stringify({ sub }))
        .replace(/=/g, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_")}.${nonce}`;
    const renewed = tokenFor("account-a", "renewed");
    mod.setAuthToken(tokenFor("account-a", "old"));
    vi.stubGlobal(
      "fetch",
      vi.fn(() =>
        Promise.resolve(
          new Response("{}", {
            status: 200,
            headers: {
              [mod.ACCESS_TOKEN_RESPONSE_HEADER]: renewed,
              "content-type": "application/json",
            },
          }),
        ),
      ),
    );

    await mod.request("/agents");

    expect(mod.getAuthToken()).toBe(renewed);
  });
  it("does not let an older-token 401 clear a same-account renewal", async () => {
    let resolveResponse!: (response: Response) => void;
    vi.stubGlobal(
      "fetch",
      vi.fn(
        () =>
          new Promise<Response>((resolve) => {
            resolveResponse = resolve;
          }),
      ),
    );
    const tokenFor = (nonce: string) =>
      `header.${btoa(JSON.stringify({ sub: "account-a" }))
        .replace(/=/g, "")
        .replace(/\+/g, "-")
        .replace(/\//g, "_")}.${nonce}`;
    const renewed = tokenFor("renewed");
    mod.setAuthToken(tokenFor("old"));
    const pending = mod.request("/agents");
    mod.setAuthToken(renewed);
    resolveResponse(unauthorizedResponse());

    await expect(pending).rejects.toMatchObject({ name: "AbortError" });
    expect(mod.getAuthToken()).toBe(renewed);
    expect(replace).not.toHaveBeenCalled();
  });
});
