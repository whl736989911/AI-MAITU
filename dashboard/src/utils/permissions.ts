/** Client-side helpers mirroring backend ``user_has_permission``. */

export type PermissionHolder = {
  /** ``admin`` bypasses every gate; ``unit_admin`` does not (its scope is the unit). */
  role: "admin" | "unit_admin" | "user" | string;
  permissions?: string[] | null;
};

/** Wildcard key: an explicit grant of every module key. */
export const ALL_PERMISSIONS_KEY = "*";

/** Any-of module keys, or ``"admin"`` for role-only. */
export type PermissionKeys = readonly string[] | "admin";

export const PERM = {
  /** The functional modules of design §2.2. Baseline keys: every signed-in
   *  account holds them until an administrator edits it, so hiding a nav entry
   *  or refusing a route here only ever affects an explicit deny. */
  mbti: ["mbti"],
  experts: ["experts"],
  features: ["features"],
  channels: ["channels"],
  connectors: ["connectors"],
  skillPackages: ["skill_packages"],
  knowledgeBases: ["knowledge_bases"],
  knowledgeSettings: ["knowledge_settings"],
  knowledgeBasesPage: ["knowledge_bases", "knowledge_settings"],
  workbench: ["browser", "terminal"],
  browser: ["browser"],
  terminal: ["terminal"],
  desktop: ["desktop"],
  mobile: ["mobile"],
  /** ACP: the key gates the entry, the route and the runner *list*; the global
   *  runner definitions are a system-administrator write on top of it (§4.4). */
  acp: ["acp"],
  usersPage: ["users", "sso"],
  /** Org-unit directory: the backend mounts ``/api/org-units`` on ``users``. */
  orgUnits: ["users"],
  modelsPage: ["providers", "ollama_models", "onnx_models", "voice", "search"],
  storage: ["storage_backends"],
  plugins: ["plugins"],
  securityPage: ["security", "admin_console"],
  advancedPage: ["update", "envs", "tls", "observability", "backup", "captcha"],
} as const satisfies Record<string, readonly string[]>;

/** Sidebar item key → permission keys. Shared with path guards. */
export const NAV_PERMISSIONS = {
  features: PERM.features,
  experts: PERM.experts,
  channels: PERM.channels,
  connectors: PERM.connectors,
  "skill-packages": PERM.skillPackages,
  "knowledge-bases": PERM.knowledgeBasesPage,
  workbench: PERM.workbench,
  "remote-desktop": ["desktop", "mobile"],
  "remote-phone": PERM.mobile,
  acp: PERM.acp,
  "admin-users": PERM.usersPage,
  "admin-org-units": PERM.orgUnits,
  models: PERM.modelsPage,
  "admin-storage": PERM.storage,
  "admin-plugins": PERM.plugins,
  "admin-security": PERM.securityPage,
  "admin-advanced": PERM.advancedPage,
} as const satisfies Record<string, PermissionKeys>;

export type NavPermissionKey = keyof typeof NAV_PERMISSIONS;

/**
 * The personalization tabs a module key gates (design §2.4: an unauthorized
 * tab is not shown). A tab absent from this table is gated by nothing here —
 * the expert/feature capability rule (`agents.kind`) decides who may write it,
 * which is a different question and stays where it is.
 *
 * `channels` was the first of these and used to be filtered inline in the
 * page; the table is what the users', advanced and security pages already use
 * for the same job, so it is the one place to read.
 */
export const PERSONALIZATION_TAB_PERMISSIONS = {
  channels: PERM.channels,
  plugins: PERM.plugins,
  mbti: PERM.mbti,
} as const satisfies Record<string, readonly string[]>;

/** True when the module keys attached to personalization tab `tab` allow it. */
export function personalizationTabAllowed(
  user: PermissionHolder | null | undefined,
  tab: string,
): boolean {
  const keys = (
    PERSONALIZATION_TAB_PERMISSIONS as Record<string, readonly string[]>
  )[tab];
  return keys === undefined || userCanKey(user, keys);
}

export const USERS_TAB_PERMISSIONS = {
  local: "users",
  feishu: "sso",
  wecom: "sso",
  dingtalk: "sso",
  oidc: "sso",
} as const;

export const ADVANCED_TAB_PERMISSIONS = {
  "env-vars": "envs",
  observability: "observability",
  backup: "backup",
  https: "tls",
  updates: "update",
  captcha: "captcha",
  branding: "admin",
} as const;

export const SECURITY_TAB_PERMISSIONS = {
  hitl: "security",
  filesystem: "security",
  pii: "security",
  tool_guard: "security",
  skill_scan: "security",
  audit: "admin_console",
} as const;

/**
 * True when the holder has the ``admin`` role itself. Mirrors the backend
 * ``require_admin`` gate (role only — a ``*`` grant does not qualify) and is
 * the only predicate a role-only gate may use.
 */
export function isSystemAdmin(
  user: PermissionHolder | null | undefined,
): boolean {
  return user?.role === "admin";
}

/** True when the user may access the module ``key`` (admin bypasses). */
export function userCan(
  user: PermissionHolder | null | undefined,
  key: string,
): boolean {
  if (!user) return false;
  if (isSystemAdmin(user)) return true;
  const held = user.permissions ?? [];
  return held.includes(key) || held.includes(ALL_PERMISSIONS_KEY);
}

/** True when the user holds any of the given keys (admin bypasses). */
export function userCanAny(
  user: PermissionHolder | null | undefined,
  keys: readonly string[],
): boolean {
  if (!user) return false;
  if (isSystemAdmin(user)) return true;
  const held = new Set(user.permissions ?? []);
  return held.has(ALL_PERMISSIONS_KEY) || keys.some((k) => held.has(k));
}

export function canAccessKeys(
  user: PermissionHolder | null | undefined,
  keys: PermissionKeys,
): boolean {
  if (keys === "admin") return isSystemAdmin(user);
  return userCanAny(user, keys);
}

export function navAllowed(
  user: PermissionHolder | null | undefined,
  navKey: NavPermissionKey,
): boolean {
  return canAccessKeys(user, NAV_PERMISSIONS[navKey]);
}

/**
 * The channel types out of ``kinds`` this holder may use, in the order given.
 *
 * Design §2.3 makes ``channel_<kind>`` (the key the backend catalog derives from
 * the gateway's own kind list) the unit of channel authorization, and §5.2/§8
 * make the type selector show the authorized types only. Exported because the
 * grid, the create drawer's kind and the row count must read one array rather
 * than each filtering for itself — and because a page that lists a type the
 * backend will refuse is the inconsistency §5.1 forbids.
 */
export function allowedChannelKinds<T extends string>(
  user: PermissionHolder | null | undefined,
  kinds: readonly T[],
): T[] {
  return kinds.filter((kind) => userCan(user, `channel_${kind}`));
}

export function userCanKey(
  user: PermissionHolder | null | undefined,
  key: string | readonly string[],
): boolean {
  return typeof key === "string" ? userCan(user, key) : userCanAny(user, key);
}

/**
 * Permissions that unlock a dashboard path (any-of).
 * ``"admin"`` means role===admin only — the ``/admin/*`` fallback, for paths
 * without a module key. ``null`` means no special gate.
 */
export function pathPermissionKeys(pathname: string): PermissionKeys | null {
  // The two module surfaces of design §5.2. The nav entry and the route read
  // the same key, so "hidden" and "refused" cannot disagree.
  if (pathname === "/features" || pathname.startsWith("/features/")) {
    return PERM.features;
  }
  if (pathname === "/experts" || pathname.startsWith("/experts/")) {
    return PERM.experts;
  }
  if (pathname.startsWith("/admin/users") || pathname === "/admin/sso") {
    return PERM.usersPage;
  }
  if (
    pathname.startsWith("/admin/models") ||
    pathname === "/models" ||
    pathname.startsWith("/admin/voice")
  ) {
    return PERM.modelsPage;
  }
  if (pathname.startsWith("/admin/backend")) {
    return PERM.storage;
  }
  if (
    pathname.startsWith("/admin/plugins") ||
    pathname === "/plugins" ||
    pathname.startsWith("/plugins/")
  ) {
    return PERM.plugins;
  }
  if (
    pathname.startsWith("/admin/security") ||
    pathname.startsWith("/admin/audit")
  ) {
    return PERM.securityPage;
  }
  if (
    pathname.startsWith("/admin/advanced") ||
    pathname.startsWith("/admin/updates")
  ) {
    return PERM.advancedPage;
  }
  // Org-unit directory: reading it is part of the users module (the backend
  // mounts ``/api/org-units`` on "users"), writes are admin-gated server-side.
  if (pathname.startsWith("/admin/org-units")) {
    return PERM.orgUnits;
  }
  if (pathname.startsWith("/admin/")) {
    return "admin";
  }
  if (
    pathname === "/personalization/channels" ||
    pathname === "/channels" ||
    pathname.startsWith("/personalization/channels/")
  ) {
    return PERM.channels;
  }
  if (pathname === "/connectors" || pathname.startsWith("/connectors/")) {
    return PERM.connectors;
  }
  if (
    pathname === "/skill-packages" ||
    pathname.startsWith("/skill-packages/")
  ) {
    return PERM.skillPackages;
  }
  if (
    pathname === "/knowledge-bases" ||
    pathname.startsWith("/knowledge-bases/")
  ) {
    return PERM.knowledgeBasesPage;
  }
  if (pathname === "/remote-desktop/desktop") {
    return PERM.desktop;
  }
  if (
    pathname === "/remote-desktop/phone" ||
    pathname === "/remote-phone" ||
    pathname.startsWith("/remote-phone/") ||
    pathname === "/remote-android" ||
    pathname.startsWith("/remote-android/")
  ) {
    return PERM.mobile;
  }
  if (
    pathname === "/remote-desktop" ||
    pathname.startsWith("/remote-desktop/")
  ) {
    return ["desktop", "mobile"];
  }
  if (pathname === "/workbench/terminal" || pathname === "/terminal") {
    return PERM.terminal;
  }
  if (pathname === "/workbench/browser" || pathname === "/remote-browser") {
    return PERM.browser;
  }
  if (pathname === "/workbench" || pathname.startsWith("/workbench/")) {
    return PERM.workbench;
  }
  // ACP: the nav entry and this route read the same module key. The global
  // runner definitions inside are still a system-administrator write.
  if (pathname === "/acp" || pathname.startsWith("/acp/")) {
    return PERM.acp;
  }
  return null;
}

/** True when this route config path should be wrapped in RequirePermission. */
export function routeNeedsPermission(routePath: string): boolean {
  const probe = routePath.replace(/\/\*$/, "").replace(/\/:[^/]+/g, "");
  if (pathPermissionKeys(probe) !== null) return true;
  if (probe === "/personalization" || probe.startsWith("/personalization/")) {
    return true;
  }
  if (probe === "/workbench" || probe.startsWith("/workbench/")) return true;
  if (probe.startsWith("/admin")) return true;
  return false;
}

export function canAccessPath(
  user: PermissionHolder | null | undefined,
  pathname: string,
): boolean {
  const req = pathPermissionKeys(pathname);
  if (req === null) return true;
  return canAccessKeys(user, req);
}
