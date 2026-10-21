import { useCallback, useEffect, useMemo, useState } from "react";
import { useLocation, useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { TFunction } from "i18next";
import { Button, Checkbox, Drawer, message } from "antd";
import AvatarDropdown from "../components/AvatarDropdown";
import CurrentVersionBadge from "../components/CurrentVersionBadge";
import {
  ArrowRightLeft,
  X,
  ChevronDown,
  Settings2,
  ArrowUp,
  ArrowDown,
} from "lucide-react";
import { useTheme } from "../context/ThemeContext";
import { useBranding } from "../context/BrandingContext";
import { useLayoutMode } from "../context/LayoutModeContext";
import { useCurrentUser, useSetCurrentUser } from "../hooks/useCurrentUser";
import { prefetchRoute } from "../routes/prefetch";
import { useServerCapabilities } from "../hooks/useServerCapabilities";
import { useChatSidebarOpen } from "../pages/Chat/hooks/useChatSidebarState";
import { EXPAND_CHAT_RAIL_EVENT } from "../pages/Chat/components/ChatSidebarPanel";
import {
  preferencesApi,
  type SidebarPreferences,
} from "../api/modules/preferences";
import {
  CHAT_HISTORY_RAIL_ID,
  OPEN_NAV_RECORDS_EVENT,
  isChatPath,
} from "./chatHistoryRail";
import type { MinimalNavPane } from "./layoutModeStorage";
import MinimalRecordsHost from "./MinimalRecordsHost";
import SidebarCollapsedIconNav from "./SidebarCollapsedIconNav";
import SidebarMinimalPaneToggle from "./SidebarMinimalPaneToggle";
import {
  COLLAPSED_WIDTH,
  EXPANDED_WIDTH,
  buildNavSections,
  type NavItem,
  type NavSection,
} from "./sidebarNav";
import styles from "./Sidebar.module.less";
import { typeSize } from "../utils/mobileTypeScale";
const DEFAULT_SIDEBAR_PREFERENCES: SidebarPreferences = {
  order: [],
  hidden: [],
};

function orderSidebarSections(
  sections: NavSection[],
  preferences: SidebarPreferences,
): NavSection[] {
  const hidden = new Set(preferences.hidden.filter((key) => key !== "chat"));
  const rank = new Map(preferences.order.map((key, index) => [key, index]));
  return sections
    .map((section) => ({
      ...section,
      items: section.items
        .filter((item) => !hidden.has(item.key))
        .sort(
          (a, b) =>
            (rank.get(a.key) ?? Number.MAX_SAFE_INTEGER) -
            (rank.get(b.key) ?? Number.MAX_SAFE_INTEGER),
        ),
    }))
    .filter((section) => section.items.length > 0);
}
const NAV_GROUPS_STORAGE_KEY = "octop:sidebar-nav-groups";
/** Minimal settings pane: skip the "设置" group header (duplicates the pane title). */
const MINIMAL_SETTINGS_HIDDEN_HEADERS = new Set(["nav.settings"]);

function loadCollapsedGroups(): Set<string> {
  try {
    const raw = localStorage.getItem(NAV_GROUPS_STORAGE_KEY);
    if (!raw) return new Set();
    const parsed = JSON.parse(raw) as unknown;
    if (Array.isArray(parsed)) {
      return new Set(parsed.filter((x): x is string => typeof x === "string"));
    }
  } catch {
    /* ignore */
  }
  return new Set();
}

function saveCollapsedGroups(collapsed: Set<string>) {
  try {
    localStorage.setItem(
      NAV_GROUPS_STORAGE_KEY,
      JSON.stringify([...collapsed]),
    );
  } catch {
    /* ignore */
  }
}

function useNavGroupCollapse(navSections: NavSection[], selectedKey: string) {
  const [collapsedGroups, setCollapsedGroups] = useState<Set<string>>(() =>
    loadCollapsedGroups(),
  );
  const activeGroupKey = navSections.find(
    (section) =>
      section.groupKey &&
      section.items.some((item) => item.key === selectedKey),
  )?.groupKey;

  const toggleGroup = useCallback((groupKey: string) => {
    setCollapsedGroups((prev) => {
      const next = new Set(prev);
      if (next.has(groupKey)) next.delete(groupKey);
      else next.add(groupKey);
      saveCollapsedGroups(next);
      return next;
    });
  }, []);

  const isGroupCollapsed = useCallback(
    (groupKey: string) => collapsedGroups.has(groupKey),
    [collapsedGroups],
  );

  useEffect(() => {
    if (!activeGroupKey) return;
    setCollapsedGroups((prev) => {
      if (!prev.has(activeGroupKey)) return prev;
      const next = new Set(prev);
      next.delete(activeGroupKey);
      saveCollapsedGroups(next);
      return next;
    });
  }, [activeGroupKey, selectedKey]);

  return { toggleGroup, isGroupCollapsed };
}

interface SidebarProps {
  selectedKey: string;
  collapsed: boolean;
  onToggle: () => void;
  isMobile?: boolean;
}

function NavItemButton({
  item,
  active,
  isMobile,
  onNavigate,
  onExpandChatRail,
  showChatRailExpand,
  t,
}: {
  item: NavItem;
  active: boolean;
  isMobile?: boolean;
  onNavigate: (path: string) => void;
  onExpandChatRail?: () => void;
  showChatRailExpand?: boolean;
  t: TFunction<"translation", undefined>;
}) {
  const showExpand = Boolean(
    showChatRailExpand && item.key === "chat" && onExpandChatRail,
  );

  return (
    <div
      className={styles.navItemRow}
      style={{
        background: active ? "var(--fn-sidebar-item-active-bg)" : "transparent",
      }}
      onMouseEnter={(e) => {
        prefetchRoute(item.path);
        if (!active) {
          e.currentTarget.style.background = "var(--fn-sidebar-item-hover)";
        }
      }}
      onMouseLeave={(e) => {
        if (!active) {
          e.currentTarget.style.background = "transparent";
        }
      }}
    >
      <button
        type="button"
        className={styles.navItemMain}
        onClick={() => onNavigate(item.path)}
        style={{
          color: active
            ? "var(--fn-sidebar-item-active-text)"
            : "var(--fn-text-secondary)",
          fontSize: typeSize(14, isMobile),
          fontWeight: active ? 500 : 400,
          paddingRight: showExpand ? 4 : 12,
        }}
      >
        <span
          style={{
            flexShrink: 0,
            display: "flex",
            alignItems: "center",
            color: active
              ? "var(--fn-sidebar-item-active-text)"
              : "var(--fn-text-tertiary)",
          }}
        >
          {item.icon}
        </span>
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            display: "flex",
            alignItems: "center",
            gap: 6,
            minWidth: 0,
          }}
        >
          {t(item.labelKey)}
          {item.badge && (
            <span
              className="nav-badge-new"
              style={{
                fontSize: typeSize(9, isMobile),
                fontWeight: 600,
                color: "#fff",
                backgroundColor: "#ff4d4f",
                padding: "1px 4px",
                borderRadius: "2px",
                whiteSpace: "nowrap",
                flexShrink: 0,
                textTransform: "uppercase",
                lineHeight: 1.2,
                letterSpacing: "0.5px",
              }}
            >
              {item.badge}
            </span>
          )}
        </span>
      </button>
      {showExpand ? (
        <button
          type="button"
          className={styles.chatRailExpandBtn}
          onClick={(e) => {
            e.stopPropagation();
            onExpandChatRail?.();
          }}
          aria-label={t("chat.expandHistorySidebar", "展开对话历史")}
          title={t("chat.expandHistorySidebar", "展开对话历史")}
        >
          <ArrowRightLeft size={14} strokeWidth={1.8} aria-hidden />
        </button>
      ) : null}
    </div>
  );
}

function NavList({
  sections,
  selectedKey,
  onNavigate,
  onExpandChatRail,
  showChatRailExpand,
  isMobile,
  isGroupCollapsed,
  toggleGroup,
  sectionFilter = "all",
  /** Group keys whose section headers are omitted (items still render). */
  hideGroupHeaderKeys,
}: {
  sections: NavSection[];
  selectedKey: string;
  onNavigate: (path: string) => void;
  onExpandChatRail?: () => void;
  showChatRailExpand?: boolean;
  isMobile?: boolean;
  isGroupCollapsed: (groupKey: string) => boolean;
  toggleGroup: (groupKey: string) => void;
  /** all = classic; primary = top flat entries; grouped = settings/control/admin */
  sectionFilter?: "all" | "primary" | "grouped";
  hideGroupHeaderKeys?: ReadonlySet<string>;
}) {
  const { t } = useTranslation();
  const navSections = sections.filter((section) => {
    if (sectionFilter === "primary") return !section.groupKey;
    if (sectionFilter === "grouped") return Boolean(section.groupKey);
    return true;
  });

  const MOBILE_HIDDEN_KEYS = new Set<string>();

  return (
    <div
      style={{
        padding:
          sectionFilter === "grouped"
            ? "0 12px 8px"
            : sectionFilter === "primary"
            ? "8px 12px 0"
            : "8px 12px",
      }}
    >
      {navSections.map((section, sectionIndex) => {
        const visibleItems = isMobile
          ? section.items.filter((item) => !MOBILE_HIDDEN_KEYS.has(item.key))
          : section.items;
        if (visibleItems.length === 0) return null;

        const sectionKey = section.groupKey ?? `flat-${sectionIndex}`;
        const hideHeader =
          Boolean(section.groupKey) &&
          Boolean(hideGroupHeaderKeys?.has(section.groupKey!));
        const isFlat = !section.groupKey || hideHeader;
        const groupCollapsed = section.groupKey
          ? isGroupCollapsed(section.groupKey)
          : false;

        if (isFlat) {
          return (
            <div key={sectionKey} className={styles.navGroup}>
              <div className={styles.navGroupItems}>
                {visibleItems.map((item) => (
                  <NavItemButton
                    key={item.key}
                    item={item}
                    active={selectedKey === item.key}
                    isMobile={isMobile}
                    onNavigate={onNavigate}
                    onExpandChatRail={onExpandChatRail}
                    showChatRailExpand={showChatRailExpand}
                    t={t}
                  />
                ))}
              </div>
            </div>
          );
        }

        return (
          <div key={sectionKey} className={styles.navGroup}>
            <button
              type="button"
              className={styles.navGroupHeader}
              onClick={() => toggleGroup(section.groupKey!)}
              aria-expanded={!groupCollapsed}
            >
              <span className={styles.navGroupLabel}>
                {t(section.groupKey!)}
              </span>
              <ChevronDown
                size={12}
                strokeWidth={2}
                className={`${styles.navGroupChevron} ${
                  groupCollapsed ? styles.navGroupChevronFolded : ""
                }`}
                aria-hidden
              />
            </button>

            {!groupCollapsed && (
              <div className={styles.navGroupItems}>
                {visibleItems.map((item) => (
                  <NavItemButton
                    key={item.key}
                    item={item}
                    active={selectedKey === item.key}
                    isMobile={isMobile}
                    onNavigate={onNavigate}
                    onExpandChatRail={onExpandChatRail}
                    showChatRailExpand={showChatRailExpand}
                    t={t}
                  />
                ))}
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

export default function Sidebar({
  selectedKey,
  collapsed,
  onToggle,
  isMobile,
}: SidebarProps) {
  const navigate = useNavigate();
  const location = useLocation();
  const { t } = useTranslation();
  const { isDark } = useTheme();
  const user = useCurrentUser();
  const setUser = useSetCurrentUser();
  const { mobileEnabled } = useServerCapabilities();
  const { layoutMode, minimalPane, setMinimalPane } = useLayoutMode();
  const isMinimal = layoutMode === "minimal";
  const onChatPath = isChatPath(location.pathname);
  const [sidebarPreferences, setSidebarPreferences] =
    useState<SidebarPreferences>(DEFAULT_SIDEBAR_PREFERENCES);
  const [navSettingsOpen, setNavSettingsOpen] = useState(false);
  const [navSettingsSaving, setNavSettingsSaving] = useState(false);
  const [navSettingsItems, setNavSettingsItems] = useState<NavItem[]>([]);
  const [sidebarPreferenceUserId, setSidebarPreferenceUserId] = useState<
    number | null
  >(null);
  const [navSettingsHidden, setNavSettingsHidden] = useState<string[]>([]);
  const effectiveSidebarPreferences =
    sidebarPreferenceUserId === user?.id
      ? sidebarPreferences
      : DEFAULT_SIDEBAR_PREFERENCES;
  const availableSections = useMemo(
    () => buildNavSections(user, { mobileEnabled }),
    [user, mobileEnabled],
  );
  const navSections = useMemo(
    () => orderSidebarSections(availableSections, effectiveSidebarPreferences),
    [availableSections, effectiveSidebarPreferences],
  );
  const allNavItems = useMemo(
    () => availableSections.flatMap((section) => section.items),
    [availableSections],
  );
  const { toggleGroup, isGroupCollapsed } = useNavGroupCollapse(
    navSections,
    selectedKey,
  );
  const [chatSidebarOpen, setChatSidebarOpen] = useChatSidebarOpen();
  const showChatRailExpand = !isMinimal && !chatSidebarOpen;

  const isRailCollapsed = collapsed && !isMobile;
  const brand = useBranding();
  const wordmarkSrc = isDark
    ? brand.logos.wordmark_dark
    : brand.logos.wordmark_light;

  useEffect(() => {
    let cancelled = false;
    setSidebarPreferenceUserId(null);
    if (!user?.id) return;
    void preferencesApi.get().then(
      (prefs) => {
        if (!cancelled) {
          setSidebarPreferences(prefs.sidebar ?? { order: [], hidden: [] });
          setSidebarPreferenceUserId(user.id);
        }
      },
      () => {
        if (!cancelled) {
          setSidebarPreferences({ order: [], hidden: [] });
          setSidebarPreferenceUserId(user.id);
        }
      },
    );
    return () => {
      cancelled = true;
    };
  }, [user?.id]);

  useEffect(() => {
    if (!navSettingsOpen) return;
    const rank = new Map(
      effectiveSidebarPreferences.order.map((key, index) => [key, index]),
    );
    setNavSettingsItems(
      [...allNavItems].sort(
        (a, b) =>
          (rank.get(a.key) ?? Number.MAX_SAFE_INTEGER) -
          (rank.get(b.key) ?? Number.MAX_SAFE_INTEGER),
      ),
    );
    setNavSettingsHidden(effectiveSidebarPreferences.hidden);
  }, [allNavItems, navSettingsOpen, effectiveSidebarPreferences]);

  const navSectionByItem = new Map(
    availableSections.flatMap((section, index) =>
      section.items.map((item) => [item.key, index] as const),
    ),
  );
  const canMoveNavSetting = (index: number, offset: -1 | 1) => {
    const target = index + offset;
    return (
      target >= 0 &&
      target < navSettingsItems.length &&
      navSectionByItem.get(navSettingsItems[index]?.key) ===
        navSectionByItem.get(navSettingsItems[target]?.key)
    );
  };
  const moveNavSetting = (index: number, offset: -1 | 1) => {
    if (!canMoveNavSetting(index, offset)) return;
    setNavSettingsItems((previous) => {
      const target = index + offset;
      const next = [...previous];
      [next[index], next[target]] = [next[target], next[index]];
      return next;
    });
  };

  const toggleNavHidden = (key: string, hidden: boolean) => {
    setNavSettingsHidden((previous) =>
      hidden
        ? [...new Set([...previous, key])]
        : previous.filter((item) => item !== key),
    );
  };

  const saveNavSettings = async () => {
    setNavSettingsSaving(true);
    try {
      const next = {
        order: navSettingsItems.map((item) => item.key),
        hidden: navSettingsHidden.filter((key) =>
          allNavItems.some((item) => item.key === key),
        ),
      };
      const saved = await preferencesApi.patch({ sidebar: next });
      setSidebarPreferences(saved.sidebar);
      setSidebarPreferenceUserId(user?.id ?? null);
      setNavSettingsOpen(false);
    } catch {
      message.error(
        t("nav.preferencesSaveFailed", "Could not save navigation preferences"),
      );
    } finally {
      setNavSettingsSaving(false);
    }
  };

  const selectMinimalPane = useCallback(
    (pane: MinimalNavPane, opts?: { expand?: boolean }) => {
      setMinimalPane(pane);
      if (opts?.expand && collapsed) {
        onToggle();
      }
    },
    [collapsed, onToggle, setMinimalPane],
  );

  // Pane choice is user-controlled; do not flip 记录/设置 when the route changes.
  // Both records-open events map to the same pane switch in minimal mode.
  useEffect(() => {
    if (!isMinimal) return;
    const handler = () => {
      selectMinimalPane("records", { expand: true });
    };
    window.addEventListener(OPEN_NAV_RECORDS_EVENT, handler);
    window.addEventListener(EXPAND_CHAT_RAIL_EVENT, handler);
    return () => {
      window.removeEventListener(OPEN_NAV_RECORDS_EVENT, handler);
      window.removeEventListener(EXPAND_CHAT_RAIL_EVENT, handler);
    };
  }, [isMinimal, selectMinimalPane]);

  const handleNavigate = (path: string) => {
    // When navigating to /chat, preserve the current chatId in the URL so the
    // Chat component is not remounted (key stays the same) and the user stays
    // on their most recent conversation instead of seeing a blank welcome screen.
    if (path === "/chat" && window.location.pathname.startsWith("/chat/")) {
      if (isMobile) onToggle();
      return;
    }
    navigate(path);
    if (isMobile) onToggle();
  };

  const handleExpandChatRail = useCallback(() => {
    if (isMinimal) {
      selectMinimalPane("records", { expand: true });
      return;
    }
    window.dispatchEvent(new Event(EXPAND_CHAT_RAIL_EVENT));
    setChatSidebarOpen(true);
    if (!window.location.pathname.startsWith("/chat")) {
      navigate("/chat");
    }
    if (isMobile) onToggle();
  }, [
    isMinimal,
    isMobile,
    navigate,
    onToggle,
    selectMinimalPane,
    setChatSidebarOpen,
  ]);

  const brandInner = (
    <>
      <img
        src={isRailCollapsed ? brand.logos.mark : wordmarkSrc}
        alt={brand.name.en}
        style={{
          height: isRailCollapsed ? 32 : isMobile ? 38 : 36,
          width: isRailCollapsed ? 32 : "auto",
          maxWidth: isRailCollapsed ? 32 : isMobile ? 190 : 160,
          objectFit: "contain",
          display: "block",
          flexShrink: 0,
          borderRadius: isRailCollapsed ? 8 : undefined,
        }}
      />
      {!isRailCollapsed && !isMobile && (
        <CurrentVersionBadge isMobile={isMobile} />
      )}
    </>
  );

  const userFooter = (
    <div
      className={styles.sidebarUser}
      style={{
        flexShrink: 0,
        padding: isRailCollapsed ? "10px 0" : "10px 12px",
        display: "flex",
        justifyContent: isRailCollapsed ? "center" : "stretch",
        alignItems: "center",
        gap: 6,
      }}
    >
      <button
        type="button"
        aria-label={t("nav.customize", "Customize navigation")}
        title={t("nav.customize", "Customize navigation")}
        onClick={() => setNavSettingsOpen(true)}
        style={{
          border: 0,
          borderRadius: 8,
          padding: 8,
          background: "transparent",
          color: "var(--fn-text-tertiary)",
          cursor: "pointer",
          display: "flex",
        }}
      >
        <Settings2 size={16} strokeWidth={1.8} />
      </button>
      <AvatarDropdown
        user={user}
        onUserChange={setUser}
        placement="sidebar"
        compact={isRailCollapsed}
        onBeforeOpenSettings={isMobile && !collapsed ? onToggle : undefined}
      />
      <Drawer
        title={t("nav.customize", "Customize navigation")}
        open={navSettingsOpen}
        onClose={() => setNavSettingsOpen(false)}
        width={360}
        destroyOnHidden
        extra={
          <Button
            type="primary"
            loading={navSettingsSaving}
            onClick={() => void saveNavSettings()}
          >
            {t("common.save")}
          </Button>
        }
      >
        <div style={{ display: "grid", gap: 8 }}>
          {navSettingsItems.map((item, index) => (
            <div
              key={item.key}
              style={{ display: "flex", alignItems: "center", gap: 8 }}
            >
              <Checkbox
                checked={
                  item.key === "chat" || !navSettingsHidden.includes(item.key)
                }
                disabled={item.key === "chat"}
                onChange={(event) =>
                  toggleNavHidden(item.key, !event.target.checked)
                }
              />
              <span
                style={{ display: "flex", color: "var(--fn-text-tertiary)" }}
              >
                {item.icon}
              </span>
              <span style={{ flex: 1 }}>{t(item.labelKey)}</span>
              <Button
                size="small"
                type="text"
                icon={<ArrowUp size={14} />}
                aria-label={t("nav.moveUp", "Move up")}
                disabled={!canMoveNavSetting(index, -1)}
                onClick={() => moveNavSetting(index, -1)}
              />
              <Button
                size="small"
                type="text"
                icon={<ArrowDown size={14} />}
                aria-label={t("nav.moveDown", "Move down")}
                disabled={!canMoveNavSetting(index, 1)}
                onClick={() => moveNavSetting(index, 1)}
              />
            </div>
          ))}
        </div>
      </Drawer>
    </div>
  );

  const primaryItems =
    navSections.find((section) => !section.groupKey)?.items ?? [];
  const groupedItems = navSections
    .filter((section) => section.groupKey)
    .flatMap((section) =>
      section.groupKey && isGroupCollapsed(section.groupKey)
        ? []
        : section.items,
    );

  const paneToggle = (
    <SidebarMinimalPaneToggle
      minimalPane={minimalPane}
      collapsed={isRailCollapsed}
      onSelect={selectMinimalPane}
    />
  );

  const classicNavBody = isRailCollapsed ? (
    <div style={{ padding: "8px 0" }}>
      <SidebarCollapsedIconNav
        items={navSections.flatMap((section) => {
          if (section.groupKey && isGroupCollapsed(section.groupKey)) {
            return [];
          }
          return section.items;
        })}
        selectedKey={selectedKey}
        onNavigate={handleNavigate}
        t={t}
      />
    </div>
  ) : (
    <NavList
      sections={navSections}
      selectedKey={selectedKey}
      onNavigate={handleNavigate}
      onExpandChatRail={handleExpandChatRail}
      showChatRailExpand={showChatRailExpand}
      isMobile={isMobile}
      isGroupCollapsed={isGroupCollapsed}
      toggleGroup={toggleGroup}
    />
  );

  const minimalNavBody = (
    <div className={styles.minimalNavBody}>
      {isRailCollapsed ? (
        <div style={{ padding: "8px 0", flexShrink: 0 }}>
          <SidebarCollapsedIconNav
            items={primaryItems}
            selectedKey={selectedKey}
            onNavigate={handleNavigate}
            t={t}
          />
          {paneToggle}
          {minimalPane === "settings" ? (
            <SidebarCollapsedIconNav
              items={groupedItems}
              selectedKey={selectedKey}
              onNavigate={handleNavigate}
              t={t}
            />
          ) : null}
        </div>
      ) : (
        <>
          <div className={styles.minimalPrimaryBlock}>
            <NavList
              sections={navSections}
              selectedKey={selectedKey}
              onNavigate={handleNavigate}
              onExpandChatRail={handleExpandChatRail}
              showChatRailExpand={false}
              isMobile={isMobile}
              isGroupCollapsed={isGroupCollapsed}
              toggleGroup={toggleGroup}
              sectionFilter="primary"
            />
            {paneToggle}
          </div>
          <div
            className={styles.minimalSettingsPane}
            hidden={minimalPane !== "settings"}
          >
            <NavList
              sections={navSections}
              selectedKey={selectedKey}
              onNavigate={handleNavigate}
              isMobile={isMobile}
              isGroupCollapsed={isGroupCollapsed}
              toggleGroup={toggleGroup}
              sectionFilter="grouped"
              hideGroupHeaderKeys={MINIMAL_SETTINGS_HIDDEN_HEADERS}
            />
          </div>
        </>
      )}
      {/*
        Chat route: empty mount point — Chat portals live sessions here.
        Other routes: MinimalRecordsHost keeps expert/session nav available.
      */}
      <div
        id={CHAT_HISTORY_RAIL_ID}
        className={styles.minimalRecordsPane}
        hidden={isRailCollapsed || minimalPane !== "records"}
      >
        {!onChatPath ? <MinimalRecordsHost /> : null}
      </div>
    </div>
  );

  const navScrollBody = isMinimal ? minimalNavBody : classicNavBody;

  // Mobile: fixed overlay drawer
  if (isMobile) {
    return (
      <div
        style={{
          position: "fixed",
          top: 0,
          bottom: 0,
          left: 0,
          height: "auto",
          width: EXPANDED_WIDTH,
          boxSizing: "border-box",
          paddingLeft: "env(safe-area-inset-left, 0px)",
          background: "var(--fn-sidebar-bg)",
          borderRight: "1px solid var(--fn-sidebar-border)",
          zIndex: 100,
          display: "flex",
          flexDirection: "column",
          transform: collapsed ? "translateX(-100%)" : "translateX(0)",
          transition: "transform 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
          boxShadow: collapsed ? "none" : "4px 0 20px rgba(0,0,0,0.10)",
        }}
      >
        <div
          style={{
            height: "calc(56px + env(safe-area-inset-top, 0px))",
            display: "flex",
            alignItems: "center",
            justifyContent: "space-between",
            padding: "env(safe-area-inset-top, 0px) 12px 0 16px",
            boxSizing: "border-box",
            flexShrink: 0,
            gap: 8,
          }}
        >
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 6,
              minWidth: 0,
              flex: 1,
            }}
          >
            {brandInner}
          </div>
          <button
            type="button"
            onClick={onToggle}
            aria-label={t("nav.collapseSidebar")}
            style={{
              display: "flex",
              alignItems: "center",
              justifyContent: "center",
              width: 32,
              height: 32,
              border: "none",
              borderRadius: "var(--fn-radius-md)",
              background: "transparent",
              color: "var(--fn-text-tertiary)",
              cursor: "pointer",
              flexShrink: 0,
            }}
          >
            <X size={16} strokeWidth={1.8} />
          </button>
        </div>

        <div
          style={{
            flex: 1,
            overflow:
              isMinimal && minimalPane === "records" ? "hidden" : "auto",
            minHeight: 0,
            display: "flex",
            flexDirection: "column",
          }}
        >
          {navScrollBody}
        </div>

        <div
          style={{
            paddingBottom: "calc(8px + env(safe-area-inset-bottom, 0px))",
          }}
        >
          {userFooter}
        </div>
      </div>
    );
  }

  // Desktop: custom sidebar with icon-only collapsed mode.
  // Right border is drawn by MainLayout's RailEdgeControl.
  return (
    <div
      style={{
        width: isRailCollapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH,
        minWidth: isRailCollapsed ? COLLAPSED_WIDTH : EXPANDED_WIDTH,
        background: "var(--fn-sidebar-bg)",
        borderRight: "none",
        transition:
          "width 0.25s cubic-bezier(0.4, 0, 0.2, 1), min-width 0.25s cubic-bezier(0.4, 0, 0.2, 1)",
        overflow: "hidden",
        display: "flex",
        flexDirection: "column",
        flexShrink: 0,
        alignSelf: "stretch",
        minHeight: 0,
      }}
    >
      <div
        className={styles.sidebarBrand}
        style={{
          display: "flex",
          alignItems: "center",
          gap: 6,
          minWidth: 0,
          padding: isRailCollapsed ? "12px 0" : "14px 14px 10px",
          justifyContent: isRailCollapsed ? "center" : "flex-start",
          flexShrink: 0,
        }}
      >
        {brandInner}
      </div>

      <div
        style={{
          flex: 1,
          minHeight: 0,
          overflowY: isMinimal && minimalPane === "records" ? "hidden" : "auto",
          overflowX: "hidden",
          display: "flex",
          flexDirection: "column",
        }}
      >
        {navScrollBody}
      </div>

      {userFooter}
    </div>
  );
}
