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
 * other agent-scoped surface acts on, chat included.
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import { useAgent } from "../../../context/AgentContext";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { userCan } from "../../../utils/permissions";
import PersonalizationPanels, {
  PERSONALIZATION_TABS,
  TAB_ICONS,
  type PersonalizationTab,
} from "./components/PersonalizationPanels";

export default function PersonalizationPage() {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const user = useCurrentUser();
  const { activeAgentId, agents } = useAgent();
  const activeAgent = agents.find((a) => a.agent_id === activeAgentId);

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
      <PersonalizationPanels
        agentId={activeAgentId}
        agentState={activeAgent?.state ?? "stopped"}
        tabs={PERSONALIZATION_TABS}
        activeTab={activeTab}
        isMounted={isMounted}
        scope="expert"
        // The caller's own expert: every panel is theirs to write.
        canWrite
      />
    </PageShell>
  );
}
