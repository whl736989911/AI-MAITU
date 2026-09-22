/**
 * The personalization panels — the experts' own surfaces, every one of them
 * parameterised on a single agent id — and the one place that says what a
 * capability *is* in the scope showing it.
 *
 * One stack, two pages. The Personalization page picks the scope at the top of
 * the page (the caller's own active expert, or a feature's agent); a feature's
 * own page has its scope fixed by the route and shows the same stack. Neither
 * page owns a panel of its own, so what a feature configures cannot drift from
 * what an expert configures: the components, their order, their icons and their
 * keep-alive are all this list.
 *
 * What *does* differ between the two is declared in ``CAPABILITY_POLICY`` below
 * rather than branched into the panels: a capability whose writing rule, or
 * whose explanation, is not the same for an expert as for a feature is a row in
 * that table. Two axes meet there —
 *
 *   - the scope: whose agent this is (``expert`` or ``feature``), and
 *   - the writer: who is allowed to write through the panel. A caller of a
 *     feature may not (design: the configuration belongs to whoever defines the
 *     feature), and the feature's memory is written by nobody at all, because
 *     one file serves every caller and a run must not fold one caller's
 *     experience into the next one's.
 *
 * A page that shows these panels says which of the two it is showing, and hands
 * ``canWrite`` the answer it already had to know: for a feature that is the
 * server's own rule — ``POST /features/{id}/agent`` is administrator-gated, and
 * a caller is not shown the surface at all.
 */

import { Alert, Empty } from "antd";
import {
  Bot,
  Brain,
  FileText,
  Notebook,
  Puzzle,
  Sparkles,
  Waypoints,
  Wrench,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { useIsMobile } from "../../../../hooks/useIsMobile";
import { pageShellStyles } from "../../../../layouts/PageShell";
import SkillsTabs from "../../Skills/components/SkillsTabs";
import ToolsTabs from "../../Tools/ToolsTabs";
import SubagentManager from "../../../Experts/components/SubagentManager";
import MemoryPanel from "../../Memory/MemoryPanel";
import ChannelsPanel from "../../Channels/ChannelsPanel";
import MBTISelector from "./MBTISelector";
import AgentPluginsPanel from "./AgentPluginsPanel";
import AgentPersonaFiles from "./AgentPersonaFiles";
import styles from "../index.module.less";

export type PersonalizationTab =
  | "skills"
  | "subagents"
  | "tools"
  | "plugins"
  | "mbti"
  | "memory"
  | "channels"
  | "files";

/** Every tab an expert has, in the order the page shows them. */
export const PERSONALIZATION_TABS = [
  "skills",
  "subagents",
  "tools",
  "plugins",
  "mbti",
  "memory",
  "channels",
] as const satisfies readonly PersonalizationTab[];

/**
 * The persona files belong to the feature's own agent: an expert's are edited in
 * their own profile (``EditAgentDrawer``'s 配置文件), while a feature's agent is
 * in nobody's expert list, so a feature's page is the only place they can be
 * reached.
 */
export const FEATURE_ONLY_TAB: PersonalizationTab = "files";

/** What a page whose scope is a feature shows: the same set, plus its own files. */
export const FEATURE_PERSONALIZATION_TABS = [
  ...PERSONALIZATION_TABS,
  FEATURE_ONLY_TAB,
] as const satisfies readonly PersonalizationTab[];

/**
 * Who may write through one capability, in one scope.
 *
 *   ``viewer`` — whoever is looking at it (the expert's own owner).
 *   ``author`` — only whoever may configure this definition; a caller is shown
 *                what it holds and nothing that writes.
 *   ``nobody`` — no one: what is there is what every run reads.
 */
export type CapabilityWriter = "viewer" | "author" | "nobody";

/** Whether the panels are the caller's own (``expert``) or a feature's. */
export type PersonalizationScope = "expert" | "feature";

/** One capability's answer for one scope: who writes it, and what to say first. */
export interface CapabilityPolicy {
  writer: CapabilityWriter;
  /** The note above the panel, as an i18n key — *why* it behaves this way here. */
  note?: { tone: "info" | "warning"; message: string };
}

/**
 * The policy table — every difference between configuring an expert and
 * configuring a feature, in one place. A capability that is the same in both
 * scopes has no entry for the scope that does not change it, and inherits the
 * panels' own default: the viewer writes it, and there is nothing to explain.
 */
export const CAPABILITY_POLICY: Record<
  PersonalizationScope,
  Partial<Record<PersonalizationTab, CapabilityPolicy>>
> = {
  // An expert is the caller's own: every panel is theirs to write, as it always
  // has been.
  expert: {},
  feature: {
    // The configuration of a feature belongs to whoever defines it. A caller
    // reads the definition's behaviour by running it, and the panels that write
    // are not offered to them — a control whose only outcome is a refusal is a
    // dead end, not a choice.
    skills: { writer: "author" },
    subagents: { writer: "author" },
    tools: { writer: "author" },
    plugins: { writer: "author" },
    channels: { writer: "author" },
    files: { writer: "author" },
    mbti: {
      writer: "author",
      // Applying a type writes this agent's system prompt and nothing in the
      // workspace, and the agent is shared by every caller, so it is said where
      // that is true.
      note: { tone: "info", message: "personalization.personalizationMbtiNote" },
    },
    memory: {
      // Nobody writes it. One agent means one MEMORY.md and every caller runs
      // on it, so a run must not carry one caller's experience into the next —
      // and neither may a person, or the file stops meaning what the design
      // says it means. The panel stays: what it holds is what a run reads.
      writer: "nobody",
      note: { tone: "warning", message: "personalization.memoryFeatureNote" },
    },
  },
};

export const TAB_ICONS = {
  skills: Sparkles,
  subagents: Bot,
  tools: Wrench,
  plugins: Puzzle,
  mbti: Brain,
  memory: Notebook,
  channels: Waypoints,
  files: FileText,
} as const;

export interface PersonalizationPanelsProps {
  /** The one id every panel below is built from. */
  agentId: string | null;
  /** Its runtime state, for the panel that has to know before it starts anything. */
  agentState: string;
  /** Tabs to render, in order. */
  tabs: readonly PersonalizationTab[];
  /** The tab on screen; the rest stay mounted and hidden. */
  activeTab: PersonalizationTab;
  /** Whether a tab has ever been opened. The panels mount lazily and stay. */
  isMounted: (tab: PersonalizationTab) => boolean;
  /** Whether this is the caller's own expert, or a feature's agent. */
  scope: PersonalizationScope;
  /**
   * Whether the caller may write through these panels at all — for a feature,
   * whether they are the one configuring it. What it does not decide is whether
   * the panels write *in principle*: that is the table's ``writer``, and a
   * capability marked ``nobody`` is read-only for an author too.
   */
  canWrite: boolean;
}

export default function PersonalizationPanels({
  agentId,
  agentState,
  tabs,
  activeTab,
  isMounted,
  scope,
  canWrite,
}: PersonalizationPanelsProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const policy = CAPABILITY_POLICY[scope];
  /** A panel writes when the scope lets this writer, and nobody else may. */
  const writesFor = (rule: CapabilityPolicy | undefined) => {
    const writer = rule?.writer ?? "viewer";
    if (writer === "nobody") return false;
    if (writer === "author") return canWrite;
    return true;
  };
  const memoryRule = policy.memory;
  const mbtiRule = policy.mbti;
  const note = (rule: CapabilityPolicy | undefined) =>
    rule?.note ? (
      <Alert
        type={rule.note.tone}
        showIcon
        style={{ marginBottom: 12 }}
        message={t(rule.note.message)}
      />
    ) : null;

  return (
    <div className={styles.panels}>
      {isMounted("skills") && tabs.includes("skills") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "skills" ? "flex" : "none" }}
          aria-hidden={activeTab !== "skills"}
        >
          <div className={pageShellStyles.fillChild}>
            <SkillsTabs agentId={agentId} />
          </div>
        </div>
      )}

      {isMounted("tools") && tabs.includes("tools") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "tools" ? "flex" : "none" }}
          aria-hidden={activeTab !== "tools"}
        >
          <div className={pageShellStyles.fillChild}>
            <ToolsTabs agentId={agentId} />
          </div>
        </div>
      )}

      {isMounted("plugins") && tabs.includes("plugins") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "plugins" ? "flex" : "none" }}
          aria-hidden={activeTab !== "plugins"}
        >
          <div className={pageShellStyles.fillChild}>
            <AgentPluginsPanel agentId={agentId} />
          </div>
        </div>
      )}

      {isMounted("subagents") && tabs.includes("subagents") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "subagents" ? "flex" : "none" }}
          aria-hidden={activeTab !== "subagents"}
        >
          {!agentId ? (
            <Empty
              style={{ marginTop: isMobile ? 48 : 24 }}
              description={t("subagents.pickAgent")}
            />
          ) : (
            <SubagentManager
              key={agentId}
              agentId={agentId}
              agentState={agentState}
              fillHeight={isMobile}
            />
          )}
        </div>
      )}

      {isMounted("mbti") && tabs.includes("mbti") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "mbti" ? "flex" : "none" }}
          aria-hidden={activeTab !== "mbti"}
        >
          {!agentId ? (
            <Empty
              style={{ marginTop: 24 }}
              description={t("mbtiPage.pickAgent")}
            />
          ) : (
            <div className={pageShellStyles.fillChild}>
              {note(mbtiRule)}
              <MBTISelector
                key={agentId}
                agentId={agentId}
                showHeader={false}
                showTestAction
              />
            </div>
          )}
        </div>
      )}

      {isMounted("memory") && tabs.includes("memory") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "memory" ? "flex" : "none" }}
          aria-hidden={activeTab !== "memory"}
        >
          {note(memoryRule)}
          {isMobile ? (
            <MemoryPanel
              agentId={agentId}
              fill={false}
              readOnly={!writesFor(memoryRule)}
            />
          ) : (
            <div className={pageShellStyles.fillChild}>
              <MemoryPanel
                agentId={agentId}
                fill
                readOnly={!writesFor(memoryRule)}
              />
            </div>
          )}
        </div>
      )}

      {isMounted("channels") && tabs.includes("channels") && (
        <div
          className={styles.panel}
          style={{ display: activeTab === "channels" ? "flex" : "none" }}
          aria-hidden={activeTab !== "channels"}
        >
          <div className={pageShellStyles.fillChild}>
            <ChannelsPanel agentId={agentId} />
          </div>
        </div>
      )}

      {isMounted(FEATURE_ONLY_TAB) && tabs.includes(FEATURE_ONLY_TAB) && agentId !== null && (
        <div
          className={styles.panel}
          style={{
            display: activeTab === FEATURE_ONLY_TAB ? "flex" : "none",
          }}
          aria-hidden={activeTab !== FEATURE_ONLY_TAB}
        >
          <AgentPersonaFiles agentId={agentId} />
        </div>
      )}
    </div>
  );
}
