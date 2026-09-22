/**
 * A feature's capabilities — the experts' own panels, pointed at the agent the
 * feature runs on.
 *
 * Nothing here is a second implementation of them: the stack is
 * ``PersonalizationPanels``, the tab row is ``PathTabsSegmented`` (the same control
 * the Personalization page's row is), and the choice of tab lives in the URL the
 * way it does there. What this module owns is the scope: which agent the panels
 * are about, which of them the caller may write through, and which are therefore
 * offered at all.
 *
 * **Which tabs a caller gets is the policy table's answer, not this file's** —
 * ``offeredTabs`` reads ``CAPABILITY_POLICY`` (``PersonalizationPanels``) and drops
 * every capability whose writer is the author's when the caller is not the author.
 * A feature is configured by whoever defined it; for everyone else those panels are
 * not shown rather than shown dead, and what remains is what a caller is entitled
 * to read — its memory, read-only, with the panel's own note saying why it never
 * changes.
 */

import { useCallback, useMemo } from "react";
import { useTranslation } from "react-i18next";

import { PathTabsSegmented } from "../../../layouts/PageShell";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { userCan } from "../../../utils/permissions";
import type { OctopAgent } from "../../../context/AgentContext";
import PersonalizationPanels, {
  FEATURE_PERSONALIZATION_TABS,
  TAB_ICONS,
  offeredTabs,
  type PersonalizationTab,
} from "../../Agent/Personalization/components/PersonalizationPanels";
import styles from "../index.module.less";

export interface FeaturePersonalizationPanelProps {
  /** The feature whose agent these panels configure. */
  feature: OctopAgent;
  /** Whether the caller may write through them — its author may. */
  canWrite: boolean;
}

export default function FeaturePersonalizationPanel({
  feature,
  canWrite,
}: FeaturePersonalizationPanelProps) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const user = useCurrentUser();

  // Channels reach the caller's own entry points, not the feature's, so the
  // permission that gates them gates this row too.
  const channelsAllowed = userCan(user, "channels");

  const offered = useMemo(() => {
    const tabs = offeredTabs("feature", FEATURE_PERSONALIZATION_TABS, canWrite);
    return channelsAllowed ? tabs : tabs.filter((tab) => tab !== "channels");
  }, [canWrite, channelsAllowed]);

  const isAllowed = useCallback(
    (tab: PersonalizationTab) => offered.includes(tab),
    [offered],
  );

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<PersonalizationTab>({
      basePath: `/features/${feature.agent_id}/personalization`,
      tabs: offered,
      storageKey: "octop:features:personalization:tab",
      // The first tab on offer, which is the expert page's own default for its
      // author and the memory for a caller — never a tab this caller may not see.
      defaultTab: offered[0] ?? "memory",
      isAllowed,
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

  return (
    <div className={styles.featurePanelStack}>
      <div className={styles.panelTabs}>
        <PathTabsSegmented pathTabs={pathTabs} isMobile={isMobile} />
      </div>

      <PersonalizationPanels
        agentId={feature.agent_id}
        agentState={feature.state}
        tabs={offered}
        activeTab={activeTab}
        isMounted={isMounted}
        scope="feature"
        canWrite={canWrite}
      />
    </div>
  );
}
