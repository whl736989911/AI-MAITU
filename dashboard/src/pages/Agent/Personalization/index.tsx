/**
 * Personalization — the expert panels, for one agent at a time.
 *
 * The page's scope is either the caller's own active expert or, chosen in the
 * feature row above the agent bar, a *feature's* agent (design 5.1). Every panel
 * below is parameterised on one agent id, so the scope is a single value handed
 * to each of them: nothing else about a panel changes, and there is no second set
 * of panels to keep in step.
 *
 * The feature scope is page-local on purpose. ``setActiveAgent`` writes
 * localStorage and pipes ``X-Octop-Agent-Id`` into every agent-scoped request —
 * chat included — and ``AgentSelector`` re-selects the first owned expert
 * whenever the current id is not one of the caller's, which a feature's
 * app-owned agent never is. So the active agent stays the caller's, and only the
 * id the panels receive switches.
 *
 * The row offers the definitions that call accepts, and no others: the catalog
 * minus ``_meta``'s ``bundled_ids``, which is the store's own writability test —
 * the very one that refuses with ``reason: "bundled"``. A shipped definition is
 * absent from the picker rather than greyed out (an option whose only outcome is
 * a refusal is a dead end, not a choice), and an id the picker does not offer is
 * dropped from the URL instead of being opened.
 *
 * The id itself comes from the server: ``POST /features/{id}/agent`` (idempotent:
 * it is the "open the personalization surface" call) answers with the agent, and
 * the status endpoint answers with its runtime state. Neither is derived from the
 * feature id here, and a failure to get them is shown instead of panels pointed
 * at the wrong agent — an empty skills list would read as "this agent has none".
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router-dom";
import { Alert, Button, Empty, Spin } from "antd";
import {
  Bot,
  Brain,
  FileText,
  Notebook,
  Puzzle,
  RefreshCw,
  Sparkles,
  Waypoints,
  Wrench,
} from "lucide-react";
import PageShell, { pageShellStyles } from "../../../layouts/PageShell";
import { useAgent } from "../../../context/AgentContext";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { isSystemAdmin, userCan } from "../../../utils/permissions";
import { apiErrorMessage } from "../../../utils/apiError";
import {
  featuresApi,
  type FeatureSummary,
} from "../../../api/modules/features";
import { octopAgentsApi } from "../../../api/modules/octopAgents";
import { pickLocale } from "../../../utils/localizedText";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import SkillsTabs from "../Skills/components/SkillsTabs";
import ToolsTabs from "../Tools/ToolsTabs";
import SubagentManager from "../../Experts/components/SubagentManager";
import MBTISelector from "./components/MBTISelector";
import AgentPluginsPanel from "./components/AgentPluginsPanel";
import AgentPersonaFiles from "./components/AgentPersonaFiles";
import FeatureScopeBar from "./components/FeatureScopeBar";
import MemoryPanel from "../Memory/MemoryPanel";
import ChannelsPanel from "../Channels/ChannelsPanel";
import styles from "./index.module.less";

export type PersonalizationTab =
  | "skills"
  | "subagents"
  | "tools"
  | "plugins"
  | "mbti"
  | "memory"
  | "channels"
  | "files";

const PERSONALIZATION_TABS = [
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
 * in nobody's expert list, so this page is the only place they can be reached.
 */
const FEATURE_ONLY_TAB: PersonalizationTab = "files";

const TAB_ICONS = {
  skills: Sparkles,
  subagents: Bot,
  tools: Wrench,
  plugins: Puzzle,
  mbti: Brain,
  memory: Notebook,
  channels: Waypoints,
  files: FileText,
} as const;

/** The query parameter that names the feature this page is scoped to. */
const FEATURE_PARAM = "feature";

export default function PersonalizationPage() {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const user = useCurrentUser();
  const { activeAgentId, agents } = useAgent();
  const activeAgent = agents.find((a) => a.agent_id === activeAgentId);
  const [searchParams, setSearchParams] = useSearchParams();
  const lang = normalizeUiLocale(i18n.language);

  const featureId = searchParams.get(FEATURE_PARAM);

  /**
   * Giving a definition an agent of its own is instance configuration: the call
   * behind the row is admin-gated, while ``features`` on its own is the *run*
   * permission. A caller holding only that permission would be refused at every
   * pick, so they are not shown the row at all.
   */
  const canPersonalizeFeature = isSystemAdmin(user);

  /**
   * The definitions this caller may actually personalize: ``null`` until the
   * server has answered.
   *
   * One question, and it takes two calls to answer it: which definitions exist
   * (``GET /features``) and which of them this instance will write (``_meta``'s
   * ``bundled_ids``, the store's own writability test — the very test the server
   * refuses a personalization on). The row must offer the same set the call
   * accepts, so a bundled definition is *absent* from it rather than greyed out:
   * a control whose only outcome is a refusal is a dead end, not a choice.
   */
  const [offered, setOffered] = useState<FeatureSummary[] | null>(null);
  /** The selected feature's own agent, as the server answered for it. */
  const [featureAgent, setFeatureAgent] = useState<{
    agent_id: string;
    state: string;
  } | null>(null);
  const [agentLoading, setAgentLoading] = useState(false);
  const [agentFailure, setAgentFailure] = useState<unknown>(null);

  const setFeatureId = useCallback(
    (next: string | null) => {
      const params = new URLSearchParams(searchParams);
      if (next === null) params.delete(FEATURE_PARAM);
      else params.set(FEATURE_PARAM, next);
      setSearchParams(params, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  useEffect(() => {
    if (!canPersonalizeFeature) return;
    let cancelled = false;
    Promise.all([featuresApi.listFeatures(), featuresApi.getFeatureMeta()])
      .then(([catalog, meta]) => {
        if (cancelled) return;
        const shipped = new Set(meta.bundled_ids);
        setOffered(
          catalog.features.filter((feature) => !shipped.has(feature.id)),
        );
      })
      .catch(() => {
        // Offering nothing is the honest answer to "which features may I
        // personalize?" when the answer could not be read; the agent bar comes
        // back as the ordinary one and no feature can be selected. An unanswered
        // ``bundled_ids`` in particular would offer exactly the ones the server
        // refuses, so half an answer is no answer.
        if (!cancelled) setOffered([]);
      });
    return () => {
      cancelled = true;
    };
  }, [canPersonalizeFeature]);

  /**
   * The scope this page acts on: the id in the URL, once the answer names it.
   * Until then there is nothing to open — and for a bundled definition there
   * never will be, because the call that opens it is the one that gets refused.
   */
  const scopedFeatureId = useMemo(
    () =>
      featureId !== null && offered?.some((feature) => feature.id === featureId)
        ? featureId
        : null,
    [featureId, offered],
  );

  /**
   * Materialize (or re-open) the selected feature's agent, and read the state the
   * server records for it. Both are needed before a panel may mount: a panel that
   * renders without a live agent answers "nothing installed, nothing configured".
   */
  const openFeatureAgent = useCallback(async (id: string) => {
    setAgentLoading(true);
    try {
      const personalization = await featuresApi.personalizeFeature(id);
      const { state } = await octopAgentsApi.getAgentStatus(
        personalization.agent_id,
      );
      setFeatureAgent({ agent_id: personalization.agent_id, state });
      setAgentFailure(null);
    } catch (err) {
      setFeatureAgent(null);
      setAgentFailure(err);
    } finally {
      setAgentLoading(false);
    }
  }, []);

  useEffect(() => {
    if (scopedFeatureId === null) {
      setFeatureAgent(null);
      setAgentFailure(null);
      return;
    }
    void openFeatureAgent(scopedFeatureId);
  }, [scopedFeatureId, openFeatureAgent]);

  // A definition the answer did not name is not a scope this page can honour — it
  // is dropped rather than shown as a selected feature the panels cannot use.
  // ``offered === null`` drops nothing: "not answered yet" is not "not offered".
  useEffect(() => {
    if (featureId === null) return;
    if (!canPersonalizeFeature) {
      setFeatureId(null);
      return;
    }
    if (offered !== null && scopedFeatureId === null) setFeatureId(null);
  }, [
    canPersonalizeFeature,
    featureId,
    offered,
    scopedFeatureId,
    setFeatureId,
  ]);

  const tabs = useMemo<readonly PersonalizationTab[]>(
    () =>
      featureId === null
        ? PERSONALIZATION_TABS
        : [...PERSONALIZATION_TABS, FEATURE_ONLY_TAB],
    [featureId],
  );

  const isAllowed = useCallback(
    (tab: PersonalizationTab) => {
      if (tab === FEATURE_ONLY_TAB) return featureId !== null;
      if (tab === "channels") return userCan(user, "channels");
      return true;
    },
    [featureId, user],
  );

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<PersonalizationTab>({
      basePath: "/personalization",
      tabs,
      storageKey: "octop:personalization:tab",
      defaultTab: "skills",
      isAllowed,
    });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: tabs
        .filter((value) => isAllowed(value))
        .map((value) => {
          const Icon = TAB_ICONS[value];
          return {
            value,
            label: t(`personalization.tabs.${value}`),
            icon: <Icon size={14} strokeWidth={2} />,
          };
        }),
    }),
    [activeTab, handleTabChange, isAllowed, t, tabs],
  );

  /**
   * The one id every panel below is built from. Without a feature it is the
   * caller's active expert, exactly as this page has always been; with one it is
   * that feature's agent and *only* that — falling back to the caller's agent
   * while the feature's is being opened would configure the wrong agent.
   */
  const scopeAgentId = featureId === null ? activeAgentId : featureAgent?.agent_id ?? null;
  const scopeAgentState =
    featureId === null ? activeAgent?.state ?? "stopped" : featureAgent?.state ?? "";

  const pageTitle = `${t("personalization.title")} / ${t(
    `personalization.tabs.${activeTab}`,
  )}`;

  const scopeBar = useMemo(
    () =>
      canPersonalizeFeature ? (
        <FeatureScopeBar
          features={(offered ?? []).map((feature) => ({
            id: feature.id,
            label: pickLocale(feature.label, lang),
          }))}
          // ``offered === null`` is the whole of "still asking": the two calls
          // behind the row are one answer, and until they answer nothing may be
          // picked — least of all a definition the server would refuse.
          featuresLoading={offered === null}
          selected={scopedFeatureId}
          onSelect={setFeatureId}
          agentId={featureAgent?.agent_id ?? null}
          agentState={featureAgent?.state ?? null}
          loading={agentLoading}
          failure={
            agentFailure === null
              ? null
              : apiErrorMessage(
                  agentFailure,
                  t("features.scopeFeatureAgentFailed"),
                  t,
                )
          }
          onRetry={() =>
            scopedFeatureId !== null && void openFeatureAgent(scopedFeatureId)
          }
          retryLabel={t("features.settingsCapabilityRetry")}
        />
      ) : null,
    [
      agentFailure,
      agentLoading,
      canPersonalizeFeature,
      featureAgent,
      lang,
      offered,
      openFeatureAgent,
      scopedFeatureId,
      setFeatureId,
      t,
    ],
  );

  // A feature is selected but its agent is not ready: the surfaces are not
  // rendered at all, because every one of them would describe an agent that is
  // not there. Loading and failure are different screens, and neither is "empty".
  if (featureId !== null && scopeAgentId === null) {
    // No scope to open is not a failure to report: the answer has not arrived
    // yet, or it arrived and did not name this definition — which the effect
    // above turns into a URL without it. Both are the same waiting screen.
    const settling = scopedFeatureId === null || agentLoading;
    return (
      <PageShell
        title={pageTitle}
        subtitle={t("personalization.description")}
        agentScoped
        agentBar={scopeBar}
        fill={!isMobile}
        pathTabs={pathTabs}
      >
        <div className={styles.scopeStatus}>
          {settling ? (
            <>
              <Spin size="small" />
              <span>{t("features.scopeFeatureAgentLoading")}</span>
            </>
          ) : (
            <Alert
              type="error"
              showIcon
              message={t("features.scopeFeatureAgentFailed")}
              description={apiErrorMessage(
                agentFailure,
                t("features.scopeFeatureAgentFailed"),
                t,
              )}
              action={
                <Button
                  size="small"
                  icon={<RefreshCw size={13} />}
                  onClick={() => void openFeatureAgent(scopedFeatureId)}
                >
                  {t("features.settingsCapabilityRetry")}
                </Button>
              }
            />
          )}
        </div>
      </PageShell>
    );
  }

  return (
    <PageShell
      title={pageTitle}
      subtitle={t("personalization.description")}
      agentScoped
      agentBar={scopeBar}
      fill={!isMobile}
      pathTabs={pathTabs}
    >
      <div className={styles.panels}>
        {isMounted("skills") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "skills" ? "flex" : "none" }}
            aria-hidden={activeTab !== "skills"}
          >
            <div className={pageShellStyles.fillChild}>
              <SkillsTabs agentId={scopeAgentId} />
            </div>
          </div>
        )}

        {isMounted("tools") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "tools" ? "flex" : "none" }}
            aria-hidden={activeTab !== "tools"}
          >
            <div className={pageShellStyles.fillChild}>
              <ToolsTabs agentId={scopeAgentId} />
            </div>
          </div>
        )}

        {isMounted("plugins") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "plugins" ? "flex" : "none" }}
            aria-hidden={activeTab !== "plugins"}
          >
            <div className={pageShellStyles.fillChild}>
              <AgentPluginsPanel agentId={scopeAgentId} />
            </div>
          </div>
        )}

        {isMounted("subagents") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "subagents" ? "flex" : "none" }}
            aria-hidden={activeTab !== "subagents"}
          >
            {!scopeAgentId ? (
              <Empty
                style={{ marginTop: isMobile ? 48 : 24 }}
                description={t("subagents.pickAgent")}
              />
            ) : (
              <SubagentManager
                key={scopeAgentId}
                agentId={scopeAgentId}
                agentState={scopeAgentState}
                fillHeight={isMobile}
              />
            )}
          </div>
        )}

        {isMounted("mbti") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "mbti" ? "flex" : "none" }}
            aria-hidden={activeTab !== "mbti"}
          >
            {!scopeAgentId ? (
              <Empty
                style={{ marginTop: 24 }}
                description={t("mbtiPage.pickAgent")}
              />
            ) : (
              <div className={pageShellStyles.fillChild}>
                {featureId !== null && (
                  // Applying a type writes this agent's system prompt and nothing
                  // in the workspace: the persona files edited on this same page
                  // (SOUL.md among them) are left alone. What *is* shared is the
                  // agent itself, so it is said where that is true.
                  <Alert
                    type="info"
                    showIcon
                    style={{ marginBottom: 12 }}
                    message={t("features.personalizationMbtiNote")}
                  />
                )}
                <MBTISelector
                  key={scopeAgentId}
                  agentId={scopeAgentId}
                  showHeader={false}
                  showTestAction
                />
              </div>
            )}
          </div>
        )}

        {isMounted("memory") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "memory" ? "flex" : "none" }}
            aria-hidden={activeTab !== "memory"}
          >
            {featureId !== null && (
              // One agent means one MEMORY.md, and this agent serves every
              // caller: the file is shared, not scoped. The panel is still
              // usable — what it must not do is read as "my own memory".
              <Alert
                type="warning"
                showIcon
                style={{ marginBottom: 12 }}
                message={t("personalization.memorySharedNote")}
              />
            )}
            {isMobile ? (
              <MemoryPanel agentId={scopeAgentId} fill={false} />
            ) : (
              <div className={pageShellStyles.fillChild}>
                <MemoryPanel agentId={scopeAgentId} fill />
              </div>
            )}
          </div>
        )}

        {isMounted("channels") && (
          <div
            className={styles.panel}
            style={{ display: activeTab === "channels" ? "flex" : "none" }}
            aria-hidden={activeTab !== "channels"}
          >
            <div className={pageShellStyles.fillChild}>
              <ChannelsPanel agentId={scopeAgentId} />
            </div>
          </div>
        )}

        {isMounted(FEATURE_ONLY_TAB) && scopeAgentId !== null && (
          <div
            className={styles.panel}
            style={{
              display: activeTab === FEATURE_ONLY_TAB ? "flex" : "none",
            }}
            aria-hidden={activeTab !== FEATURE_ONLY_TAB}
          >
            <AgentPersonaFiles agentId={scopeAgentId} />
          </div>
        )}
      </div>
    </PageShell>
  );
}
