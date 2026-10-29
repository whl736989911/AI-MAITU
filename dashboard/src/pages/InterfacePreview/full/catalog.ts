import { WORKSPACE_PAGES } from "./WorkspacePages";
import { INTEGRATION_PAGES } from "./IntegrationPages";
import { ADMINISTRATION_PAGES } from "./AdministrationPages";
import type { DemoPageDefinition } from "./contract";

export const KNOWLEDGE_TABS = [
  "files",
  "sources",
  "collections",
  "templates",
  "permissions",
  "bases",
  "documents",
  "settings",
] as const;
export const FULL_PREVIEW_PAGES: readonly DemoPageDefinition[] = [
  { path: "/chat", titleKey: "nav.chat" },
  ...WORKSPACE_PAGES,
  ...INTEGRATION_PAGES,
  {
    path: "/knowledge-bases",
    titleKey: "nav.knowledgeBases",
    tabs: KNOWLEDGE_TABS,
  },
  ...ADMINISTRATION_PAGES,
];
export const FULL_PREVIEW_NAV = [
  {
    group: "",
    items: [
      { key: "chat", path: "/chat", titleKey: "nav.chat" },
      { key: "features", path: "/features", titleKey: "nav.features" },
      { key: "experts", path: "/experts", titleKey: "nav.experts" },
      { key: "teams", path: "/teams", titleKey: "nav.teams" },
      { key: "tasks", path: "/tasks", titleKey: "nav.tasks" },
      { key: "token-usage", path: "/token-usage", titleKey: "nav.tokenUsage" },
    ],
  },
  {
    group: "nav.settings",
    items: [
      {
        key: "personalization",
        path: "/personalization/skills",
        titleKey: "nav.personalization",
      },
      {
        key: "channels",
        path: "/personalization/channels",
        titleKey: "nav.channels",
      },
      { key: "connectors", path: "/connectors", titleKey: "nav.connectors" },
      {
        key: "skill-packages",
        path: "/skill-packages",
        titleKey: "nav.skillPackages",
      },
      {
        key: "knowledge-bases",
        path: "/knowledge-bases",
        titleKey: "nav.knowledgeBases",
      },
    ],
  },
  {
    group: "nav.control",
    items: [
      { key: "workbench", path: "/workbench", titleKey: "nav.workbench" },
      {
        key: "remote-desktop",
        path: "/remote-desktop",
        titleKey: "nav.remoteDesktop",
      },
      { key: "acp", path: "/acp", titleKey: "nav.acp" },
    ],
  },
  {
    group: "nav.admin",
    items: [
      { key: "admin-users", path: "/admin/users", titleKey: "nav.adminUsers" },
      {
        key: "admin-org-units",
        path: "/admin/org-units",
        titleKey: "nav.adminOrgUnits",
      },
      { key: "models", path: "/admin/models", titleKey: "nav.models" },
      {
        key: "admin-storage",
        path: "/admin/backend",
        titleKey: "nav.adminStorage",
      },
      {
        key: "admin-plugins",
        path: "/admin/plugins",
        titleKey: "nav.adminPlugins",
      },
      {
        key: "admin-security",
        path: "/admin/security",
        titleKey: "nav.security",
      },
      {
        key: "admin-advanced",
        path: "/admin/advanced",
        titleKey: "nav.adminAdvanced",
      },
      { key: "sharing", path: "/sharing", titleKey: "nav.sharing" },
    ],
  },
] as const;

export interface DemoLocation {
  path: string;
  tab?: string;
  item?: string;
}
const aliases: Record<string, DemoLocation> = {
  "/": { path: "/chat" },
  "/personalization": { path: "/personalization/skills" },
  "/channels": { path: "/personalization/channels" },
  "/skills": { path: "/personalization/skills" },
  "/subagents": { path: "/personalization/subagents" },
  "/mbti": { path: "/personalization/mbti" },
  "/memory": { path: "/personalization/memory" },
  "/personalization/acp": { path: "/acp" },
  "/workspace": { path: "/experts" },
  "/workbench/terminal": { path: "/workbench", tab: "terminal" },
  "/workbench/browser": { path: "/workbench", tab: "browser" },
  "/terminal": { path: "/workbench", tab: "terminal" },
  "/remote-browser": { path: "/workbench", tab: "browser" },
  "/remote-desktop/desktop": { path: "/remote-desktop", tab: "desktop" },
  "/remote-desktop/phone/screen": {
    path: "/remote-desktop/phone",
    tab: "screen",
  },
  "/remote-desktop/phone/shell": {
    path: "/remote-desktop/phone",
    tab: "shell",
  },
  "/remote-phone": { path: "/remote-desktop/phone", tab: "screen" },
  "/remote-android": { path: "/remote-desktop/phone", tab: "screen" },
  "/admin/sso": { path: "/admin/users", tab: "oidc" },
  "/admin/shared-models": { path: "/admin/models" },
  "/models": { path: "/admin/models" },
  "/admin/audit": { path: "/admin/security", tab: "audit" },
  "/admin/agents": { path: "/admin/users" },
  "/admin/voice": { path: "/admin/models", tab: "voice" },
  "/admin/updates": { path: "/admin/advanced", tab: "updates" },
  "/admin/storage": { path: "/admin/backend" },
  "/advanced-settings": { path: "/admin/advanced" },
  "/environments": { path: "/admin/advanced" },
  "/updates": { path: "/admin/advanced", tab: "updates" },
  "/plugins": { path: "/admin/plugins" },
  "/sessions": { path: "/chat" },
  "/cron-jobs": { path: "/tasks" },
};
export function normalizeDemoLocation(
  path: string,
  tab?: string,
  item?: string,
): DemoLocation {
  let requested = path;
  if (requested.startsWith("/orca/")) requested = requested.slice(5);
  if (requested.startsWith("/octop/")) requested = requested.slice(6);
  if (requested === "/cron") requested = "/tasks";
  if (
    requested === "/personalization" &&
    tab &&
    FULL_PREVIEW_PAGES.some((page) => page.path === `/personalization/${tab}`)
  ) {
    requested = `/personalization/${tab}`;
    tab = undefined;
  }
  const resolved = aliases[requested] || { path: requested };
  const definition = FULL_PREVIEW_PAGES.find(
    (page) => page.path === resolved.path,
  );
  const chosenTab = tab || resolved.tab;
  return {
    path: resolved.path,
    tab:
      chosenTab && definition?.tabs && !definition.tabs.includes(chosenTab)
        ? undefined
        : chosenTab,
    item: item || resolved.item,
  };
}
export function readDemoLocation(): DemoLocation {
  const params = new URLSearchParams(window.location.search);
  return normalizeDemoLocation(
    params.get("page") || "/chat",
    params.get("tab") || undefined,
    params.get("item") || undefined,
  );
}
export function demoNavKey(path: string): string {
  if (path === "/personalization/channels") return "channels";
  if (path.startsWith("/personalization/")) return "personalization";
  if (path.startsWith("/remote-desktop")) return "remote-desktop";
  return (
    FULL_PREVIEW_NAV.flatMap((group) => [...group.items]).find(
      (item) => item.path === path,
    )?.key || ""
  );
}
