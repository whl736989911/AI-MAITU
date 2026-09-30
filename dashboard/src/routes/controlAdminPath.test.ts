import { describe, expect, it } from "vitest";
import type { OctopUser } from "../api/modules/auth";
import { buildNavSections } from "../layouts/sidebarNav";
import {
  ADVANCED_TAB_PERMISSIONS,
  canAccessPath,
  NAV_PERMISSIONS,
  navAllowed,
  pathPermissionKeys,
  PERM,
} from "../utils/permissions";
import { isWorkbenchPath, resolveSelectedKey, routeConfigs } from "./index";

describe("pathPermissionKeys", () => {
  it("matches workbench and legacy aliases", () => {
    expect(isWorkbenchPath("/workbench")).toBe(true);
    expect(pathPermissionKeys("/workbench")).toEqual([...PERM.workbench]);
    expect(pathPermissionKeys("/workbench/terminal")).toEqual([
      ...PERM.terminal,
    ]);
    expect(pathPermissionKeys("/workbench/browser")).toEqual([...PERM.browser]);
    expect(pathPermissionKeys("/terminal")).toEqual([...PERM.terminal]);
    expect(pathPermissionKeys("/remote-browser")).toEqual([...PERM.browser]);
  });

  it("matches remote desktop hub tabs and acp", () => {
    expect(pathPermissionKeys("/remote-desktop")).toEqual([
      "desktop",
      "mobile",
    ]);
    expect(pathPermissionKeys("/remote-desktop/desktop")).toEqual([
      ...PERM.desktop,
    ]);
    expect(pathPermissionKeys("/remote-desktop/phone")).toEqual([
      ...PERM.mobile,
    ]);
    expect(pathPermissionKeys("/remote-phone")).toEqual([...PERM.mobile]);
    // ACP is a module surface: the same key the nav entry reads (design §4.4).
    expect(pathPermissionKeys("/acp")).toEqual([...PERM.acp]);
    expect(NAV_PERMISSIONS.acp).toEqual(PERM.acp);
  });

  it("keeps sso on users page, not advanced", () => {
    expect(pathPermissionKeys("/admin/users")).toEqual([...PERM.usersPage]);
    expect(pathPermissionKeys("/admin/advanced")).toEqual([
      ...PERM.advancedPage,
    ]);
    expect([...PERM.advancedPage]).not.toContain("sso");
    expect(NAV_PERMISSIONS["admin-users"]).toEqual(PERM.usersPage);
    expect(NAV_PERMISSIONS["admin-advanced"]).toEqual(PERM.advancedPage);
    expect(ADVANCED_TAB_PERMISSIONS.captcha).toBe("captcha");
    expect([...PERM.advancedPage]).toContain("captcha");
  });

  it("keeps voice and search on models page, not advanced", () => {
    expect(pathPermissionKeys("/admin/models")).toEqual([...PERM.modelsPage]);
    expect(pathPermissionKeys("/admin/voice")).toEqual([...PERM.modelsPage]);
    expect([...PERM.modelsPage]).toContain("voice");
    expect([...PERM.modelsPage]).toContain("search");
    expect([...PERM.advancedPage]).not.toContain("voice");
    expect([...PERM.advancedPage]).not.toContain("search");
    expect(NAV_PERMISSIONS.models).toEqual(PERM.modelsPage);
  });

  it("does not gate common pages", () => {
    expect(pathPermissionKeys("/chat")).toBeNull();
    expect(pathPermissionKeys("/tasks")).toBeNull();
    expect(pathPermissionKeys("/token-usage")).toBeNull();
    expect(pathPermissionKeys("/personalization/skills")).toBeNull();
  });

  it("gates experts and teams independently, with experts required by teams", () => {
    expect(pathPermissionKeys("/features")).toEqual([...PERM.features]);
    expect(pathPermissionKeys("/experts")).toEqual([...PERM.experts]);
    expect(pathPermissionKeys("/teams")).toEqual([...PERM.teams]);

    expect(
      canAccessPath({ role: "user", permissions: ["experts"] }, "/teams"),
    ).toBe(false);
    expect(
      canAccessPath({ role: "user", permissions: ["teams"] }, "/teams"),
    ).toBe(false);
    expect(
      canAccessPath(
        { role: "user", permissions: ["teams", "experts"] },
        "/teams",
      ),
    ).toBe(true);
    expect(
      canAccessPath(
        { role: "user", permissions: ["teams", "experts"] },
        "/teams/alpha",
      ),
    ).toBe(true);
    expect(
      canAccessPath({ role: "user", permissions: ["experts"] }, "/experts"),
    ).toBe(true);
    expect(
      canAccessPath({ role: "user", permissions: ["features"] }, "/teams"),
    ).toBe(false);
    expect(
      canAccessPath({ role: "user", permissions: ["features"] }, "/experts"),
    ).toBe(false);
    expect(
      canAccessPath({ role: "user", permissions: ["features"] }, "/features"),
    ).toBe(true);
    expect(
      canAccessPath({ role: "user", permissions: [] }, "/features/feat-1"),
    ).toBe(false);
    expect(
      canAccessPath({ role: "user", permissions: [] }, "/experts/any-expert"),
    ).toBe(false);
    expect(NAV_PERMISSIONS.teams).toEqual([...PERM.teams]);
  });

  it("shows Teams only when both Teams and Experts are available", () => {
    const makeUser = (permissions: string[]): OctopUser => ({
      id: 1,
      username: "test",
      role: "user",
      display_name: null,
      locale: "en",
      permissions,
    });
    const containsTeams = (permissions: string[]) =>
      buildNavSections(makeUser(permissions))
        .flatMap((section) => section.items)
        .some((item) => item.key === "teams");

    expect(navAllowed(makeUser(["experts"]), "experts")).toBe(true);
    expect(containsTeams(["experts"])).toBe(false);
    expect(containsTeams(["teams"])).toBe(false);
    expect(containsTeams(["teams", "experts"])).toBe(true);
  });

  it("gates settings modules", () => {
    expect(pathPermissionKeys("/connectors")).toEqual([...PERM.connectors]);
    expect(pathPermissionKeys("/skill-packages")).toEqual([
      ...PERM.skillPackages,
    ]);
    expect(pathPermissionKeys("/personalization/channels")).toEqual([
      ...PERM.channels,
    ]);
    expect(pathPermissionKeys("/knowledge-bases")).toEqual([
      ...PERM.knowledgeBasesPage,
    ]);
    expect([...PERM.advancedPage]).not.toContain("knowledge_settings");
  });

  it("canAccessPath respects holder permissions", () => {
    const user = { role: "user", permissions: ["desktop"] };
    expect(canAccessPath(user, "/remote-desktop")).toBe(true);
    expect(canAccessPath(user, "/remote-desktop/desktop")).toBe(true);
    expect(canAccessPath(user, "/remote-desktop/phone")).toBe(false);
    expect(
      canAccessPath(
        { role: "user", permissions: ["mobile"] },
        "/remote-desktop/phone",
      ),
    ).toBe(true);
    expect(canAccessPath(user, "/admin/users")).toBe(false);
    expect(canAccessPath({ role: "admin", permissions: [] }, "/acp")).toBe(
      true,
    );
    // A non-administrator holding the key passes the same guard. What stays
    // administrator-only is the runner *definition* write inside the panel —
    // a role-only gate, so it is not expressed here.
    expect(canAccessPath({ role: "user", permissions: ["acp"] }, "/acp")).toBe(
      true,
    );
    expect(
      canAccessPath({ role: "user", permissions: ["terminal"] }, "/acp"),
    ).toBe(false);
    expect(
      canAccessPath(
        { role: "user", permissions: ["knowledge_bases"] },
        "/knowledge-bases",
      ),
    ).toBe(true);
    expect(
      canAccessPath(
        { role: "user", permissions: ["knowledge_settings"] },
        "/knowledge-bases",
      ),
    ).toBe(true);
    expect(
      canAccessPath(
        { role: "user", permissions: ["knowledge_bases"] },
        "/admin/advanced",
      ),
    ).toBe(false);
  });

  it("does not give unit_admin a full bypass", () => {
    const bare = { role: "unit_admin", permissions: [] };
    expect(canAccessPath(bare, "/admin/users")).toBe(false);
    expect(canAccessPath(bare, "/acp")).toBe(false);
    // No bypass, but a granted key is honored — the role is not the gate.
    expect(
      canAccessPath({ role: "unit_admin", permissions: ["acp"] }, "/acp"),
    ).toBe(true);
    expect(
      canAccessPath(
        { role: "unit_admin", permissions: ["users"] },
        "/admin/users",
      ),
    ).toBe(true);
    expect(
      canAccessPath(
        { role: "unit_admin", permissions: ["browser"] },
        "/workbench/browser",
      ),
    ).toBe(true);
  });

  it("treats an explicit wildcard grant as every module key, not as the admin role", () => {
    const wildcard = { role: "user", permissions: ["*"] };
    // Module gates: ``*`` stands for the whole catalog — ACP's entry included,
    // now that it answers to a module key rather than to the role.
    expect(canAccessPath(wildcard, "/admin/users")).toBe(true);
    expect(canAccessPath(wildcard, "/workbench/browser")).toBe(true);
    expect(canAccessPath(wildcard, "/acp")).toBe(true);
    // Role-only gates: the backend's ``require_admin`` never reads
    // ``permissions``, so a ``*`` grant must not open them. The ``/admin/*``
    // fallback — a path no module key owns — is the one left.
    expect(pathPermissionKeys("/admin/unmapped-section")).toBe("admin");
    expect(canAccessPath(wildcard, "/admin/unmapped-section")).toBe(false);
    expect(canAccessPath({ role: "user", permissions: [] }, "/acp")).toBe(
      false,
    );
  });
});

describe("unknown dashboard paths", () => {
  it("do not highlight a sidebar item", () => {
    expect(resolveSelectedKey("/does-not-exist")).toBe("");
  });

  it("are caught by the not-found route", () => {
    expect(routeConfigs.some((rc) => rc.path === "*")).toBe(true);
  });
});

describe("personalization nav key", () => {
  it("comes from the section prefix, tabs listed in the map or not", () => {
    // ``/personalization/files`` is a feature's tab; the experts' page has no
    // such tab, and the path still highlights the section it is under.
    expect(resolveSelectedKey("/personalization/files")).toBe(
      "personalization",
    );
    expect(resolveSelectedKey("/personalization/skills")).toBe(
      "personalization",
    );
  });
});
