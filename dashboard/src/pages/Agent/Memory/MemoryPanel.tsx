/**
 * Embeddable memory dashboard (tabs + content). Used by the Memory page,
 * Experts MemoryCatalogDrawer, and Personalization.
 */

import { useEffect, useMemo, useState, type ReactNode } from "react";
import { Empty, Segmented, Tabs } from "antd";
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

import memoryDashboardApi from "../../../api/modules/memoryDashboard";
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
    writes: true,
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
  /** Stretch tabs to fill parent height (desktop PageShell / drawer). */
  fill?: boolean;
  /**
   * Show the memory without offering to change it: the tabs that write are not
   * offered (whatever they hold stays on screen), and so are the entries that
   * would write from the tabs that remain. Defaults to false, which is every
   * surface an expert is configured on.
   */
  readOnly?: boolean;
}

export default function MemoryPanel({
  agentId,
  fill = true,
  readOnly = false,
}: MemoryPanelProps) {
  const { t } = useTranslation();

  const [activeTab, setActiveTab] = useState<MemoryTab>("overview");
  const [libraryView, setLibraryView] = useState<LibraryView>("tree");
  const [pendingCount, setPendingCount] = useState(0);
  const [expandEntityId, setExpandEntityId] = useState<string | undefined>(
    undefined,
  );
  const [expandKey, setExpandKey] = useState(0);

  useEffect(() => {
    if (!agentId) {
      setPendingCount(0);
      return;
    }
    let cancelled = false;
    (async () => {
      try {
        const c = await memoryDashboardApi.statsCounts(agentId);
        if (!cancelled) setPendingCount(c.candidates_pending ?? 0);
      } catch {
        if (!cancelled) setPendingCount(0);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, activeTab]);

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
            key={expandKey}
            agentId={agentId}
            initialExpandEntityId={expandEntityId}
          />
        ) : libraryView === "atoms" ? (
          <AtomsList agentId={agentId} />
        ) : (
          <RawEventsList agentId={agentId} />
        )}
      </div>
    );

    // A read-only panel still shows what it holds: the tabs that write are the
    // ones nobody may use here, and leaving them out is how "no" is said.
    const offered = readOnly ? TABS.filter((tab) => !tab.writes) : TABS;

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
              agentId={agentId}
              readOnly={readOnly}
              onViewConversations={() => setActiveTab("conversations")}
              // ``undefined`` rather than a handler into a tab that is not
              // offered: the panel hides the entry when it has nowhere to go,
              // and a button whose only outcome is a refusal is not an entry.
              onReviewCandidates={
                readOnly ? undefined : () => setActiveTab("candidates")
              }
              onOpenSettings={
                readOnly ? undefined : () => setActiveTab("settings")
              }
            />
          );
          break;
        case "profile":
          children = (
            <ProfileOverview
              agentId={agentId}
              onReview={readOnly ? undefined : () => setActiveTab("candidates")}
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
          children = <EpisodesList agentId={agentId} />;
          break;
        case "candidates":
          children = <CandidatesReview agentId={agentId} />;
          break;
        case "journal":
          children = <JournalList agentId={agentId} />;
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
    pendingCount,
    readOnly,
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

  return (
    <Tabs
      className={styles.memoryTabs}
      style={fill ? undefined : { height: "auto" }}
      activeKey={activeTab}
      onChange={(k) => setActiveTab(k as MemoryTab)}
      destroyOnHidden
      items={tabItems}
    />
  );
}
