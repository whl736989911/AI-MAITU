import type { ReactNode } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes, useLocation } from "react-router-dom";
import { describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../../../api/modules/auth";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import zh from "../../../locales/zh.json";
import AgentNotReadyScreen from "./AgentNotReadyScreen";
import MinimalAgentSessionNav from "./MinimalAgentSessionNav";
import SessionList from "./SessionList";

function lookup(key: string): string {
  let value: unknown = zh;
  for (const part of key.split(".")) {
    if (value === null || typeof value !== "object") return key;
    value = (value as Record<string, unknown>)[part];
  }
  return typeof value === "string" ? value : key;
}

vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => lookup(key) }),
}));

function Destination() {
  const location = useLocation();
  return <output data-testid="destination">{location.pathname}</output>;
}

function renderEmptyState(permissions: string[]) {
  const user: OctopUser = {
    id: 5,
    username: "member",
    role: "user",
    display_name: null,
    locale: "zh",
    permissions,
  };
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <MemoryRouter initialEntries={["/chat"]}>
        <AgentNotReadyScreen agent={null} noAgents />
        <Routes>
          <Route path="*" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </CurrentUserProvider>,
  );
}

function renderWithCurrentUser(permissions: string[], children: ReactNode) {
  const user: OctopUser = {
    id: 5,
    username: "member",
    role: "user",
    display_name: null,
    locale: "zh",
    permissions,
  };
  return render(
    <CurrentUserProvider user={user} setUser={() => undefined}>
      <MemoryRouter initialEntries={["/chat"]}>
        {children}
        <Routes>
          <Route path="*" element={<Destination />} />
        </Routes>
      </MemoryRouter>
    </CurrentUserProvider>,
  );
}

describe("chat empty-state access wording and destination", () => {
  it("keeps expert-only users on the Expert wording and route", () => {
    renderEmptyState(["experts"]);

    expect(
      screen.getByRole("heading", { name: "还没有专家" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("从专家列表选择模板，快速创建你的第一个 AI 专家。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去创建专家" }));
    expect(screen.getByTestId("destination")).toHaveTextContent("/experts");
  });

  it("sends feature-only users to Features and uses feature wording", () => {
    renderEmptyState(["features"]);

    expect(
      screen.getByRole("heading", { name: "还没有功能" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("从功能列表创建一个功能，然后即可在对话中使用。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去创建功能" }));
    expect(screen.getByTestId("destination")).toHaveTextContent("/features");
  });

  it("uses agent wording for users with both permissions and keeps the Expert route", () => {
    renderEmptyState(["experts", "features"]);

    expect(
      screen.getByRole("heading", { name: "还没有智能体" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("创建一个专家或功能，开始使用你的第一个 AI 智能体。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去创建智能体" }));
    expect(screen.getByTestId("destination")).toHaveTextContent("/experts");
  });

  it("does not offer an inaccessible destination when neither permission is held", () => {
    renderEmptyState(["users"]);

    expect(
      screen.getByRole("heading", { name: "暂无可用的智能体" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("button")).toBeNull();
  });

  it("adapts the full session list's empty prompt and route for feature-only users", () => {
    renderWithCurrentUser(
      ["features"],
      <SessionList
        agents={[]}
        sessions={[]}
        activeId={null}
        activeAgentId={null}
        hasMore={false}
        loadingMore={false}
        onLoadMore={() => undefined}
        onFetchAllSessions={() => undefined}
        onSelect={() => undefined}
        onAgentSelect={() => undefined}
        onDelete={() => undefined}
        onRename={() => undefined}
        onPin={() => undefined}
        onFork={() => undefined}
      />,
    );

    expect(
      screen.getByText("从功能列表创建一个功能，然后即可在对话中使用。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去创建功能" }));
    expect(screen.getByTestId("destination")).toHaveTextContent("/features");
  });

  it("adapts the minimal session nav's empty prompt and route for feature-only users", () => {
    renderWithCurrentUser(
      ["features"],
      <MinimalAgentSessionNav
        agents={[]}
        activeId={null}
        activeAgentId={null}
        activeSessions={[]}
        onSelect={() => undefined}
        onAgentSelect={() => undefined}
        onNewChat={() => undefined}
        onDeleteActive={() => undefined}
        onRenameActive={async () => true}
        onPinActive={async () => true}
        onFork={() => undefined}
      />,
    );

    expect(
      screen.getByText("从功能列表创建一个功能，然后即可在对话中使用。"),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: "去创建功能" }));
    expect(screen.getByTestId("destination")).toHaveTextContent("/features");
  });
});
