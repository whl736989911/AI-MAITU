/**
 * Personalization — the expert panels, for one agent at a time.
 *
 * The page's scope is the caller's own active expert, and nothing else: the
 * agent bar is the shell's ``AgentSelector``. Every panel below is parameterised
 * on one agent id, so the scope is a single value handed to each of them:
 * nothing else about a panel changes, and there is no second set of panels to
 * keep in step.
 *
 * That id is the active agent as ``AgentContext`` holds it — the same id every
 * other agent-scoped surface acts on, chat included — *resolved among the
 * experts the caller owns*, which is the only thing this page may configure. The
 * two are not the same list: an agent is in the caller's list when somebody
 * shared it, and a feature's agent is in it from the moment the feature is
 * shared with them (``utils/agentKind``). Neither of those is an expert of the
 * caller's, and offering the writing panels over one would be offering controls
 * whose only outcome is a refusal — the bar's own selector, which offers the
 * caller's experts and no one else, already answers the question this page asks.
 *
 * A caller who owns no expert at all is therefore a state the page has to name,
 * not one it can leave to the bar (a bar with nothing to offer is not drawn):
 * the body says what it is waiting for and offers the two ways to get it —
 * create an expert of one's own, or open a feature.
 */

import { useCallback, useMemo } from "react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { GraduationCap } from "lucide-react";
import PageShell from "../../../layouts/PageShell";
import { EmptyStateIcon } from "../../../components/EmptyState";
import { useAgent } from "../../../context/AgentContext";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { ownedExperts } from "../../../utils/sharedExpert";
import { userCan } from "../../../utils/permissions";
import PersonalizationPanels, {
  PERSONALIZATION_TABS,
  TAB_ICONS,
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
  const activeAgent = useMemo(
    () =>
      ownExperts.find((a) => a.agent_id === activeAgentId) ?? ownExperts[0] ?? null,
    [ownExperts, activeAgentId],
  );
  // No expert of the caller's to configure. Every panel below is built from one
  // agent id, so with none the page is not "waiting for a choice" — it is
  // waiting for an expert to exist, and it says so with the two ways to get one:
  // create an expert of one's own, or open a feature (whose own agent is set up
  // by its author, not here).
  const hasOwnExpert = ownExperts.length > 0;

  const isAllowed = useCallback(
    (tab: PersonalizationTab) =>
      tab === "channels" ? userCan(user, "channels") : true,
    [user],
  );

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<PersonalizationTab>({
      basePath: "/personalization",
      tabs: PERSONALIZATION_TABS,
      storageKey: "octop:personalization:tab",
      defaultTab: "skills",
      isAllowed,
    });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: PERSONALIZATION_TABS.filter((value) => isAllowed(value)).map(
        (value) => {
          const Icon = TAB_ICONS[value];
          return {
            value,
            label: t(`personalization.tabs.${value}`),
            icon: <Icon size={14} strokeWidth={2} />,
          };
        },
      ),
    }),
    [activeTab, handleTabChange, isAllowed, t],
  );

  const pageTitle = `${t("personalization.title")} / ${t(
    `personalization.tabs.${activeTab}`,
  )}`;

  return (
    <PageShell
      title={pageTitle}
      subtitle={t("personalization.description")}
      agentScoped
      fill={!isMobile}
      pathTabs={pathTabs}
    >
      {hasOwnExpert ? (
        <PersonalizationPanels
          agentId={activeAgent?.agent_id ?? null}
          agentState={activeAgent?.state ?? "stopped"}
          tabs={PERSONALIZATION_TABS}
          activeTab={activeTab}
          isMounted={isMounted}
          scope="expert"
          // The caller's own expert: every panel is theirs to write.
          canWrite={activeAgent?.is_owner !== false}
        />
      ) : (
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
