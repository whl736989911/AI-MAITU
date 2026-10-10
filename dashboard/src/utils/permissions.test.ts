import { describe, expect, it } from "vitest";
import { canAccessPath, navAllowed, userCan, userCanAny } from "./permissions";

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
