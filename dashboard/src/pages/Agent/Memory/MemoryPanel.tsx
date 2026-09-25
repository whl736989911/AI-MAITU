/**
 * Embeddable memory dashboard (tabs + content). Used by the Memory page,
 * Experts MemoryCatalogDrawer, and Personalization.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Empty,
  Input,
  Segmented,
  Space,
  Tabs,
  Typography,
} from "antd";
import type { LucideIcon } from "lucide-react";
import {
  Bell,
  Heart,
  Inbox,
  LayoutDashboard,
  MessageSquare,
  Network,
  ScrollText,
  Settings,
  User,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import TabLabel from "../../../components/TabLabel";

import ConversationRecords from "./ConversationRecords";
import Overview from "./Overview";
import ProfileOverview from "./ProfileOverview";
import AtomsList from "./AtomsList";
import RawEventsList from "./RawEventsList";
import EpisodesList from "./EpisodesList";
import JournalList from "./JournalList";
import CandidatesReview from "./CandidatesReview";
import MemoryTree from "./MemoryTree";
import ProactiveConfig from "./ProactiveConfig";
import MemorySettings from "./MemorySettings";

import memoryDashboardApi, {
  type MemoryAccess,
  type MemoryScope,
} from "../../../api/modules/memoryDashboard";
import { featureWorkflowApi } from "../../../api/modules/featureWorkflow";
import styles from "./index.module.less";

type MemoryTab =
  | "overview"
  | "profile"
  | "library"
  | "episodes"
  | "candidates"
  | "journal"
  | "conversations"
  | "proactive"
  | "settings";

type LibraryView = "tree" | "atoms" | "raw";

interface TabDef {
  key: MemoryTab;
  labelKey: string;
  fallback: string;
  icon: LucideIcon;
  showPendingBadge?: boolean;
  /**
   * The tab writes what it shows, so it is not offered over a memory the reader
   * may not write — see ``readOnly``.
   */
  writes?: boolean;
}

const TABS: TabDef[] = [
  {
    key: "overview",
    labelKey: "memory.tabs.overview",
    fallback: "概览",
    icon: LayoutDashboard,
  },
  {
    key: "profile",
    labelKey: "memory.tabs.profile",
    fallback: "用户画像",
    icon: User,
  },
  {
    key: "library",
    labelKey: "memory.tabs.library",
    fallback: "记忆树",
    icon: Network,
  },
  {
    key: "episodes",
    labelKey: "memory.tabs.episodes",
    fallback: "情绪日记",
    icon: Heart,
  },
  {
    key: "candidates",
    labelKey: "memory.tabs.candidates",
    fallback: "记忆沉淀",
    showPendingBadge: true,
    icon: Inbox,
  },
  {
    key: "journal",
    labelKey: "memory.tabs.journal",
    fallback: "整理记录",
    icon: ScrollText,
  },
  {
    key: "conversations",
    labelKey: "memory.conversationHistory",
    fallback: "对话记录",
    icon: MessageSquare,
  },
  {
    key: "proactive",
    labelKey: "memory.tabs.proactive",
    fallback: "主动关心",
    icon: Bell,
    writes: true,
  },
  {
    key: "settings",
    labelKey: "memory.tabs.settings",
    fallback: "设置",
    icon: Settings,
    writes: true,
  },
];

export interface MemoryPanelProps {
  agentId: string | null;
  fill?: boolean;
  readOnly?: boolean;
  featureMemory?: boolean;
}

export default function MemoryPanel({
  agentId,
  fill = true,
  readOnly = false,
  featureMemory = false,
}: MemoryPanelProps) {
  const { t } = useTranslation();

  const [activeTab, setActiveTab] = useState<MemoryTab>("overview");
  const [libraryView, setLibraryView] = useState<LibraryView>("tree");
  const [pendingCount, setPendingCount] = useState(0);
  const [expandEntityId, setExpandEntityId] = useState<string | undefined>(
    undefined,
  );
  const [expandKey, setExpandKey] = useState(0);
  const [access, setAccess] = useState<MemoryAccess | null>(null);
  const [accessError, setAccessError] = useState(false);
  const [scope, setScope] = useState<MemoryScope>("shared");
  const [accessAgentId, setAccessAgentId] = useState<string | null>(null);
  const [overlay, setOverlay] = useState("");
  const [overlayLoading, setOverlayLoading] = useState(false);
  const [overlaySaving, setOverlaySaving] = useState(false);
  const [overlaySaved, setOverlaySaved] = useState(false);
  const [overlayError, setOverlayError] = useState(false);
  const [overlayLoaded, setOverlayLoaded] = useState(false);

  useEffect(() => {
    if (!featureMemory || !agentId) {
      setAccess(null);
      setAccessError(false);
      setAccessAgentId(null);
      setScope("shared");
      return;
    }
    let cancelled = false;
    setAccess(null);
    setAccessAgentId(null);
    setAccessError(false);
    memoryDashboardApi
      .getAccess(agentId)
      .then((result) => {
        if (cancelled) return;
        setAccess(result);
        setAccessError(false);
        setAccessAgentId(agentId);
        setActiveTab("overview");
        setLibraryView("tree");
        setExpandEntityId(undefined);
        setPendingCount(0);
        setScope(
          result.stage === "draft"
            ? "shared"
            : result.stage === "active"
            ? "private"
            : result.default_scope,
        );
      })
      .catch(() => {
        if (!cancelled) {
          setAccess(null);
          setAccessAgentId(null);
          setAccessError(true);
        }
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, featureMemory]);

  useEffect(() => {
    if (!featureMemory || !agentId) {
      setOverlay("");
      setOverlayLoaded(false);
      setOverlayLoading(false);
      return;
    }
    let cancelled = false;
    setOverlayLoaded(false);
    setOverlay("");
    setOverlayLoading(true);
    setOverlaySaved(false);
    setOverlayError(false);
    featureWorkflowApi
      .overlay(agentId)
      .then((result) => {
        if (!cancelled) {
          setOverlay(result.overlay ?? "");
          setOverlayLoaded(true);
        }
      })
      .catch(() => {
        if (!cancelled) setOverlayError(true);
      })
      .finally(() => {
        if (!cancelled) setOverlayLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [agentId, featureMemory]);

  const saveOverlay = async () => {
    if (!agentId) return;
    setOverlaySaving(true);
    setOverlaySaved(false);
    setOverlayError(false);
    try {
      const result = await featureWorkflowApi.putOverlay(agentId, overlay);
      setOverlay(result.overlay ?? "");
      setOverlaySaved(true);
    } catch {
      setOverlayError(true);
    } finally {
      setOverlaySaving(false);
    }
  };

  const currentAccess = accessAgentId === agentId ? access : null;
  const memoryScope = featureMemory && currentAccess ? scope : undefined;
  const effectiveReadOnly =
    readOnly ||
    (featureMemory &&
      (!currentAccess ||
        (currentAccess.stage === "active" && scope === "shared") ||
        (currentAccess.stage === "draft" && scope === "private") ||
        (scope === "shared"
          ? !currentAccess.shared_writable
          : !currentAccess.private_writable)));
  const availableScopes: MemoryScope[] =
    currentAccess?.stage === "draft" ? ["shared"] : ["shared", "private"];

  useEffect(() => {
    if (!agentId || (featureMemory && !currentAccess)) {
      setPendingCount(0);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const c = memoryScope
          ? await memoryDashboardApi.statsCounts(agentId, memoryScope)
          : await memoryDashboardApi.statsCounts(agentId);
        if (!cancelled) setPendingCount(c.candidates_pending ?? 0);
      } catch {
        if (!cancelled) setPendingCount(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, activeTab, featureMemory, currentAccess, memoryScope]);

  const tabItems = useMemo(() => {
    if (!agentId) return [];

    const library = (
      <div>
        <div className={styles.librarySwitchRow}>
          <Segmented
            value={libraryView}
            onChange={(v) => setLibraryView(v as LibraryView)}
            options={[
              {
                label: t("memory.library.viewTree", "主题视图"),
                value: "tree",
              },
              {
                label: t("memory.library.viewAtoms", "列表视图"),
                value: "atoms",
              },
              {
                label: t("memory.library.viewRaw", "原始素材"),
                value: "raw",
              },
            ]}
          />
          <span className={styles.librarySwitchHint}>
            {libraryView === "tree"
              ? t(
                  "memory.library.hintTree",
                  "按人、项目、工具等主题，分组浏览相关记忆",
                )
              : libraryView === "atoms"
              ? t(
                  "memory.library.hintAtoms",
                  "扁平展示全部记忆，可按重要程度筛选",
                )
              : t(
                  "memory.library.hintRaw",
                  "提炼前捕获的原始对话记忆（条数与「对话记录」不一一对应）",
                )}
          </span>
        </div>
        {libraryView === "tree" ? (
          <MemoryTree
            key={`${expandKey}:${memoryScope ?? "default"}:${agentId}`}
            agentId={agentId}
            scope={memoryScope}
            readOnly={effectiveReadOnly}
            initialExpandEntityId={expandEntityId}
          />
        ) : libraryView === "atoms" ? (
          <AtomsList
            key={`${memoryScope ?? "default"}:${agentId}`}
            agentId={agentId}
            scope={memoryScope}
            readOnly={effectiveReadOnly}
          />
        ) : (
          <RawEventsList agentId={agentId} scope={memoryScope} />
        )}
      </div>
    );

    const offered = TABS.filter(
      (tab) =>
        !(effectiveReadOnly && tab.writes) &&
        !(
          featureMemory &&
          (tab.key === "proactive" ||
            tab.key === "settings" ||
            tab.key === "conversations")
        ),
    );

    return offered.map((tab) => {
      const showBadge = tab.showPendingBadge && pendingCount > 0;
      const label = (
        <TabLabel icon={tab.icon}>
          {t(tab.labelKey, tab.fallback)}
          {showBadge ? (
            <span className={styles.tabBadge}>{pendingCount}</span>
          ) : null}
        </TabLabel>
      );

      let children: ReactNode = null;
      switch (tab.key) {
        case "overview":
          children = (
            <Overview
              key={`${memoryScope ?? "default"}:${agentId}`}
              agentId={agentId}
              scope={memoryScope}
              readOnly={effectiveReadOnly}
              hideMigration={featureMemory}
              onViewConversations={
                featureMemory ? undefined : () => setActiveTab("conversations")
              }
              onReviewCandidates={() => setActiveTab("candidates")}
              onOpenSettings={
                effectiveReadOnly || featureMemory
                  ? undefined
                  : () => setActiveTab("settings")
              }
            />
          );
          break;
        case "profile":
          children = (
            <ProfileOverview
              key={`${memoryScope ?? "default"}:${agentId}`}
              agentId={agentId}
              scope={memoryScope}
              onReview={() => setActiveTab("candidates")}
              onViewAll={(entityId) => {
                setExpandEntityId(entityId);
                setExpandKey((k) => k + 1);
                setLibraryView("tree");
                setActiveTab("library");
              }}
            />
          );
          break;
        case "library":
          children = library;
          break;
        case "episodes":
          children = (
            <EpisodesList
              key={`${memoryScope ?? "default"}:${agentId}`}
              agentId={agentId}
              scope={memoryScope}
            />
          );
          break;
        case "candidates":
          children = (
            <CandidatesReview
              key={`${memoryScope ?? "default"}:${agentId}`}
              agentId={agentId}
              scope={memoryScope}
              readOnly={effectiveReadOnly}
            />
          );
          break;
        case "journal":
          children = (
            <JournalList
              key={`${memoryScope ?? "default"}:${agentId}`}
              agentId={agentId}
              scope={memoryScope}
            />
          );
          break;
        case "conversations":
          children = <ConversationRecords agentId={agentId} />;
          break;
        case "proactive":
          children = (
            <ProactiveConfig
              agentId={agentId}
              onSwitchToEpisodes={() => setActiveTab("episodes")}
            />
          );
          break;
        case "settings":
          children = <MemorySettings agentId={agentId} />;
          break;
      }

      return { key: tab.key, label, children };
    });
  }, [
    agentId,
    expandEntityId,
    expandKey,
    libraryView,
    featureMemory,
    memoryScope,
    effectiveReadOnly,
    pendingCount,
    t,
  ]);

  if (!agentId) {
    return (
      <Empty
        description={t("memory.noAgentSelected")}
        style={{ marginTop: 64 }}
      />
    );
  }

  if (featureMemory && !currentAccess) {
    return accessError ? (
      <Alert type="error" showIcon message={t("memory.scope.loadFailed")} />
    ) : (
      <Empty
        description={t("memory.scope.loading", "Loading memory access…")}
      />
    );
  }

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: 0,
        height: "100%",
      }}
    >
      {featureMemory && (
        <Card
          size="small"
          title={t("personalization.overlayTitle", "我的专属指令")}
          style={{ marginBottom: 12 }}
        >
          <Typography.Paragraph type="secondary">
            {t(
              "personalization.overlayHint",
              "只影响你自己的功能调用；不会从记忆中自动生成或覆盖。",
            )}
          </Typography.Paragraph>
          {overlayError && (
            <Alert
              type="error"
              showIcon
              style={{ marginBottom: 12 }}
              message={t(
                "personalization.overlayError",
                "专属指令加载或保存失败。",
              )}
            />
          )}
          <Input.TextArea
            value={overlay}
            onChange={(event) => {
              setOverlay(event.target.value);
              setOverlaySaved(false);
            }}
            disabled={!overlayLoaded || overlayLoading || overlaySaving}
            placeholder={t(
              "personalization.overlayPlaceholder",
              "写下你希望功能额外遵循的要求…",
            )}
            maxLength={4000}
            showCount
            rows={3}
            aria-label={t("personalization.overlayTitle", "我的专属指令")}
          />
          <Space style={{ marginTop: 12 }}>
            <Button
              onClick={() => void saveOverlay()}
              loading={overlaySaving}
              disabled={!overlayLoaded || overlayLoading}
            >
              {t("common.save")}
            </Button>
            {overlaySaved && (
              <Typography.Text type="success">
                {t("personalization.overlaySaved", "已保存")}
              </Typography.Text>
            )}
          </Space>
        </Card>
      )}
      {featureMemory && (
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            marginBottom: 12,
          }}
        >
          <Typography.Text strong>
            {t("personalization.memorySourceTitle", "功能记忆")}
          </Typography.Text>
          <Segmented
            value={scope}
            onChange={(value) => {
              const nextScope = value as MemoryScope;
              if (!availableScopes.includes(nextScope)) return;
              setScope(nextScope);
              setPendingCount(0);
              setActiveTab("overview");
            }}
            options={availableScopes.map((availableScope) => ({
              label: t(
                `memory.scope.${availableScope}`,
                availableScope === "shared"
                  ? "Shared memory"
                  : "My private memory",
              ),
              value: availableScope,
            }))}
          />
        </div>
      )}
      {featureMemory && (
        <Typography.Paragraph type="secondary">
          {scope === "shared" && currentAccess?.stage === "draft"
            ? t(
                "memory.scope.draftShared",
                "Draft feature memory is shared and editable by its author.",
              )
            : scope === "shared"
            ? t(
                "memory.scope.sharedReadOnly",
                "Shared memory is read-only for active features.",
              )
            : t(
                "memory.scope.privateDescription",
                "Private memory is visible and editable only to you.",
              )}
        </Typography.Paragraph>
      )}
      <Tabs
        className={styles.memoryTabs}
        style={fill ? undefined : { height: "auto" }}
        activeKey={activeTab}
        onChange={(k) => setActiveTab(k as MemoryTab)}
        destroyOnHidden
        items={tabItems}
      />
    </div>
  );
}
