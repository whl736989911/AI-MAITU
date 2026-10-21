import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

import type { UpdateStatus } from "../../../api/modules/update";

/**
 * Resolve copy from the shipped zh bundle so the tests exercise the same
 * strings the UI shows (no duplicated Chinese fallbacks in the component).
 */
vi.mock("react-i18next", async () => {
  const bundle = (
    (await import("../../../locales/zh.json")) as {
      default: Record<string, unknown>;
    }
  ).default;
  const lookup = (key: string): string | undefined => {
    let node: unknown = bundle;
    for (const part of key.split(".")) {
      if (!node || typeof node !== "object") return undefined;
      node = (node as Record<string, unknown>)[part];
    }
    return typeof node === "string" ? node : undefined;
  };
  const interpolate = (
    template: string,
    options?: Record<string, unknown>,
  ): string =>
    template.replace(/\{\{(\w+)\}\}/g, (match, name: string) =>
      options && name in options ? String(options[name]) : match,
    );
  const t = (
    key: string,
    fallback?: string | Record<string, unknown>,
    options?: Record<string, unknown>,
  ): string => {
    const interpolation = typeof fallback === "object" ? fallback : options;
    const template =
      lookup(key) ??
      (typeof fallback === "string" ? fallback : undefined) ??
      (typeof interpolation?.defaultValue === "string"
        ? interpolation.defaultValue
        : undefined) ??
      key;
    return interpolate(template, interpolation);
  };
  return {
    useTranslation: () => ({
      t,
      i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
    }),
  };
});

const mocks = vi.hoisted(() => ({
  getUpdateStatus: vi.fn(),
  checkForUpdates: vi.fn(),
  upgrade: vi.fn(),
  restartService: vi.fn(),
  requestRestart: vi.fn(),
  formatServerIsoDateTime: vi.fn(
    (iso: string, timeZone?: string) => `SERVER[${iso}]@${timeZone}`,
  ),
}));

vi.mock("../../../api/modules/update", () => ({
  updateApi: {
    getUpdateStatus: mocks.getUpdateStatus,
    checkForUpdates: mocks.checkForUpdates,
    upgrade: mocks.upgrade,
    restartService: mocks.restartService,
  },
}));

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));

vi.mock("../../../utils/formatMessageTime", () => ({
  formatServerIsoDateTime: mocks.formatServerIsoDateTime,
}));

vi.mock("../../../context/ServiceRestartContext", () => ({
  useServiceRestartContext: () => ({
    restartPhase: "idle",
    isRestarting: false,
    requestRestart: mocks.requestRestart,
    executeRestart: vi.fn(),
    resetRestart: vi.fn(),
  }),
}));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: 1,
    username: "admin",
    role: "admin",
    permissions: [],
  }),
}));

import UpdateConfig from "./UpdateConfig";

const INITIAL_CHECKED_AT = "2026-01-01T00:00:00+00:00";
const STATUS_UNAVAILABLE =
  "无法连接服务器以读取更新状态，当前显示的信息可能已过时。";
const NO_RELEASE = "检查完成：授权中心暂未发布适用于当前平台的更新包。";

function updateStatus(overrides: Partial<UpdateStatus> = {}): UpdateStatus {
  return {
    current_version: "1.0.0",
    latest_version: null,
    has_update: false,
    is_editable: false,
    service_mode: null,
    error: null,
    source: null,
    release_notes: null,
    center_configured: true,
    missing_configuration: [],
    install_available: true,
    checked_at: INITIAL_CHECKED_AT,
    upgrade_phase: "idle",
    upgrade_error: null,
    upgrade_target: null,
    ...overrides,
  };
}

async function renderLoaded() {
  render(<UpdateConfig />);
  await screen.findByText(`上次检查：SERVER[${INITIAL_CHECKED_AT}]@UTC`);
}

async function clickCheck() {
  await userEvent.click(screen.getByRole("button", { name: "检查更新" }));
}

beforeEach(() => {
  localStorage.clear();
  vi.clearAllMocks();
  mocks.getUpdateStatus.mockResolvedValue(updateStatus());
  mocks.checkForUpdates.mockResolvedValue(updateStatus());
});

describe("UpdateConfig", () => {
  it("renders one network-failure notice for an HTTP 200 error result", async () => {
    mocks.checkForUpdates.mockResolvedValue(
      updateStatus({
        error: "无法检查更新中心：connection refused",
        checked_at: "2026-01-02T00:00:00+00:00",
      }),
    );
    await renderLoaded();

    await clickCheck();

    expect(
      await screen.findByText("无法检查更新中心：connection refused"),
    ).toBeInTheDocument();
    expect(screen.getByText("检查更新失败")).toBeInTheDocument();
    expect(
      screen.getByText(
        "更新检查未成功，请根据具体错误核对配置、连接或发行签名；这不表示当前已是最新版本，上次检查结果已保留。",
      ),
    ).toBeInTheDocument();
    // With an error the version card must not claim a checked-but-empty center.
    expect(screen.getByText("尚未检查")).toBeInTheDocument();
    // Exactly one alert: the stale-server notice must not stack on top of it.
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(STATUS_UNAVAILABLE)).not.toBeInTheDocument();
    expect(mocks.formatServerIsoDateTime).toHaveBeenCalledWith(
      "2026-01-02T00:00:00+00:00",
      "UTC",
    );
  });

  it("reports when the center has no version higher than the current one", async () => {
    mocks.checkForUpdates.mockResolvedValue(
      updateStatus({
        latest_version: "1.0.0",
        has_update: false,
        source: "authorization_center",
        checked_at: "2026-01-03T00:00:00+00:00",
      }),
    );
    await renderLoaded();

    await clickCheck();

    expect(
      await screen.findByText(
        "检查完成：当前版本 1.0.0 已是最新，无更高版本。",
      ),
    ).toBeInTheDocument();
    expect(
      screen.getByText("上次检查：SERVER[2026-01-03T00:00:00+00:00]@UTC"),
    ).toBeInTheDocument();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("reports when the center has no compatible release package", async () => {
    mocks.checkForUpdates.mockResolvedValue(
      updateStatus({
        latest_version: null,
        checked_at: "2026-01-04T00:00:00+00:00",
      }),
    );
    await renderLoaded();

    await clickCheck();

    expect(
      await screen.findByText(
        "上次检查：SERVER[2026-01-04T00:00:00+00:00]@UTC",
      ),
    ).toBeInTheDocument();
    expect(mocks.checkForUpdates).toHaveBeenCalledTimes(1);
    expect(screen.getByText(NO_RELEASE)).toBeInTheDocument();
    // The version card agrees with the notice: no release for this platform,
    // not the "not checked" placeholder.
    expect(screen.getByText("无适配发布包")).toBeInTheDocument();
    expect(screen.queryByText("尚未检查")).not.toBeInTheDocument();
  });

  it("does not claim a completed check for a never-requested status", async () => {
    mocks.getUpdateStatus.mockResolvedValue(updateStatus({ checked_at: null }));
    render(<UpdateConfig />);

    expect(await screen.findByText("尚未检查")).toBeInTheDocument();
    expect(screen.queryByText(NO_RELEASE)).not.toBeInTheDocument();
    expect(screen.queryByText(/检查完成/)).not.toBeInTheDocument();
    expect(screen.queryAllByRole("alert")).toHaveLength(0);
  });

  it("still reports an independent check result after a historical upgrade failure", async () => {
    mocks.getUpdateStatus.mockResolvedValue(
      updateStatus({
        upgrade_phase: "failed",
        upgrade_error: "上一次升级失败",
      }),
    );
    mocks.checkForUpdates.mockResolvedValue(
      updateStatus({
        latest_version: "1.0.0",
        has_update: false,
        upgrade_phase: "failed",
        upgrade_error: "上一次升级失败",
        checked_at: "2026-01-06T00:00:00+00:00",
      }),
    );
    render(<UpdateConfig />);
    expect(
      await screen.findByText("升级失败：上一次升级失败"),
    ).toBeInTheDocument();

    await clickCheck();

    expect(
      await screen.findByText(
        "检查完成：当前版本 1.0.0 已是最新，无更高版本。",
      ),
    ).toBeInTheDocument();
    // The historical upgrade error stays visible; it just no longer blocks the
    // independent version check result.
    expect(screen.getByText("升级失败：上一次升级失败")).toBeInTheDocument();
  });

  it("shows a single error for an HTTP failure without the availability notice", async () => {
    mocks.getUpdateStatus
      .mockResolvedValueOnce(updateStatus())
      .mockRejectedValue(new Error("Failed to fetch"));
    mocks.checkForUpdates.mockRejectedValue(
      new Error("500 - update center exploded"),
    );
    await renderLoaded();

    await clickCheck();

    expect(
      await screen.findByText("update center exploded"),
    ).toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText(STATUS_UNAVAILABLE)).not.toBeInTheDocument();
  });

  it("lists missing update-center variables and the manual-install hint once", async () => {
    mocks.checkForUpdates.mockResolvedValue(
      updateStatus({
        center_configured: false,
        missing_configuration: [
          "MAITU_UPDATE_CENTER_URL",
          "MAITU_UPDATE_CLIENT_TOKEN",
        ],
        error:
          "Update center configuration is incomplete. Missing: " +
          "MAITU_UPDATE_CENTER_URL, MAITU_UPDATE_CLIENT_TOKEN",
        latest_version: null,
        checked_at: null,
        install_available: false,
      }),
    );
    await renderLoaded();

    await clickCheck();

    expect(await screen.findByText("更新中心未配置")).toBeInTheDocument();
    expect(screen.getByText("缺少以下环境变量：")).toBeInTheDocument();
    expect(screen.getByText("MAITU_UPDATE_CENTER_URL")).toBeInTheDocument();
    expect(screen.getByText("MAITU_UPDATE_CLIENT_TOKEN")).toBeInTheDocument();
    expect(screen.getByText(/授权激活与更新配置相互独立/)).toBeInTheDocument();
    // No heartbeat means no automatic install. The hint must point to the
    // correct installer instead of implying that configuring a queue is enough.
    expect(screen.getByText(/未检测到有效的更新器心跳/)).toBeInTheDocument();
    expect(
      screen.getByText(/scripts\/install_native_updater\.py/),
    ).toBeInTheDocument();
    expect(
      screen.queryByText(/MAITU_UPDATE_QUEUE_DIR/),
    ).not.toBeInTheDocument();
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    // The backend error repeats those names; the list replaces it instead of
    // rendering the same missing variables twice.
    expect(
      screen.queryByText(/Update center configuration is incomplete/),
    ).not.toBeInTheDocument();
    // A configuration problem is not reported as "no compatible release".
    expect(screen.queryByText(NO_RELEASE)).not.toBeInTheDocument();
    // Nor may it claim a completed check when nothing was requested.
    expect(screen.getByText("尚未检查")).toBeInTheDocument();
  });
});
