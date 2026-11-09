import { act, cleanup, render, screen } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { afterEach, expect, it, vi } from "vitest";
import AuthGuard from "./AuthGuard";
import { getAuthToken, setAuthToken } from "../api/request";

vi.mock("../api/config", () => ({
  getApiUrl: (path: string) => `/api${path}`,
}));
vi.mock("../i18n", () => ({ default: { language: "en" } }));
vi.mock("../utils/locale", () => ({ applyUserLocale: vi.fn() }));

const originalLocation = window.location;

afterEach(() => {
  cleanup();
  vi.useRealTimers();
  vi.unstubAllGlobals();
  localStorage.clear();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: originalLocation,
  });
});

it("expires the session at the server deadline when the browser clock is ahead", async () => {
  const epoch = Date.UTC(2026, 10, 9, 0, 0, 0);
  const expiresAt = (epoch + 2000) / 1000;
  let serverNow = epoch;
  const replace = vi.fn();
  Object.defineProperty(window, "location", {
    configurable: true,
    value: { pathname: "/chat", replace },
  });
  vi.useFakeTimers();
  vi.setSystemTime(epoch + 60_000);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (input: RequestInfo | URL) => {
      const headers = {
        "content-type": "application/json",
        date: new Date(serverNow).toUTCString(),
      };
      if (String(input) === "/api/setup/status") {
        return new Response(
          JSON.stringify({ setup_required: false, admin_created: true }),
          { headers },
        );
      }
      if (String(input) !== "/api/auth/me")
        throw new Error(`Unexpected URL: ${input}`);
      if (serverNow >= expiresAt * 1000) {
        return new Response(
          JSON.stringify({
            error: {
              code: "ACCESS_EXPIRED",
              details: {
                notice: "Contact your administrator.",
                expires_at: expiresAt,
              },
            },
          }),
          { status: 403, headers },
        );
      }
      return new Response(
        JSON.stringify({
          id: 7,
          username: "trial",
          role: "user",
          locale: "en",
          permissions: [],
          access_expires_at: expiresAt,
        }),
        { headers },
      );
    }),
  );
  setAuthToken("trial-session");
  await act(async () => {
    render(
      <MemoryRouter>
        <AuthGuard>
          <div>Authorized content</div>
        </AuthGuard>
      </MemoryRouter>,
    );
    await vi.advanceTimersByTimeAsync(0);
  });
  expect(screen.getByText("Authorized content")).toBeInTheDocument();
  expect(getAuthToken()).toBe("trial-session");
  await act(async () => {
    await vi.advanceTimersByTimeAsync(0);
  });

  serverNow = expiresAt * 1000;
  await act(async () => {
    await vi.advanceTimersByTimeAsync(2000);
  });
  expect(getAuthToken()).toBe("");
  expect(replace).toHaveBeenCalledWith(
    expect.stringContaining("/login?oidc_error=access_expired"),
  );
});
