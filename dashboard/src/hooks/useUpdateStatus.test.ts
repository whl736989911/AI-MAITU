import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { act, renderHook } from "@testing-library/react";

import type { UpdateStatus } from "../api/modules/update";
import {
  UPDATE_STATUS_CHANGED_EVENT,
  UPDATE_STATUS_POLL_MS,
  clearStoredUpdateStatus,
  storeUpdateStatus,
} from "../utils/updateStatusCache";

const getUpdateStatus = vi.fn();

vi.mock("../api/modules/update", () => ({
  updateApi: {
    getUpdateStatus: (...args: unknown[]) => getUpdateStatus(...args),
  },
}));

import { useUpdateStatus } from "./useUpdateStatus";

const sample: UpdateStatus = {
  current_version: "0.9.6",
  latest_version: null,
  has_update: false,
  is_editable: false,
  service_mode: null,
  error: null,
  source: null,
  release_notes: null,
};

describe("useUpdateStatus", () => {
  beforeEach(() => {
    localStorage.clear();
    getUpdateStatus.mockReset();
    vi.useFakeTimers();
    vi.setSystemTime(new Date("2026-07-14T12:00:00Z"));
  });

  afterEach(() => {
    vi.useRealTimers();
    clearStoredUpdateStatus();
    localStorage.clear();
  });

  it("probes on mount when cache is empty", async () => {
    getUpdateStatus.mockResolvedValue(sample);
    const { result } = renderHook(() => useUpdateStatus());

    await act(async () => {
      await Promise.resolve();
    });

    expect(getUpdateStatus).toHaveBeenCalledTimes(1);
    expect(result.current.status?.current_version).toBe("0.9.6");
  });

  it("probes on mount even when the local cache is still fresh", async () => {
    storeUpdateStatus({
      ...sample,
      current_version: "0.9.0",
      latest_version: "0.9.7",
      has_update: true,
    });
    getUpdateStatus.mockResolvedValue(sample);
    const { result } = renderHook(() => useUpdateStatus());

    expect(result.current.status?.current_version).toBe("0.9.0");

    await act(async () => {
      await Promise.resolve();
    });

    expect(getUpdateStatus).toHaveBeenCalledTimes(1);
    expect(result.current.status?.current_version).toBe("0.9.6");
    expect(result.current.status?.latest_version).toBeNull();
  });

  it("re-probes after TTL via the poll interval", async () => {
    getUpdateStatus.mockResolvedValue(sample);
    renderHook(() => useUpdateStatus());

    await act(async () => {
      await Promise.resolve();
    });
    expect(getUpdateStatus).toHaveBeenCalledTimes(1);

    await act(async () => {
      // One poll tick lands at TTL; cache is expired so the probe runs again.
      vi.advanceTimersByTime(UPDATE_STATUS_POLL_MS);
      await Promise.resolve();
    });

    expect(getUpdateStatus.mock.calls.length).toBeGreaterThanOrEqual(2);
  });

  it("picks up a current-version status written by another screen", async () => {
    getUpdateStatus.mockResolvedValue(sample);
    const { result } = renderHook(() => useUpdateStatus());

    await act(async () => {
      await Promise.resolve();
    });

    await act(async () => {
      storeUpdateStatus({ ...sample, current_version: "0.9.8" });
    });

    expect(result.current.status?.current_version).toBe("0.9.8");
  });

  it("listens for the shared change event name", () => {
    expect(UPDATE_STATUS_CHANGED_EVENT).toBe("octop:update-status-changed");
  });
});
