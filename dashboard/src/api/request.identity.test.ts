import { afterEach, describe, expect, it, vi } from "vitest";

import {
  AUTH_IDENTITY_CHANGED_EVENT,
  clearAuthToken,
  setAuthToken,
} from "./request";

vi.mock("./config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));
vi.mock("../i18n", () => ({ default: { language: "en" } }));

function token(sub: string, nonce: string): string {
  const payload = btoa(JSON.stringify({ sub }))
    .replace(/=/g, "")
    .replace(/\+/g, "-")
    .replace(/\//g, "_");
  return `header.${payload}.${nonce}`;
}

describe("authentication identity changes", () => {
  afterEach(() => {
    localStorage.clear();
  });

  it("dispatches when the first token is set", () => {
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    setAuthToken(token("account-a", "first"));

    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });

  it("dispatches on account switch and token clear", () => {
    localStorage.setItem("auth_token", token("account-a", "first"));
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    setAuthToken(token("account-b", "second"));
    expect(changed).toHaveBeenCalledTimes(1);

    clearAuthToken();
    expect(changed).toHaveBeenCalledTimes(2);
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });

  it("does not dispatch for same-subject sliding renewal", () => {
    localStorage.setItem("auth_token", token("account-a", "old"));
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    setAuthToken(token("account-a", "renewed"));

    expect(changed).not.toHaveBeenCalled();
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });

  it("broadcasts cross-tab account switches but ignores same-sub renewals", () => {
    const oldToken = token("account-a", "old");
    setAuthToken(oldToken);
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "auth_token",
        oldValue: oldToken,
        newValue: token("account-a", "renewed"),
        storageArea: localStorage,
      }),
    );
    expect(changed).not.toHaveBeenCalled();

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "auth_token",
        oldValue: token("account-a", "renewed"),
        newValue: token("account-b", "next"),
        storageArea: localStorage,
      }),
    );
    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });

  it("broadcasts a cross-tab token removal and storage clear", () => {
    const currentToken = token("account-a", "old");
    setAuthToken(currentToken);
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    localStorage.removeItem("auth_token");

    window.dispatchEvent(
      new StorageEvent("storage", {
        key: "auth_token",
        oldValue: currentToken,
        newValue: null,
        storageArea: localStorage,
      }),
    );
    expect(changed).toHaveBeenCalledTimes(1);

    setAuthToken(currentToken);
    localStorage.clear();
    window.dispatchEvent(
      new StorageEvent("storage", {
        key: null,
        oldValue: null,
        newValue: null,
        storageArea: localStorage,
      }),
    );
    expect(changed).toHaveBeenCalledTimes(3);
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });

  it("dispatches when a changed token has an unrecognizable subject", () => {
    localStorage.setItem("auth_token", "not-a-jwt");
    const changed = vi.fn();
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);

    setAuthToken("also-not-a-jwt");

    expect(changed).toHaveBeenCalledTimes(1);
    window.removeEventListener(AUTH_IDENTITY_CHANGED_EVENT, changed);
  });
});
