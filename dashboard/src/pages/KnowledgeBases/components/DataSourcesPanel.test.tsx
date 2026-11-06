import { App, ConfigProvider } from "antd";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  test: vi.fn(),
  sync: vi.fn(),
  runs: vi.fn(),
}));
const userHooks = vi.hoisted(() => ({ current: vi.fn() }));

vi.mock("../../../api/modules/dataSources", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/dataSources")
  >();
  return { ...actual, dataSourcesApi: { ...actual.dataSourcesApi, ...api } };
});
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => userHooks.current(),
}));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

import DataSourcesPanel from "./DataSourcesPanel";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

const source = (id: string) => ({
  id,
  data_source_id: id,
  knowledge_base_id: "kb",
  name: `Source ${id}`,
  kind: "local" as const,
  config: {},
  server: "",
  share: "",
  root_path: `/data/${id}`,
  username: "",
  has_credentials: false,
  read_only: true,
  include_globs: "",
  exclude_globs: "",
  scan_interval_seconds: 0,
  connection_status: "unknown" as const,
  connection_error: null,
  last_scan_at: null,
  last_scan_ok_at: null,
  created_by: null,
  sync_status: "idle" as const,
  sync_error: null,
  last_synced_at: null,
  created_at: 0,
  updated_at: 0,
});

beforeEach(() => {
  api.list.mockReset();
  api.test.mockReset();
  api.sync.mockReset();
  api.runs.mockReset();
  userHooks.current.mockReturnValue({
    id: 1,
    username: "manager",
    role: "admin",
    permissions: ["knowledge_bases"],
  });
});

describe("<DataSourcesPanel />", () => {
  it("keeps every independently tested source busy until its own request settles", async () => {
    const first = deferred<{ ok: boolean; detail: string }>();
    const second = deferred<{ ok: boolean; detail: string }>();
    api.list.mockResolvedValue([source("a"), source("b")]);
    api.test.mockImplementation((id: string) =>
      id === "a" ? first.promise : second.promise,
    );
    const user = userEvent.setup();
    render(
      <ConfigProvider>
        <App>
          <DataSourcesPanel baseId="kb" canWriteBase canManageBase />
        </App>
      </ConfigProvider>,
    );

    await screen.findByText("Source a");
    const testButtons = screen.getAllByRole("button", {
      name: "knowledgeBases.dataSources.test",
    });
    await user.click(testButtons[0]);
    await user.click(testButtons[1]);
    expect(testButtons[0]).toBeDisabled();
    expect(testButtons[1]).toBeDisabled();
    await user.click(testButtons[0]);
    expect(api.test).toHaveBeenCalledTimes(2);
    await act(async () => {
      first.resolve({ ok: true, detail: "connected" });
      await first.promise;
    });

    await waitFor(() => expect(testButtons[0]).toBeEnabled());
    expect(testButtons[1]).toBeDisabled();
    await act(async () => {
      second.resolve({ ok: true, detail: "connected" });
      await second.promise;
    });
    await waitFor(() => expect(testButtons[1]).toBeEnabled());
  });
  it("reports a confirmed sync as successful when the optional run history read fails", async () => {
    api.list.mockResolvedValue([source("a")]);
    api.sync.mockResolvedValue(source("a"));
    api.runs.mockRejectedValue(new Error("history unavailable"));
    const onDocumentsChanged = vi.fn();
    const user = userEvent.setup();
    render(
      <ConfigProvider>
        <App>
          <DataSourcesPanel
            baseId="kb"
            canWriteBase
            canManageBase
            onDocumentsChanged={onDocumentsChanged}
          />
        </App>
      </ConfigProvider>,
    );

    await screen.findByText("Source a");
    await user.click(
      screen.getByRole("button", {
        name: "knowledgeBases.dataSources.sync",
      }),
    );

    await waitFor(() => expect(onDocumentsChanged).toHaveBeenCalledTimes(1));
    expect(api.sync).toHaveBeenCalledWith("a");
  });
});
