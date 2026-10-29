import {
  useCallback,
  useEffect,
  useMemo,
  useState,
  type CSSProperties,
} from "react";
import {
  Avatar,
  Badge,
  Button,
  ConfigProvider,
  Drawer,
  Empty,
  List,
  Space,
  Table,
  Tooltip,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  Activity,
  Bell,
  Cpu,
  Database,
  GraduationCap,
  HardDrive,
  LayoutGrid,
  Link2,
  Menu,
  MessageSquareText,
  Monitor,
  Network,
  Package,
  PanelLeftClose,
  PanelLeftOpen,
  PanelsTopLeft,
  Puzzle,
  Share2,
  Shield,
  ShieldCheck,
  SlidersHorizontal,
  Sparkles,
  Timer,
  Users,
  Waypoints,
  X,
} from "lucide-react";
import { BRAND } from "../../../brand.generated";
import { useIsMobile } from "../../../hooks/useIsMobile";
import PageShell from "../../../layouts/PageShell";
import navStyles from "../../../layouts/Sidebar.module.less";
import { brandTokensFor, DEFAULT_PALETTE } from "../../../styles/themePalettes";
import "../../../styles/theme-vars.css";
import type { PreviewVariantProps } from "../model";
import { WorkspacePages, WORKSPACE_PAGES } from "./WorkspacePages";
import { IntegrationPages, INTEGRATION_PAGES } from "./IntegrationPages";
import {
  AdministrationPages,
  ADMINISTRATION_PAGES,
} from "./AdministrationPages";
import KnowledgeContent from "./KnowledgePage";
import ChatFlowPreview from "./chat/ChatFlowPreview";
import { DemoSettings, useDemoState } from "./DemoKit";
import {
  INITIAL_DEMO_BASES,
  INITIAL_DEMO_NOTES,
  noteDocument,
} from "./knowledgeData";
import type { DemoRow } from "./contract";
import {
  FULL_PREVIEW_NAV,
  FULL_PREVIEW_PAGES,
  demoNavKey,
  normalizeDemoLocation,
  readDemoLocation,
} from "./catalog";
import {
  DEMO_NOTIFICATION_KEY,
  initialDemoNotifications,
  type DemoNotification,
} from "./notifications";
import type { DemoPageProps } from "./contract";
import styles from "../variants/OriginalPreview.module.less";
import fullStyles from "./PlatformPreview.module.less";
import { PREVIEW_NAVIGATE_EVENT } from "./PreviewEnvironment";

const icons = {
  chat: MessageSquareText,
  features: LayoutGrid,
  experts: GraduationCap,
  teams: Network,
  tasks: Timer,
  "token-usage": Activity,
  personalization: Sparkles,
  channels: Waypoints,
  connectors: Link2,
  "skill-packages": Package,
  "knowledge-bases": Database,
  workbench: PanelsTopLeft,
  "remote-desktop": Monitor,
  acp: Share2,
  "admin-users": Users,
  "admin-org-units": Network,
  models: Cpu,
  "admin-storage": HardDrive,
  "admin-plugins": Puzzle,
  "admin-security": Shield,
  "admin-advanced": SlidersHorizontal,
  sharing: ShieldCheck,
};

function DemoPage(props: DemoPageProps) {
  const { t } = useTranslation();
  if (props.path === "/chat") return <ChatFlowPreview {...props} />;
  if (props.path === "/knowledge-bases") return <KnowledgeContent {...props} />;
  if (WORKSPACE_PAGES.some((page) => page.path === props.path))
    return <WorkspacePages {...props} />;
  if (INTEGRATION_PAGES.some((page) => page.path === props.path))
    return <IntegrationPages {...props} />;
  if (ADMINISTRATION_PAGES.some((page) => page.path === props.path))
    return <AdministrationPages {...props} />;
  return (
    <PageShell title={t("interfacePreview.full.common.pageNotFound")}>
      <Empty description={props.path} />
      <Button onClick={() => props.navigate("/chat")}>
        {t("interfacePreview.full.common.backHome")}
      </Button>
    </PageShell>
  );
}

export default function PlatformPreview(props: PreviewVariantProps) {
  const { copy, actions } = props;
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const [view, setView] = useState(readDemoLocation);
  const [collapsed, setCollapsed] = useState(false);
  const [mobileOpen, setMobileOpen] = useState(false);
  const [utility, setUtility] = useState<
    "notifications" | "profile" | "coverage" | null
  >(null);
  const [notifications, setNotifications] = useDemoState<DemoNotification[]>(
    DEMO_NOTIFICATION_KEY,
    initialDemoNotifications(t),
  );
  const [profile] = useDemoState("full.profile", {
    name: copy.sampleUser,
    email: "chen@example.test",
    language: "zh",
  });
  const selected = demoNavKey(view.path);
  const currentPage = FULL_PREVIEW_PAGES.find(
    (page) => page.path === view.path,
  );
  const unread = notifications.filter((notice) => !notice.read).length;
  const tx = (key: string) => t(`interfacePreview.full.common.${key}`);
  const tokens = brandTokensFor(DEFAULT_PALETTE, false, BRAND.color.accent);
  const [libraries] = useDemoState<DemoRow[]>(
    "knowledge.bases",
    INITIAL_DEMO_BASES,
  );
  const [notes] = useDemoState<DemoRow[]>(
    "knowledge.notes",
    INITIAL_DEMO_NOTES,
  );
  const data = useMemo(() => {
    const ids = new Set(libraries.map((base) => base.id));
    return {
      ...props.data,
      documents: [
        ...props.data.documents,
        ...notes.map((note) => noteDocument(note, copy)),
      ].filter((document) => ids.has(document.baseId)),
    };
  }, [props.data, libraries, notes, copy]);
  const navigate = useCallback((path: string, tab?: string, item?: string) => {
    const next = normalizeDemoLocation(path, tab, item);
    const url = new URL(window.location.href);
    url.searchParams.set("style", "original");
    url.searchParams.set("page", next.path);
    if (next.tab) url.searchParams.set("tab", next.tab);
    else url.searchParams.delete("tab");
    if (next.item) url.searchParams.set("item", next.item);
    else url.searchParams.delete("item");
    if (url.href !== window.location.href)
      window.history.pushState(null, "", url);
    setView(next);
    setMobileOpen(false);
  }, []);
  useEffect(() => {
    const handleNavigate = (event: Event) => {
      const { path, directory } = (
        event as CustomEvent<{ path?: string; directory?: boolean }>
      ).detail;
      if (directory) setUtility("coverage");
      else if (path) navigate(path);
    };
    window.addEventListener(PREVIEW_NAVIGATE_EVENT, handleNavigate);
    return () =>
      window.removeEventListener(PREVIEW_NAVIGATE_EVENT, handleNavigate);
  }, [navigate]);
  useEffect(() => {
    const restore = () => {
      setView(readDemoLocation());
      setMobileOpen(false);
    };
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);
  const title = currentPage ? t(currentPage.titleKey) : tx("pageNotFound");
  const utilityTitle = utility
    ? tx(utility === "coverage" ? "coverage" : utility)
    : "";
  return (
    <ConfigProvider
      prefixCls="octop"
      theme={{
        inherit: false,
        token: {
          ...tokens,
          colorPrimary: BRAND.color.accent,
          colorText: "#242423",
          colorTextSecondary: "#73736f",
          colorBgLayout: "#fafaf9",
          colorBgContainer: "#ffffff",
          colorBorder: "#e5e5e3",
          colorBorderSecondary: "#eeedeb",
          colorFillAlter: "#fafaf9",
          colorBgTextHover: "#f4f4f2",
          borderRadius: 8,
          borderRadiusLG: 12,
          fontSize: 14,
          lineHeight: 1.6,
          boxShadowSecondary: "0 8px 32px #24242314, 0 2px 8px #24242308",
          fontFamily:
            '-apple-system, BlinkMacSystemFont, "Segoe UI", "Inter", "Noto Sans SC", sans-serif',
          ...(isMobile ? { fontSize: 15, controlHeight: 36 } : {}),
        },
        components: {
          Button: {
            primaryShadow: "none",
            defaultShadow: "none",
            fontWeight: 500,
          },
          Card: { headerFontSize: 15 },
          Table: {
            headerBg: "#fafaf9",
            headerColor: "#73736f",
            headerSplitColor: "transparent",
            rowHoverBg: "#fafaf9",
            borderColor: "#eeedeb",
          },
          Tabs: { inkBarColor: BRAND.color.accent, titleFontSize: 14 },
          Input: {
            activeShadow: "0 0 0 3px #ba4c5d0d",
            hoverBorderColor: "#bcbcb7",
          },
          Select: {
            optionSelectedBg: "#f4f4f2",
            optionSelectedColor: "#242423",
          },
          Modal: { borderRadiusLG: 16, titleFontSize: 16 },
          Drawer: { footerPaddingBlock: 16 },
          Dropdown: { borderRadiusLG: 10 },
          Tag: { defaultBg: "#fafaf9", defaultColor: "#73736f" },
          Segmented: { trackBg: "#f4f4f2", itemSelectedBg: "#fff" },
          Form: { labelColor: "#73736f" },
        },
      }}
    >
      <div
        className={styles.shell}
        data-original-preview
        data-full-preview
        data-collapsed={collapsed}
        style={
          {
            "--fn-color-brand": BRAND.color.accent,
            "--fn-sidebar-item-active-text": BRAND.color.accent,
            "--fn-sidebar-item-active-bg":
              "color-mix(in srgb, var(--fn-color-brand) 8%, transparent)",
          } as CSSProperties
        }
      >
        <aside
          className={`${styles.sidebar} ${
            mobileOpen ? styles.sidebarOpen : ""
          }`}
          aria-label={copy.originalNavigation}
          aria-hidden={isMobile && !mobileOpen}
        >
          <div className={styles.brand}>
            <img
              src={
                !isMobile && collapsed
                  ? BRAND.logo.mark
                  : BRAND.logo.wordmarkLight
              }
              alt={BRAND.name.zh}
            />
            <Button
              type="text"
              aria-label={
                isMobile
                  ? copy.close
                  : collapsed
                  ? copy.originalExpandNav
                  : copy.originalCollapseNav
              }
              onClick={() =>
                isMobile
                  ? setMobileOpen(false)
                  : setCollapsed((value) => !value)
              }
              icon={
                isMobile ? (
                  <X size={16} />
                ) : collapsed ? (
                  <PanelLeftOpen size={16} />
                ) : (
                  <PanelLeftClose size={16} />
                )
              }
            />
          </div>
          <nav className={styles.navigation}>
            {FULL_PREVIEW_NAV.map((group, index) => (
              <div className={navStyles.navGroup} key={group.group || index}>
                {group.group && (
                  <div
                    className={`${navStyles.navGroupHeader} ${styles.navGroupHeading}`}
                  >
                    <span className={navStyles.navGroupLabel}>
                      {t(group.group)}
                    </span>
                  </div>
                )}
                {group.items.map((item) => {
                  const Icon = icons[item.key];
                  const label = t(item.titleKey);
                  return (
                    <div
                      className={navStyles.navItemRow}
                      key={item.key}
                      style={{
                        background:
                          item.key === selected
                            ? "var(--fn-sidebar-item-active-bg)"
                            : undefined,
                      }}
                    >
                      <Tooltip
                        title={collapsed && !isMobile ? label : undefined}
                        placement="right"
                      >
                        <button
                          className={navStyles.navItemMain}
                          data-demo-nav={item.key}
                          aria-label={label}
                          aria-current={
                            item.key === selected ? "page" : undefined
                          }
                          style={{
                            color:
                              item.key === selected
                                ? "var(--fn-sidebar-item-active-text)"
                                : "var(--fn-text-secondary)",
                          }}
                          onClick={() => navigate(item.path)}
                        >
                          <Icon size={16} strokeWidth={1.8} />
                          <span className={styles.navLabel}>{label}</span>
                        </button>
                      </Tooltip>
                    </div>
                  );
                })}
              </div>
            ))}
          </nav>
          <div className={styles.sidebarBottom}>
            <Space className={fullStyles.footerTools}>
              <Button
                type="text"
                aria-label={tx("notifications")}
                icon={
                  <Badge count={unread} size="small">
                    <Bell size={16} />
                  </Badge>
                }
                onClick={() => setUtility("notifications")}
              />
              <Button
                type="text"
                aria-label={tx("allPages")}
                icon={<LayoutGrid size={16} />}
                onClick={() => setUtility("coverage")}
              />
              <Button
                type="text"
                aria-label={copy.help}
                icon={<ShieldCheck size={16} />}
                onClick={actions.openGuide}
              />
            </Space>
            <Button
              type="text"
              className={fullStyles.profileButton}
              aria-label={tx("profile")}
              onClick={() => setUtility("profile")}
            >
              <Avatar size={30}>{String(profile.name)[0]}</Avatar>
              <span className={styles.navLabel}>
                {profile.name} · {copy.demoOnly}
              </span>
            </Button>
          </div>
        </aside>
        {mobileOpen && (
          <button
            className={styles.backdrop}
            aria-label={copy.close}
            onClick={() => setMobileOpen(false)}
          />
        )}
        <main className={`${styles.main} ${fullStyles.main}`}>
          {isMobile && (
            <header className={styles.mobileHeader}>
              <Button
                type="text"
                icon={<Menu size={20} />}
                aria-label={copy.originalExpandNav}
                onClick={() => setMobileOpen(true)}
              />
              <Typography.Text strong>{title}</Typography.Text>
              <Button
                type="text"
                aria-label={tx("notifications")}
                icon={
                  <Badge count={unread} size="small">
                    <Bell size={17} />
                  </Badge>
                }
                onClick={() => setUtility("notifications")}
              />
            </header>
          )}
          <div
            className={fullStyles.page}
            data-demo-page={view.path}
            data-demo-tab={view.tab || ""}
            data-demo-item={view.item || ""}
          >
            <DemoPage
              key={`${view.path}:${view.tab || ""}:${view.item || ""}`}
              {...props}
              data={data}
              {...view}
              navigate={navigate}
            />
          </div>
        </main>
      </div>
      <Drawer
        title={utilityTitle}
        open={utility !== null}
        onClose={() => setUtility(null)}
        width={utility === "coverage" ? 840 : 560}
        destroyOnHidden
      >
        {utility === "notifications" && (
          <>
            <Button
              disabled={!unread}
              onClick={() =>
                setNotifications((current) =>
                  current.map((notice) => ({ ...notice, read: true })),
                )
              }
            >
              {tx("markAllRead")}
            </Button>
            <List
              locale={{ emptyText: tx("noNotifications") }}
              dataSource={notifications}
              renderItem={(notice) => (
                <List.Item
                  actions={[
                    <Button
                      key="read"
                      type="link"
                      disabled={notice.read}
                      onClick={() =>
                        setNotifications((current) =>
                          current.map((item) =>
                            item.id === notice.id
                              ? { ...item, read: true }
                              : item,
                          ),
                        )
                      }
                    >
                      {tx("markRead")}
                    </Button>,
                  ]}
                >
                  <List.Item.Meta
                    title={
                      <Button
                        type="link"
                        onClick={() => {
                          setNotifications((current) =>
                            current.map((item) =>
                              item.id === notice.id
                                ? { ...item, read: true }
                                : item,
                            ),
                          );
                          setUtility(null);
                          navigate(notice.path, notice.tab);
                        }}
                      >
                        {!notice.read && <Badge status="processing" />}
                        {notice.title}
                      </Button>
                    }
                    description={notice.body}
                  />
                </List.Item>
              )}
            />
          </>
        )}
        {utility === "profile" && (
          <DemoSettings
            storageKey="full.profile"
            title={tx("profile")}
            fields={[
              { name: "name", label: tx("name"), required: true },
              { name: "email", label: tx("email") },
              {
                name: "language",
                label: tx("language"),
                kind: "select",
                options: [
                  { value: "zh", label: tx("zh") },
                  { value: "en", label: tx("en") },
                ],
              },
            ]}
            initialValues={{
              name: copy.sampleUser,
              email: "chen@example.test",
              language: "zh",
            }}
            onSaved={(values) => {
              void i18n.changeLanguage(String(values.language));
            }}
          />
        )}
        {utility === "coverage" && (
          <>
            <Typography.Paragraph>
              {t("interfacePreview.full.common.pageCount", {
                count: FULL_PREVIEW_PAGES.length,
              })}
            </Typography.Paragraph>
            <Table
              rowKey="path"
              pagination={false}
              scroll={{ x: 700 }}
              dataSource={[...FULL_PREVIEW_PAGES]}
              columns={[
                {
                  title: tx("allPages"),
                  key: "title",
                  width: 200,
                  render: (_, page) => (
                    <Button
                      type="link"
                      onClick={() => {
                        setUtility(null);
                        navigate(page.path);
                      }}
                    >
                      {t(page.titleKey)}
                    </Button>
                  ),
                },
                { title: tx("identifier"), dataIndex: "path", width: 250 },
                {
                  title: tx("tabs"),
                  key: "tabs",
                  render: (_, page) => (
                    <Space wrap>
                      {page.tabs?.map((tab) => (
                        <Button
                          key={tab}
                          size="small"
                          onClick={() => {
                            setUtility(null);
                            navigate(page.path, tab, page.tabItems?.[tab]);
                          }}
                        >
                          {tab}
                        </Button>
                      ))}
                    </Space>
                  ),
                },
              ]}
            />
          </>
        )}
      </Drawer>
    </ConfigProvider>
  );
}
