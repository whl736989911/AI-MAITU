import { act, renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { agentChatApi } from "../../../api/modules/agentChat";
import { useHitlEnabled } from "./useHitlEnabled";

describe("useHitlEnabled", () => {
  afterEach(() => vi.restoreAllMocks());

  it("reports the server's global approval setting", async () => {
    vi.spyOn(agentChatApi, "welcome").mockResolvedValue({
      hitl_enabled: false,
    });
    const { result } = renderHook(() => useHitlEnabled("agent-1"));

    await waitFor(() => expect(result.current).toBe(false));
  });

  it("stays unknown when the setting cannot be fetched", async () => {
    vi.spyOn(agentChatApi, "welcome").mockRejectedValue(new Error("offline"));
    const { result } = renderHook(() => useHitlEnabled("agent-1"));

    await waitFor(() => expect(result.current).toBeNull());
  });

  it("clears availability when no agent is selected", async () => {
    const welcome = vi.spyOn(agentChatApi, "welcome").mockResolvedValue({
      hitl_enabled: true,
    });
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string | null }) => useHitlEnabled(agentId),
      { initialProps: { agentId: "agent-1" } },
    );

    await waitFor(() => expect(result.current).toBe(true));
    await act(async () => rerender({ agentId: null }));

    expect(result.current).toBeNull();
    expect(welcome).toHaveBeenCalledTimes(1);
  });
});
