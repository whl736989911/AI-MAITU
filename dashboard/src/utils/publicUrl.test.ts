import { describe, expect, it } from "vitest";
import {
  appPathnameForBase,
  publicUrlForBase,
  routerBasenameForBase,
} from "./publicUrl";
describe("public URL base paths", () => {
  it("preserves root deployment URLs", () => {
    expect(publicUrlForBase("/brand/logo.png", "/")).toBe("/brand/logo.png");
    expect(routerBasenameForBase("/")).toBeUndefined();
    expect(appPathnameForBase("/chat", "/")).toBe("/chat");
    expect(appPathnameForBase("/api/branding/assets/logo.png", "/")).toBe(
      "/api/branding/assets/logo.png",
    );
  });

  it("prefixes app resources once under a subpath", () => {
    expect(publicUrlForBase("/brand/logo.png", "/maitu/")).toBe(
      "/maitu/brand/logo.png",
    );
    expect(publicUrlForBase("sw.js", "/maitu/")).toBe("/maitu/sw.js");
    expect(publicUrlForBase("/maitu/brand/logo.png", "/maitu/")).toBe(
      "/maitu/brand/logo.png",
    );
    expect(routerBasenameForBase("/maitu/")).toBe("/maitu");
  });

  it("normalizes subpath browser route comparisons", () => {
    expect(appPathnameForBase("/maitu/login", "/maitu/")).toBe("/login");
    expect(appPathnameForBase("/maitu", "/maitu/")).toBe("/");
    expect(appPathnameForBase("/maitux/login", "/maitu/")).toBe(
      "/maitux/login",
    );
  });
});
