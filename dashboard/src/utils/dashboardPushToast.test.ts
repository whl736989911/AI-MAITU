import { beforeEach, describe, expect, it } from "vitest";
import {
  claimSystemPush,
  clearSystemPushClaims,
  parseDashboardPushFrame,
  readSystemNotifyPermission,
  shouldShowSystemPush,
  truncatePushText,
} from "./dashboardPushToast";

describe("parseDashboardPushFrame", () => {
  it("accepts a dashboard_push frame", () => {
    expect(
      parseDashboardPushFrame({
        type: "dashboard_push",
        agent_id: "a1",
        thread_id: "thr_1",
        text: "记得喝水",
        agent_name: "助手",
      }),
    ).toEqual({
      type: "dashboard_push",
      agent_id: "a1",
      thread_id: "thr_1",
      text: "记得喝水",
      agent_name: "助手",
    });
  });

  it("rejects other frame types and incomplete payloads", () => {
    expect(
      parseDashboardPushFrame({ type: "token", content: "hi" }),
    ).toBeNull();
    expect(
      parseDashboardPushFrame({
        type: "dashboard_push",
        agent_id: "a1",
        text: "x",
      }),
    ).toBeNull();
    expect(parseDashboardPushFrame(null)).toBeNull();
  });
});

describe("truncatePushText", () => {
  it("keeps short text intact", () => {
    expect(truncatePushText("记得喝水")).toBe("记得喝水");
  });

  it("truncates long text with an ellipsis", () => {
    const long = "a".repeat(300);
    const out = truncatePushText(long, 80);
    expect(out.length).toBe(81);
    expect(out.endsWith("…")).toBe(true);
  });
});

describe("system notification permission", () => {
  it("requires a secure context and already granted browser permission", () => {
    expect(readSystemNotifyPermission(false, { permission: "granted" })).toBe(
      "unsupported",
    );
    expect(readSystemNotifyPermission(true, undefined)).toBe("unsupported");
    expect(readSystemNotifyPermission(true, { permission: "default" })).toBe(
      "default",
    );
    expect(readSystemNotifyPermission(true, { permission: "denied" })).toBe(
      "denied",
    );
    expect(readSystemNotifyPermission(true, { permission: "granted" })).toBe(
      "granted",
    );
  });

  it("only delivers a system notification for a hidden tab with granted access", () => {
    const hiddenTab = { visible: false };
    const foregroundTab = { visible: true };
    expect(shouldShowSystemPush("granted", hiddenTab.visible)).toBe(true);
    expect(shouldShowSystemPush("granted", foregroundTab.visible)).toBe(false);
    expect(shouldShowSystemPush("default", hiddenTab.visible)).toBe(false);
    expect(shouldShowSystemPush("denied", hiddenTab.visible)).toBe(false);
    expect(shouldShowSystemPush("unsupported", hiddenTab.visible)).toBe(false);
  });
});

describe("cross-tab push claim", () => {
  beforeEach(() => localStorage.clear());

  it("allows one tab to claim a push and suppresses duplicate delivery", () => {
    expect(
      claimSystemPush(localStorage, "thread\\nreminder", "tab-a", 10_000),
    ).toBe(true);
    expect(
      claimSystemPush(localStorage, "thread\\nreminder", "tab-b", 10_001),
    ).toBe(false);
  });

  it("expires claims at the TTL boundary and keeps unrelated pushes independent", () => {
    expect(
      claimSystemPush(localStorage, "thread\\nfirst", "tab-a", 10_000),
    ).toBe(true);
    expect(
      claimSystemPush(localStorage, "thread\\nsecond", "tab-b", 10_001),
    ).toBe(true);
    expect(
      claimSystemPush(localStorage, "thread\\nfirst", "tab-b", 12_000),
    ).toBe(true);
  });

  it("does not deliver when cross-tab storage is unavailable", () => {
    expect(claimSystemPush(null, "thread\\nreminder", "tab-a", 10_000)).toBe(
      false,
    );
  });
});

describe("identity-scoped push state", () => {
  it("clears push claims without clearing unrelated local storage", () => {
    localStorage.clear();
    expect(
      claimSystemPush(localStorage, "thread\\nreminder", "tab-a", 10_000),
    ).toBe(true);
    localStorage.setItem("dashboard:preference", "dark");

    clearSystemPushClaims(localStorage);

    expect(localStorage.length).toBe(1);
    expect(localStorage.getItem("dashboard:preference")).toBe("dark");
  });
});
