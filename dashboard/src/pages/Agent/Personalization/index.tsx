/**
 * Personalization — the panels, for one agent at a time, of either kind.
 *
 * The page's scope is the caller's own active agent — the id every other
 * agent-scoped surface acts on, chat included — *resolved among the agents this
 * page may configure*: the experts the caller owns, and the features they own
 * (``utils/sharedExpert``). Every panel below is parameterised on one agent id,
 * so the scope is a single value handed to each of them: nothing else about a
 * panel changes, and there is no second set of panels to keep in step.
 *
 * Those two are the bars' own lists (``AgentScopeBars`` — the experts' row and
 * the features' row, over ``activeAgentId``, which is also a feature's to hold):
 * what the page acts on is therefore always something one of its own rows can
 * name. An agent of either kind is configured here, and which kind it is decides
 * the scope — ``PersonalizationPanels``' policy table says the rest, so a
 * feature's own tab set (its persona files included, since its agent is in
 * nobody's expert list) comes along with it.
 *
 * An expert still resolves exactly as it always has: the one aimed at, else the
 * caller's first. A feature the caller owns resolves to itself. The two are not
 * the same list, and a pointer on neither — an agent somebody shared, which
 * ``utils/agentKind`` shows to be either kind — falls back where it always did,
 * to the caller's first own expert: a shared agent is read-only for the caller,
 * so the panels would be offered over something the server refuses to write.
 *
 * A caller who owns no expert of their own and has no feature aimed at is
 * therefore a state the page has to name, not one it can leave to the bar (a bar
 * with nothing to offer is not drawn): the body says what it is waiting for and
 * offers the two ways to get it — create an expert of one's own, or open a
 * feature.
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
import {
  canManageExpert,
  ownedExperts,
  ownedFeatures,
} from "../../../utils/sharedExpert";
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
  const ownFeatures = useMemo(() => ownedFeatures(agents), [agents]);
  /** The pointer, when it is on a feature of the caller's — the features' row's own list. */
  const pointedFeature = useMemo(
    () => ownFeatures.find((a) => a.agent_id === activeAgentId) ?? null,
    [ownFeatures, activeAgentId],
  );
  /**
   * What the panels below are about: that feature, else the experts' own
   * resolution, which is the expression it has always been. The pointer is
   * resolved against the bars' lists rather than taken as it comes: an agent
   * somebody shared is not the caller's to configure here, of either kind.
   */
  const activeAgent = useMemo(
    () =>
      pointedFeature ??
      ownExperts.find((a) => a.agent_id === activeAgentId) ??
      ownExperts[0] ??
      null,
    [pointedFeature, ownExperts, activeAgentId],
  );
  // Who may write here is the server's rule for an agent — its owner, or an
  // administrator (``assert_agent_owner``) — and not ownership alone: a
  // administrator pointed at somebody else's expert gets ``is_owner`` false on
  // it and would be offered no panel at all, though the server accepts every
  // write. ``canManageExpert`` is that judgement, the same one the cards use.
  const role = useUserRole();
  const canWrite = activeAgent ? canManageExpert(activeAgent, role) : true;
  // The scope says which kind the agent is.
  const scope: PersonalizationScope = pointedFeature ? "feature" : "expert";

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
      // Both kinds are a scope of this page's content, so both rows are drawn —
      // each one by its own option set, so an expert-only caller sees the single
      // row this page has always had.
      agentBar={<AgentScopeBars />}
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
        // No expert of the caller's and no feature of theirs aimed at: every
        // panel is built from one agent id, so this is not "waiting for a
        // choice" — it is waiting for an agent of one's own to exist, and it
        // says so with the two ways to get one.
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
