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
import { userCan } from "../../../utils/permissions";
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
  const canManageFeatures = userCan(user, "features");

  const [features, setFeatures] = useState<FeatureSummary[]>([]);
  const [featuresLoading, setFeaturesLoading] = useState(false);
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

  // Only the features this caller may configure are offered: the row is the way
  // in to an app-owned agent, which is instance configuration.
  useEffect(() => {
    if (!canManageFeatures) return;
    let cancelled = false;
    setFeaturesLoading(true);
    featuresApi
      .listFeatures()
      .then((answer) => {
        if (!cancelled) setFeatures(answer.features);
      })
      .catch(() => {
        // Offering nothing is the honest answer to "which features may I
        // configure?" when the list could not be read; the agent bar comes back
        // as the ordinary one and no feature can be selected.
        if (!cancelled) setFeatures([]);
      })
      .finally(() => {
        if (!cancelled) setFeaturesLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [canManageFeatures]);

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
    if (featureId === null) {
      setFeatureAgent(null);
      setAgentFailure(null);
      return;
    }
    void openFeatureAgent(featureId);
  }, [featureId, openFeatureAgent]);

  // A feature nobody offered (no permission, or an id that is not in the list)
  // is not a scope this page can honour — it is dropped rather than shown as a
  // selected feature the panels cannot use.
  useEffect(() => {
    if (featureId === null || !canManageFeatures) return;
    if (featuresLoading || features.length === 0) return;
    if (!features.some((feature) => feature.id === featureId)) setFeatureId(null);
  }, [featureId, canManageFeatures, features, featuresLoading, setFeatureId]);

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
      canManageFeatures ? (
        <FeatureScopeBar
          features={features.map((feature) => ({
            id: feature.id,
            label: pickLocale(feature.label, lang),
          }))}
          featuresLoading={featuresLoading}
          selected={featureId}
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
          onRetry={() => featureId !== null && void openFeatureAgent(featureId)}
          retryLabel={t("features.settingsCapabilityRetry")}
        />
      ) : null,
    [
      agentFailure,
      agentLoading,
      canManageFeatures,
      featureAgent,
      featureId,
      features,
      featuresLoading,
      lang,
      openFeatureAgent,
      setFeatureId,
      t,
    ],
  );

  // A feature is selected but its agent is not ready: the surfaces are not
  // rendered at all, because every one of them would describe an agent that is
  // not there. Loading and failure are different screens, and neither is "empty".
  if (featureId !== null && scopeAgentId === null) {
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
          {agentLoading ? (
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
                  onClick={() => void openFeatureAgent(featureId)}
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
                  // Applying a type regenerates SOUL.md; on a feature's agent the
                  // soul file is edited on this same page, so the collision is
                  // said out loud where it happens.
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
