import { describe, expect, it } from "vitest";
import {
  canAccessPath,
  navAllowed,
  personalizationTabAllowed,
  userCan,
  userCanAny,
} from "./permissions";

const restrictedAdmin = {
  role: "admin",
  permissions: ["features", "experts", "users", "teams"],
};

describe("effective permission profile precedence", () => {
  it("honors removed host capability keys for administrators", () => {
    for (const key of [
      "terminal",
      "browser",
      "desktop",
      "mobile",
      "acp",
      "plugins",
      "storage_backends",
      "envs",
    ]) {
      expect(userCan(restrictedAdmin, key)).toBe(false);
    }
    expect(userCanAny(restrictedAdmin, ["desktop", "mobile"])).toBe(false);
    expect(navAllowed(restrictedAdmin, "acp")).toBe(false);
    expect(navAllowed(restrictedAdmin, "admin-plugins")).toBe(false);
    expect(navAllowed(restrictedAdmin, "admin-storage")).toBe(false);
  });

  it("keeps business and user administration privileges for administrators", () => {
    expect(userCan(restrictedAdmin, "features")).toBe(true);
    expect(canAccessPath(restrictedAdmin, "/experts")).toBe(true);
    expect(canAccessPath(restrictedAdmin, "/admin/users")).toBe(true);
    expect(navAllowed(restrictedAdmin, "admin-users")).toBe(true);
  });

  it("allows only host capabilities retained in the effective profile", () => {
    const profile = { ...restrictedAdmin, permissions: ["terminal"] };
    expect(userCan(profile, "terminal")).toBe(true);
    expect(userCan(profile, "browser")).toBe(false);
  });
});

describe("personalization module grants", () => {
  it.each([
    ["skills", "skills"],
    ["subagents", "subagents"],
    ["tools", "tools"],
    ["plugins", "agent_plugins"],
    ["mbti", "mbti"],
    ["memory", "memory"],
  ])("gates %s independently on %s", (tab, key) => {
    const user = { role: "user", permissions: [key] };
    expect(personalizationTabAllowed(user, tab)).toBe(true);
    expect(
      personalizationTabAllowed({ role: "user", permissions: [] }, tab),
    ).toBe(false);
    expect(canAccessPath(user, `/personalization/${tab}`)).toBe(true);
    expect(
      canAccessPath(
        { role: "user", permissions: [] },
        `/personalization/${tab}`,
      ),
    ).toBe(false);
  });

  it("shows the personalization navigation when any independent tab is granted", () => {
    expect(
      navAllowed({ role: "user", permissions: ["memory"] }, "personalization"),
    ).toBe(true);
    expect(
      navAllowed({ role: "user", permissions: [] }, "personalization"),
    ).toBe(false);
  });
});
