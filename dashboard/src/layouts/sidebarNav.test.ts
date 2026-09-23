import { describe, expect, it } from "vitest";
import {
  SIDEBAR_GROUPED_NAV_KEYS,
  buildNavSections,
  isGroupedNavKey,
} from "./sidebarNav";
import type { OctopUser } from "../api/modules/auth";

const adminUser = {
  id: 1,
  username: "admin",
  role: "admin",
  permissions: ["*"],
} as OctopUser;

/** The settings-group keys a new account is pre-checked with. */
const baselinePermissions = [
  "channels",
  "connectors",
  "experts",
  "features",
  "knowledge_bases",
  "mbti",
  "skill_packages",
];

const defaultUser = {
  id: 2,
  username: "member",
  role: "user",
  permissions: baselinePermissions,
} as OctopUser;

describe("sidebarNav", () => {
  it("marks catalog keys as grouped", () => {
    for (const key of SIDEBAR_GROUPED_NAV_KEYS) {
      expect(isGroupedNavKey(key)).toBe(true);
    }
    expect(isGroupedNavKey("chat")).toBe(false);
    expect(isGroupedNavKey("experts")).toBe(false);
  });

  it("places grouped keys only under sections with groupKey", () => {
    const sections = buildNavSections(adminUser, { mobileEnabled: true });
    const flatKeys = new Set(
      sections
        .filter((s) => !s.groupKey)
        .flatMap((s) => s.items.map((i) => i.key)),
    );
    const groupedKeys = new Set(
      sections
        .filter((s) => s.groupKey)
        .flatMap((s) => s.items.map((i) => i.key)),
    );
    for (const key of groupedKeys) {
      expect(isGroupedNavKey(key)).toBe(true);
      expect(flatKeys.has(key)).toBe(false);
    }
    for (const key of flatKeys) {
      expect(isGroupedNavKey(key)).toBe(false);
    }
  });

  it("shows the two module entries to the default account", () => {
    // Both keys are baseline (design §2.2): an upgrade must not take the entries
    // away from an account nobody edited, and the two are not one entry.
    const keys = buildNavSections(defaultUser, { mobileEnabled: true }).flatMap(
      (s) => s.items.map((i) => i.key),
    );
    expect(keys).toContain("features");
    expect(keys).toContain("experts");
  });

  it("hides a module entry only when its own key is not held", () => {
    const withoutBoth = {
      ...defaultUser,
      permissions: baselinePermissions.filter(
        (key) => key !== "features" && key !== "experts",
      ),
    };
    const none = buildNavSections(withoutBoth, {
      mobileEnabled: true,
    }).flatMap((s) => s.items.map((i) => i.key));
    expect(none).not.toContain("features");
    expect(none).not.toContain("experts");
    // The rest of the bar is not collateral: only the two entries went.
    expect(none).toContain("chat");
    expect(none).toContain("tasks");

    const expertOnly = buildNavSections(
      { ...defaultUser, permissions: ["experts"] },
      { mobileEnabled: true },
    ).flatMap((s) => s.items.map((i) => i.key));
    expect(expertOnly).toContain("experts");
    expect(expertOnly).not.toContain("features");

    const featureOnly = buildNavSections(
      { ...defaultUser, permissions: ["features"] },
      { mobileEnabled: true },
    ).flatMap((s) => s.items.map((i) => i.key));
    expect(featureOnly).toContain("features");
    expect(featureOnly).not.toContain("experts");
  });

  it("shows the ACP entry to the key's holder, not only to an administrator", () => {
    // The entry answers to the `acp` module key (design §4.4), so its holder
    // gets it whatever the role; an account without the key gets neither the
    // entry nor, per the route guard, the page behind it.
    const keys = (u: OctopUser) =>
      buildNavSections(u, { mobileEnabled: true }).flatMap((s) =>
        s.items.map((i) => i.key),
      );
    expect(keys({ ...defaultUser, permissions: ["acp"] })).toContain("acp");
    expect(keys(defaultUser)).not.toContain("acp");
    expect(keys(adminUser)).toContain("acp");
  });
});
