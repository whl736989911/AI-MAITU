import { describe, expect, it } from "vitest";
import { buildNavSections, findWorkspaceItem } from "./sidebarNav";
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
      buildNavSections(u, { mobileEnabled: true }).flatMap((section) =>
        section.items
          .flatMap((item) => [item, ...(item.children ?? [])])
          .map((item) => item.key),
      );
    expect(keys({ ...defaultUser, permissions: ["acp"] })).toContain("acp");
    expect(keys(defaultUser)).not.toContain("acp");
    expect(keys(adminUser)).toContain("acp");
  });

  it("does not expose independent resource settings navigation", () => {
    const keys = buildNavSections({
      ...defaultUser,
      permissions: ["skills", "channels"],
    }).flatMap((section) =>
      section.items
        .flatMap((item) => [item, ...(item.children ?? [])])
        .map((item) => item.key),
    );
    expect(keys).not.toContain("personalization");
    expect(keys).not.toContain("channels");
  });

  it("offers identity configuration to SSO delegates without offering user administration", () => {
    const sections = buildNavSections({ ...defaultUser, permissions: ["sso"] });
    const items = sections.flatMap((section) => section.items);
    expect(items.some((item) => item.key === "admin-users")).toBe(false);
    expect(
      items
        .find((item) => item.key === "system")
        ?.children?.some((item) => item.key === "identity"),
    ).toBe(true);
    expect(findWorkspaceItem(sections, "admin-users", "?tab=oidc")?.key).toBe(
      "system",
    );
  });

  it("does not turn a model grant into storage or host access", () => {
    const items = buildNavSections({
      ...defaultUser,
      permissions: ["providers"],
    }).flatMap((section) => section.items);
    expect(items.some((item) => item.key === "execution")).toBe(false);
    const settings =
      items.find((item) => item.key === "system")?.children ?? [];
    expect(settings.some((item) => item.key === "models")).toBe(true);
    expect(
      settings.some(
        (item) => item.key === "admin-storage" || item.key === "admin-plugins",
      ),
    ).toBe(false);
  });

  it("does not implicitly enable host tools for administrators", () => {
    const sections = buildNavSections(
      { ...adminUser, permissions: [] },
      { mobileEnabled: true },
    );
    expect(
      sections
        .flatMap((section) => section.items)
        .some((item) => item.key === "execution"),
    ).toBe(false);
  });
});
