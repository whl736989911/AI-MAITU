import { act, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../api/modules/auth";
import { useCurrentUser } from "../hooks/useCurrentUser";
import AuthGuard from "./AuthGuard";

const mocks = vi.hoisted(() => ({ me: vi.fn() }));

vi.mock("../api/modules/auth", () => ({
  authApi: {
    getAuthStatus: async () => ({ setup_required: false }),
    me: mocks.me,
  },
}));
vi.mock("../api/request", () => ({
  getAuthToken: () => "token",
  clearAuthToken: vi.fn(),
}));
vi.mock("../utils/locale", () => ({ applyUserLocale: async () => undefined }));

const account = (permissions: string[]): OctopUser => ({
  id: 7,
  username: "user",
  role: "user",
  display_name: null,
  locale: "zh",
  permissions,
});

function ConnectorAccess() {
  const user = useCurrentUser();
  return (
    <span>
      {user?.permissions?.includes("connectors")
        ? "可选连接器"
        : "已撤销连接器"}
    </span>
  );
}

describe("AuthGuard permission refresh", () => {
  it("removes a revoked permission when an open tab regains focus", async () => {
    mocks.me.mockReset();
    mocks.me
      .mockResolvedValueOnce(account(["connectors"]))
      .mockResolvedValueOnce(account([]));

    await act(async () => {
      render(
        <MemoryRouter>
          <AuthGuard>
            <ConnectorAccess />
          </AuthGuard>
        </MemoryRouter>,
      );
    });

    expect(screen.getByText("可选连接器")).toBeInTheDocument();
    await act(async () => {
      window.dispatchEvent(new Event("focus"));
    });
    await waitFor(() => {
      expect(screen.getByText("已撤销连接器")).toBeInTheDocument();
    });
    expect(mocks.me).toHaveBeenCalledTimes(2);
  });
});
