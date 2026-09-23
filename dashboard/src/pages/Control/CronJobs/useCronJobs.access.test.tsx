import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopRole } from "../../../api/modules/auth";

const held = vi.hoisted(() => ({
  agent: null as { is_owner?: boolean } | null,
  agentId: null as string | null,
  role: null as OctopRole | null,
  list: vi.fn(),
  settings: vi.fn(),
  translate: (key: string) => key,
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ activeAgentId: held.agentId, activeAgent: held.agent }),
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => (held.role ? { role: held.role } : null),
}));
vi.mock("../../../api/modules/cronjob", () => ({
  octopCronApi: {
    list: held.list,
    settings: held.settings,
  },
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: held.translate }),
}));

import { useCronJobs } from "./useCronJobs";

describe("useCronJobs management access", () => {
  beforeEach(() => {
    held.agent = { is_owner: false };
    held.agentId = "someone-elses-expert";
    held.role = "user";
    held.list.mockReset().mockResolvedValue([]);
    held.settings.mockReset().mockResolvedValue({ timezone: "UTC" });
  });

  it("loads another owner's jobs for a system administrator", async () => {
    held.role = "admin";

    renderHook(() => useCronJobs());

    await waitFor(() => {
      expect(held.list).toHaveBeenCalledWith("someone-elses-expert");
    });
  });

  it("does not load another owner's jobs for a non-admin", async () => {
    renderHook(() => useCronJobs());

    await act(async () => {
      await Promise.resolve();
    });
    expect(held.list).not.toHaveBeenCalled();
  });

  it("keeps the existing no-agent behavior", async () => {
    held.agent = null;
    held.agentId = null;
    held.role = "admin";

    renderHook(() => useCronJobs());

    await act(async () => {
      await Promise.resolve();
    });
    expect(held.list).not.toHaveBeenCalled();
  });
});
