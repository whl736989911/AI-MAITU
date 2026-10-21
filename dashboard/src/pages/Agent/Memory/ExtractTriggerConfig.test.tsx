import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent, {
  PointerEventsCheckLevel,
} from "@testing-library/user-event";

vi.mock("../../../api/modules/memoryDashboard", () => ({
  memoryDashboardApi: {
    getExtractConfig: vi.fn(),
    putExtractConfig: vi.fn(),
  },
}));

vi.mock("../../../api/modules/provider", () => ({
  providerApi: {
    listResolvedModels: vi.fn(),
  },
}));

import { memoryDashboardApi } from "../../../api/modules/memoryDashboard";
import { providerApi } from "../../../api/modules/provider";
import MemorySettings from "./MemorySettings";

const api = vi.mocked(memoryDashboardApi, true);
const providers = vi.mocked(providerApi, true);

const idleConfig = {
  memory_enabled: true,
  extract_on_session_end: true,
  extract_trigger_mode: "idle" as const,
  extract_idle_seconds: 300,
  extract_interval_seconds: 21600,
  extract_light_timeout_s: 30,
  extract_heavy_timeout_s: 45,
  extract_max_tokens: 512,
  extract_extra_body: { metadata: { purpose: "memory" } },
};

beforeEach(() => {
  vi.clearAllMocks();
  providers.listResolvedModels.mockResolvedValue([]);
});

describe("<MemorySettings />", () => {
  it.each([
    {
      mode: "idle" as const,
      label: "对话空闲",
      value: "7",
      expected: { extract_idle_seconds: 420 },
    },
    {
      mode: "interval" as const,
      label: "每隔",
      value: "2",
      expected: { extract_interval_seconds: 7200 },
    },
  ])(
    "replaces and serializes the $mode timing without inserting its minimum",
    async ({ mode, label, value, expected }) => {
      const config = { ...idleConfig, extract_trigger_mode: mode };
      api.getExtractConfig.mockResolvedValue(config);
      api.putExtractConfig.mockResolvedValue({ ...config, ...expected });
      const user = userEvent.setup({
        pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
        delay: null,
      });
      render(<MemorySettings agentId="ZYWZTD" />);
      await screen.findByText("存储记忆");
      const input = screen.getByLabelText(label);
      expect(input).toBeVisible();
      await user.clear(input);
      expect(input).toHaveValue("");
      await user.type(input, value);
      expect(input).toHaveValue(value);
      const save = screen.getByText("保存设置");
      expect(save).toBeVisible();
      await user.click(save);
      await waitFor(() => {
        expect(api.putExtractConfig).toHaveBeenCalledWith(
          "ZYWZTD",
          expect.objectContaining({
            ...expected,
            extract_light_timeout_s: 30,
            extract_heavy_timeout_s: 45,
            extract_max_tokens: 512,
            extract_extra_body: { metadata: { purpose: "memory" } },
          }),
        );
      });
    },
  );

  it("normalizes an invalid zero idle time to one minute", async () => {
    api.getExtractConfig.mockResolvedValue({
      ...idleConfig,
      extract_idle_seconds: 0,
    });
    api.putExtractConfig.mockResolvedValue({
      ...idleConfig,
      extract_idle_seconds: 60,
    });
    const user = userEvent.setup({
      pointerEventsCheck: PointerEventsCheckLevel.EachTarget,
      delay: null,
    });
    render(<MemorySettings agentId="ZYWZTD" />);

    await screen.findByText("存储记忆");
    expect(screen.getByLabelText("对话空闲")).toHaveValue("1");
    const save = screen.getByText("保存设置");
    expect(save).toBeVisible();
    await user.click(save);
    await waitFor(() =>
      expect(api.putExtractConfig).toHaveBeenCalledWith(
        "ZYWZTD",
        expect.objectContaining({ extract_idle_seconds: 60 }),
      ),
    );
  });
});
