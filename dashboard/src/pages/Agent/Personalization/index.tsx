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
import { useNavigate, useSearchParams } from "react-router-dom";
import { Alert, Button, Spin } from "antd";
import { RefreshCw } from "lucide-react";
import PageShell from "../../../layouts/PageShell";
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
import { pickLocale } from "../../../utils/localizedText";
import { normalizeUiLocale } from "../../../utils/localePrefs";
import { useFeatureAgent } from "../../../hooks/useFeatureAgent";
import FeatureScopeBar from "./components/FeatureScopeBar";
import PersonalizationPanels, {
  FEATURE_ONLY_TAB,
  PERSONALIZATION_TABS,
  TAB_ICONS,
  type PersonalizationTab,
} from "./components/PersonalizationPanels";
import styles from "./index.module.less";

/** The query parameter that names the feature this page is scoped to. */
const FEATURE_PARAM = "feature";

export default function PersonalizationPage() {
  const { t, i18n } = useTranslation();
  const isMobile = useIsMobile();
  const user = useCurrentUser();
  const { activeAgentId, agents } = useAgent();
  const activeAgent = agents.find((a) => a.agent_id === activeAgentId);
  const [searchParams, setSearchParams] = useSearchParams();
  const navigate = useNavigate();
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
   * The selected feature's own agent: materialized (or re-opened) and read from
   * the server, both before any panel may mount. The feature's own page opens it
   * through the same hook, so the two surfaces cannot disagree about how a
   * feature's agent is reached.
   */
  const featureAgent = useFeatureAgent(scopedFeatureId);

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
  const scopeAgentId =
    featureId === null ? activeAgentId : featureAgent.agentId;
  const scopeAgentState =
    featureId === null ? activeAgent?.state ?? "stopped" : featureAgent.state;

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
          agentId={featureAgent.agentId}
          agentState={featureAgent.agentId === null ? null : featureAgent.state}
          loading={featureAgent.loading}
          failure={
            featureAgent.failure === null
              ? null
              : apiErrorMessage(
                  featureAgent.failure,
                  t("features.scopeFeatureAgentFailed"),
                  t,
                )
          }
          onRetry={featureAgent.retry}
          retryLabel={t("features.settingsCapabilityRetry")}
          // The catalog is where a definition is created, which is the only way
          // to get one this page can offer.
          onCreateFeature={() => navigate("/features")}
        />
      ) : null,
    [
      canPersonalizeFeature,
      featureAgent,
      lang,
      navigate,
      offered,
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
    const settling = scopedFeatureId === null || featureAgent.loading;
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
                featureAgent.failure,
                t("features.scopeFeatureAgentFailed"),
                t,
              )}
              action={
                <Button
                  size="small"
                  icon={<RefreshCw size={13} />}
                  onClick={featureAgent.retry}
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
      <PersonalizationPanels
        agentId={scopeAgentId}
        agentState={scopeAgentState}
        tabs={tabs}
        activeTab={activeTab}
        isMounted={isMounted}
        scope={featureId === null ? "expert" : "feature"}
        // A feature's agent is instance configuration, so this page only ever
        // reaches one as an administrator — which is the same person the
        // definition's own write endpoints accept, and the writer the policy
        // table asks about.
        canWrite={featureId === null || canPersonalizeFeature}
      />
    </PageShell>
  );
}
