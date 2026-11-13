import type { ReactNode } from "react";
import {
  Monitor,
  MessageSquareText,
  Timer,
  SlidersHorizontal,
  Link2,
  Database,
  Cpu,
  Users as UsersIcon,
  Activity,
  Share2,
  Puzzle,
  Package,
  HardDrive,
  GraduationCap,
  LayoutGrid,
  Network,
  Shield,
  PanelsTopLeft,
  ShieldCheck,
  Boxes,
  Settings2,
} from "lucide-react";
import type { OctopUser } from "../api/modules/auth";
import { navAllowed, userCan } from "../utils/permissions";

export const COLLAPSED_WIDTH = 56;

const iconSize = 15;
const iconStroke = 1.8;

export interface NavItem {
  key: string;
  path: string;
  icon: ReactNode;
  labelKey: string;
  badge?: string;
  /** Authorized destinations inside a workspace. Each retains its own route guard. */
  children?: NavItem[];
}

export interface NavSection {
  /** When omitted, items render flat without a group header. */
  groupKey?: string;
  items: NavItem[];
}

function buildModuleNavSections(
  user: OctopUser | null,
  opts?: { mobileEnabled?: boolean },
): NavSection[] {
  const sections: NavSection[] = [];

  const primaryItems: NavItem[] = [
    {
      key: "chat",
      path: "/chat",
      icon: <MessageSquareText size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.chat",
    },
  ];
  // Module visibility follows the same keys as route guards. Teams is
  // independently gated and additionally requires Experts.
  if (navAllowed(user, "features")) {
    primaryItems.push({
      key: "features",
      path: "/features",
      icon: <LayoutGrid size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.features",
    });
  }
  if (navAllowed(user, "experts")) {
    primaryItems.push({
      key: "experts",
      path: "/experts",
      icon: <GraduationCap size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.experts",
    });
  }
  if (navAllowed(user, "teams")) {
    primaryItems.push({
      key: "teams",
      path: "/teams",
      icon: <Network size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.teams",
    });
  }
  primaryItems.push({
    key: "tasks",
    path: "/tasks",
    icon: <Timer size={iconSize} strokeWidth={iconStroke} />,
    labelKey: "nav.tasks",
  });
  primaryItems.push({
    key: "token-usage",
    path: "/token-usage",
    icon: <Activity size={iconSize} strokeWidth={iconStroke} />,
    labelKey: "nav.tokenUsage",
  });
  sections.push({ items: primaryItems });

  const knowledgeItems: NavItem[] = [];
  if (navAllowed(user, "knowledge-bases")) {
    knowledgeItems.push({
      key: "knowledge-bases",
      path: "/knowledge-bases",
      icon: <Database size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.knowledgeBases",
    });
  }
  if (knowledgeItems.length > 0) {
    sections.push({ groupKey: "nav.knowledge", items: knowledgeItems });
  }

  const settingsItems: NavItem[] = [];
  if (navAllowed(user, "connectors")) {
    settingsItems.push({
      key: "connectors",
      path: "/connectors",
      icon: <Link2 size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.connectors",
    });
  }
  if (navAllowed(user, "skill-packages")) {
    settingsItems.push({
      key: "skill-packages",
      path: "/skill-packages",
      icon: <Package size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.skillPackages",
    });
  }
  if (settingsItems.length > 0) {
    sections.push({ groupKey: "nav.settings", items: settingsItems });
  }

  const controlItems: NavItem[] = [];
  if (navAllowed(user, "workbench")) {
    controlItems.push({
      key: "workbench",
      path: "/workbench",
      icon: <PanelsTopLeft size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.workbench",
    });
  }
  // Server desktop + phone share one nav entry; phone tab also needs host capability.
  if (
    userCan(user, "desktop") ||
    (opts?.mobileEnabled && userCan(user, "mobile"))
  ) {
    controlItems.push({
      key: "remote-desktop",
      path: "/remote-desktop",
      icon: <Monitor size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.remoteDesktop",
    });
  }
  // ACP: the module key `acp` (PERM.acp) — the same one the route reads. The
  // global runner definitions inside stay a system-administrator write (§4.4).
  if (navAllowed(user, "acp")) {
    controlItems.push({
      key: "acp",
      path: "/acp",
      icon: <Share2 size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.acp",
    });
  }
  if (controlItems.length > 0) {
    sections.push({ groupKey: "nav.control", items: controlItems });
  }

  const adminItems: NavItem[] = [];
  if (navAllowed(user, "admin-users")) {
    adminItems.push({
      key: "admin-users",
      path: "/admin/users",
      icon: <UsersIcon size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminUsers",
    });
  }
  if (navAllowed(user, "admin-org-units")) {
    adminItems.push({
      key: "admin-org-units",
      path: "/admin/org-units",
      icon: <Network size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminOrgUnits",
    });
  }
  if (navAllowed(user, "models")) {
    adminItems.push({
      key: "models",
      path: "/admin/models",
      icon: <Cpu size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.models",
    });
  }
  if (navAllowed(user, "admin-storage")) {
    adminItems.push({
      key: "admin-storage",
      path: "/admin/backend",
      icon: <HardDrive size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminStorage",
    });
  }
  if (navAllowed(user, "admin-plugins")) {
    adminItems.push({
      key: "admin-plugins",
      path: "/admin/plugins",
      icon: <Puzzle size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminPlugins",
    });
  }
  if (navAllowed(user, "admin-security")) {
    adminItems.push({
      key: "admin-security",
      path: "/admin/security",
      icon: <Shield size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.security",
    });
  }
  if (navAllowed(user, "admin-advanced")) {
    adminItems.push({
      key: "admin-advanced",
      path: "/admin/advanced",
      icon: <SlidersHorizontal size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.adminAdvanced",
    });
  }
  // Sharing governance: /api/sharing is gated on the ``users`` module key, so
  // the entry follows that key rather than a permission of its own.
  if (userCan(user, "users")) {
    adminItems.push({
      key: "sharing",
      path: "/sharing",
      icon: <ShieldCheck size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.sharing",
    });
  }
  if (adminItems.length > 0) {
    sections.push({ groupKey: "nav.admin", items: adminItems });
  }
  return sections;
}

/** Group real module destinations without broadening their existing permissions. */
export function buildNavSections(
  user: OctopUser | null,
  opts?: { mobileEnabled?: boolean },
): NavSection[] {
  const modules = buildModuleNavSections(user, opts).flatMap(
    (section) => section.items,
  );
  const byKey = new Map(modules.map((item) => [item.key, item]));
  const select = (keys: string[]) =>
    keys.flatMap((key) => {
      const item = byKey.get(key);
      return item ? [item] : [];
    });
  const primary = select([
    "chat",
    "features",
    "knowledge-bases",
    "experts",
    "teams",
    "tasks",
    "token-usage",
  ]);
  const collaboration = select(["admin-users", "admin-org-units", "sharing"])
    .filter((item) => item.key !== "admin-users" || userCan(user, "users"))
    .map((item) =>
      item.key === "admin-users"
        ? { ...item, path: "/admin/users?tab=local" }
        : item,
    );
  const capabilities = select(["connectors", "skill-packages"]);
  const execution = select(["workbench", "remote-desktop", "acp"]);
  const system = select([
    "models",
    "admin-storage",
    "admin-plugins",
    "admin-security",
    "admin-advanced",
  ]);
  if (userCan(user, "sso")) {
    system.push({
      key: "identity",
      path: "/admin/users?tab=feishu",
      icon: <UsersIcon size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.identity",
    });
  }
  if (system.length) {
    system.push({
      key: "pwa-debug",
      path: "/pwa-debug",
      icon: <Activity size={iconSize} strokeWidth={iconStroke} />,
      labelKey: "nav.diagnostics",
    });
  }
  const operations: NavItem[] = [];
  const workspace = (
    key: string,
    labelKey: string,
    icon: ReactNode,
    children: NavItem[],
  ) => {
    if (children.length)
      operations.push({
        key,
        labelKey,
        icon,
        path: children[0].path,
        children,
      });
  };
  workspace(
    "capabilities",
    "nav.capabilities",
    <Boxes size={iconSize} strokeWidth={iconStroke} />,
    capabilities,
  );
  workspace(
    "execution",
    "nav.execution",
    <PanelsTopLeft size={iconSize} strokeWidth={iconStroke} />,
    execution,
  );
  workspace(
    "system",
    "nav.system",
    <Settings2 size={iconSize} strokeWidth={iconStroke} />,
    system,
  );
  return [
    { items: primary },
    ...(collaboration.length
      ? [{ groupKey: "nav.collaboration", items: collaboration }]
      : []),
    ...(operations.length
      ? [{ groupKey: "nav.operations", items: operations }]
      : []),
  ];
}

/** Resolve a module route to its parent workspace; SSO remains separate from user administration. */
export function findWorkspaceItem(
  sections: NavSection[],
  moduleKey: string,
  search = "",
): NavItem | undefined {
  const items = sections.flatMap((section) => section.items);
  let key = moduleKey;
  if (moduleKey === "admin-users") {
    const tab = new URLSearchParams(search).get("tab");
    if (
      (tab && tab !== "local") ||
      !items.some((item) => item.key === "admin-users")
    )
      key = "identity";
  }
  return items.find(
    (item) =>
      item.key === key || item.children?.some((child) => child.key === key),
  );
}
