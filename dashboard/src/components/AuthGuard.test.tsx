import { useEffect } from "react";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, Route, Routes, useNavigate } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import AuthGuard from "./AuthGuard";

const mocks = vi.hoisted(() => ({
  status: vi.fn(),
  me: vi.fn(),
  token: vi.fn(),
}));

vi.mock("../api/modules/auth", () => ({
  authApi: { getAuthStatus: mocks.status, me: mocks.me },
}));
vi.mock("../api/request", () => ({
  clearAuthToken: vi.fn(),
  getAuthToken: mocks.token,
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
});
