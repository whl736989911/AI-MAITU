/**
 * Personalization — the panels, for one agent at a time, of either kind.
 *
 * The active agent selects a caller-owned expert or an available feature.
 * Features the caller owns offer author configuration; shared features offer
 * only the memory tab, where their private memory and overlay are writable.
 * Shared experts remain outside this page's configuration scope.
 * Both agent bars and the rendered panels use the same active agent id.
 */

import { useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { GraduationCap } from "lucide-react";
import PageShell from "../../../layouts/PageShell";
import AgentScopeBars from "../../../components/AgentScopeBars";
import { EmptyStateIcon } from "../../../components/EmptyState";
import { useAgent } from "../../../context/AgentContext";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useUserRole } from "../../../hooks/useUserRole";
import { canManageExpert, ownedExperts } from "../../../utils/sharedExpert";
import { isFeatureAgent } from "../../../utils/agentKind";
import { personalizationTabAllowed } from "../../../utils/permissions";
import PersonalizationPanels, {
  FEATURE_PERSONALIZATION_TABS,
  PERSONALIZATION_TABS,
  TAB_ICONS,
  offeredTabs,
  type PersonalizationScope,
  type PersonalizationTab,
} from "./components/PersonalizationPanels";
// The experts' own "no expert of mine" placeholder and its styles: a caller
// without one is in the state the Experts page already describes, so it is that
// placeholder rather than a look-alike of it.
import emptyStyles from "../../Experts/index.module.less";

export default function PersonalizationPage() {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const isMobile = useIsMobile();
  const user = useCurrentUser();
  const { activeAgentId, agents } = useAgent();
  const ownExperts = useMemo(() => ownedExperts(agents), [agents]);
  /** A feature may be owned or shared; callers can personalize either one. */
  const pointedFeature = useMemo(
    () =>
      agents.find(
        (agent) => agent.agent_id === activeAgentId && isFeatureAgent(agent),
      ) ?? null,
    [agents, activeAgentId],
  );
  /** Shared experts are not configuration scopes; shared features are. */
  const activeAgent = useMemo(
    () =>
      pointedFeature ??
      ownExperts.find((a) => a.agent_id === activeAgentId) ??
      ownExperts[0] ??
      agents.find(isFeatureAgent) ??
      null,
    [pointedFeature, ownExperts, activeAgentId, agents],
  );
  // Who may write here is the server's rule for an agent — its owner, or an
  // administrator (``assert_agent_owner``) — and not ownership alone: a
  // administrator pointed at somebody else's expert gets ``is_owner`` false on
  // it and would be offered no panel at all, though the server accepts every
  // write. ``canManageExpert`` is that judgement, the same one the cards use.
  const role = useUserRole();
  const canWrite = activeAgent ? canManageExpert(activeAgent, role) : true;
  // A caller with no expert can still personalize their first shared feature.
  const scope: PersonalizationScope =
    activeAgent && isFeatureAgent(activeAgent) ? "feature" : "expert";

  /**
   * The tabs this scope offers this caller, from the panels' own table
   * (``offeredTabs``) — the same answer a feature's own page gets, so what the
   * row shows and what the stack below may render cannot disagree — narrowed
   * again by the module keys the tabs name (``personalizationTabAllowed``):
   * a tab whose key is not held is not offered at all (design §2.4).
   *
   * ``usePathTabs`` takes this list as the tabs the URL may name, so a refused
   * tab is not reachable by address either — the same one list answers both.
   */
  const offered = useMemo(() => {
    const tabs = offeredTabs(
      scope,
      scope === "feature" ? FEATURE_PERSONALIZATION_TABS : PERSONALIZATION_TABS,
      canWrite,
    );
    return tabs.filter((tab) => personalizationTabAllowed(user, tab));
  }, [scope, canWrite, user]);

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<PersonalizationTab>({
      basePath: "/personalization",
      // The tabs on offer are the tabs the URL may name: what this scope does not
      // offer is not reachable by address either.
      tabs: offered,
      storageKey: "octop:personalization:tab",
      // The first tab this scope offers: an expert's is its own default, and a
      // feature's is what is left of them for a caller who may not write
      // through the rest (``memory``, which the table never drops).
      defaultTab: offered[0] ?? "skills",
    });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: offered.map((value) => {
        const Icon = TAB_ICONS[value];
        return {
          value,
          label: t(`personalization.tabs.${value}`),
          icon: <Icon size={14} strokeWidth={2} />,
        };
      }),
    }),
    [activeTab, handleTabChange, offered, t],
  );

  const pageTitle = `${t("personalization.title")} / ${t(
    `personalization.tabs.${activeTab}`,
  )}`;

  return (
    <PageShell
      title={pageTitle}
      subtitle={t("personalization.description")}
      agentScoped
      // Shared features remain selectable for private memory even without
      // the feature-management module permission.
      agentBar={<AgentScopeBars includeSharedFeatures />}
      fill={!isMobile}
      pathTabs={pathTabs}
    >
      {activeAgent ? (
        <PersonalizationPanels
          agentId={activeAgent.agent_id}
          agentState={activeAgent.state}
          tabs={offered}
          activeTab={activeTab}
          isMounted={isMounted}
          scope={scope}
          canWrite={canWrite}
        />
      ) : (
        // No available expert or feature to configure yet.
        <div className={emptyStyles.emptyState}>
          <EmptyStateIcon icon={GraduationCap} />
          <div className={emptyStyles.emptyTitle}>
            {t("personalization.noExpertTitle")}
          </div>
          <div className={emptyStyles.emptyHint}>
            {t("personalization.noExpertHint")}
          </div>
          <div className={emptyStyles.emptyActions}>
            <button
              type="button"
              className={emptyStyles.emptyAction}
              onClick={() => navigate("/experts")}
            >
              {t("personalization.createExpert")}
            </button>
            <button
              type="button"
              className={emptyStyles.emptyAction}
              onClick={() => navigate("/features")}
            >
              {t("personalization.pickFeature")}
            </button>
          </div>
        </div>
      )}
    </PageShell>
  );
}
