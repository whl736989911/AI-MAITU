import { useEffect } from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthGuard from "./AuthGuard";
import { useCurrentUser } from "../hooks/useCurrentUser";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  me: vi.fn(),
  token: vi.fn(),
}));

vi.mock("../api/modules/auth", () => ({
  authApi: { getAuthStatus: mocks.status, me: mocks.me },
}));
vi.mock("../api/request", () => ({
  AUTH_IDENTITY_CHANGED_EVENT: "octop:auth-identity-changed",
  clearAuthToken: vi.fn(),
  getAuthToken: mocks.token,
  isSameAuthIdentity: (previous: string, next: string) =>
    previous === next || previous.split(":")[0] === next.split(":")[0],
}));
vi.mock("../utils/locale", () => ({ applyUserLocale: vi.fn() }));

function ProtectedRouteChange() {
  const navigate = useNavigate();
  useEffect(() => {
    navigate("/b");
  }, [navigate]);
  return <div>protected-shell</div>;
}

const status = { setup_required: false, has_admin: true };
const user = {
  id: 1,
  username: "admin",
  role: "admin",
  locale: "zh",
  permissions: [],
};

describe("AuthGuard startup", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.token.mockReturnValue("token");
  });

  it("shows a retry panel on network failure and recovers on retry", async () => {
    mocks.status
      .mockRejectedValueOnce(new TypeError("Failed to fetch"))
      .mockResolvedValue(status);
    mocks.me.mockResolvedValue(user);
    const userEventInstance = userEvent.setup();

    render(
      <MemoryRouter>
        <AuthGuard>
          <div>protected-shell</div>
        </AuthGuard>
      </MemoryRouter>,
    );

    expect(await screen.findByRole("alert")).toBeInTheDocument();
    expect(screen.queryByText("protected-shell")).not.toBeInTheDocument();
    await userEventInstance.click(
      screen.getByRole("button", { name: /errors\.retry|Retry|重试/ }),
    );
    expect(await screen.findByText("protected-shell")).toBeInTheDocument();
    expect(mocks.status).toHaveBeenCalledTimes(2);
  });

  it("does not rerun auth probing for ordinary in-app route changes", async () => {
    mocks.status.mockResolvedValue(status);
    mocks.me.mockResolvedValue(user);

    render(
      <MemoryRouter initialEntries={["/a"]}>
        <AuthGuard>
          <Routes>
            <Route path="/a" element={<ProtectedRouteChange />} />
            <Route path="/b" element={<div>arrived-at-b</div>} />
          </Routes>
        </AuthGuard>
      </MemoryRouter>,
    );

    expect(await screen.findByText("arrived-at-b")).toBeInTheDocument();
    await waitFor(() => expect(mocks.status).toHaveBeenCalledTimes(1));
    expect(mocks.me).toHaveBeenCalledTimes(1);
  });
  it("ignores an old user response after switching identity", async () => {
    const alice = { ...user, username: "alice", role: "user" };
    const bob = { ...user, username: "bob", role: "admin" };
    let resolveStale!: (value: typeof user) => void;
    const staleResponse = new Promise<typeof user>((resolve) => {
      resolveStale = resolve;
    });
    mocks.token.mockReturnValue("token-a");
    mocks.status.mockResolvedValue(status);
    mocks.me
      .mockResolvedValueOnce(alice)
      .mockImplementationOnce(() => staleResponse)
      .mockResolvedValueOnce(bob);

    function CurrentUserLabel() {
      const current = useCurrentUser();
      return (
        <div>{`${current?.username ?? "none"}:${current?.role ?? ""}`}</div>
      );
    }

    render(
      <MemoryRouter>
        <AuthGuard>
          <CurrentUserLabel />
        </AuthGuard>
      </MemoryRouter>,
    );
    expect(await screen.findByText("alice:user")).toBeInTheDocument();

    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(mocks.me).toHaveBeenCalledTimes(2));

    mocks.token.mockReturnValue("token-b");
    act(() => window.dispatchEvent(new Event("octop:auth-identity-changed")));
    expect(await screen.findByText("bob:admin")).toBeInTheDocument();

    await act(async () => {
      resolveStale(alice);
      await staleResponse;
    });
    expect(screen.getByText("bob:admin")).toBeInTheDocument();
    expect(screen.queryByText("alice:user")).not.toBeInTheDocument();
  });
  it("retries /auth/me after a same-account token is renewed", async () => {
    let rejectStale!: (error: Error) => void;
    const staleRequest = new Promise<typeof user>((_resolve, reject) => {
      rejectStale = reject;
    });
    mocks.token.mockReturnValue("alice:old");
    mocks.status.mockResolvedValue(status);
    mocks.me
      .mockImplementationOnce(() => staleRequest)
      .mockResolvedValueOnce({ ...user, username: "alice" });

    render(
      <MemoryRouter>
        <AuthGuard>
          <div>protected-shell</div>
        </AuthGuard>
      </MemoryRouter>,
    );
    await waitFor(() => expect(mocks.me).toHaveBeenCalledTimes(1));

    mocks.token.mockReturnValue("alice:renewed");
    await act(async () => {
      rejectStale(new DOMException("Stale token response", "AbortError"));
      await staleRequest.catch(() => undefined);
    });

    expect(await screen.findByText("protected-shell")).toBeInTheDocument();
    expect(mocks.me).toHaveBeenCalledTimes(2);
  });
});
