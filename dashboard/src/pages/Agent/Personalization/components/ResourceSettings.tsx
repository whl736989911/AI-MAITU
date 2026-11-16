import { useMemo, useState } from "react";
import { Tabs } from "antd";
import { useTranslation } from "react-i18next";
import { useCurrentUser } from "../../../../hooks/useCurrentUser";
import { personalizationTabAllowed } from "../../../../utils/permissions";
import PersonalizationPanels, {
  FEATURE_PERSONALIZATION_TABS,
  PERSONALIZATION_TABS,
  TAB_ICONS,
  offeredTabs,
  type PersonalizationScope,
  type PersonalizationTab,
} from "./PersonalizationPanels";

interface ResourceSettingsProps {
  agentId: string;
  agentState: string;
  scope: PersonalizationScope;
  canWrite: boolean;
  activeTab?: string | null;
  featureWorkspaceTab?: "workflow" | "data";
  onTabChange?: (tab: PersonalizationTab) => void;
  onFeatureWorkspaceTabChange?: (tab: "workflow" | "data") => void;
}

/** Settings whose panels are always bound to the selected resource id. */
export default function ResourceSettings({
  agentId,
  agentState,
  scope,
  canWrite,
  activeTab: requestedTab,
  featureWorkspaceTab,
  onTabChange,
  onFeatureWorkspaceTabChange,
}: ResourceSettingsProps) {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const tabs = useMemo(
    () =>
      offeredTabs(
        scope,
        scope === "feature"
          ? FEATURE_PERSONALIZATION_TABS
          : PERSONALIZATION_TABS,
        canWrite,
      ).filter((tab) => personalizationTabAllowed(user, tab)),
    [scope, canWrite, user],
  );
  const [localTab, setLocalTab] = useState<PersonalizationTab>(
    tabs[0] ?? "skills",
  );
  const activeTab = tabs.includes(requestedTab as PersonalizationTab)
    ? (requestedTab as PersonalizationTab)
    : tabs.includes(localTab)
    ? localTab
    : tabs[0];
  const [mounted, setMounted] = useState<Set<PersonalizationTab>>(
    () => new Set(activeTab ? [activeTab] : []),
  );

  if (!activeTab) return null;
  return (
    <>
      <Tabs
        activeKey={activeTab}
        onChange={(key) => {
          const next = key as PersonalizationTab;
          setLocalTab(next);
          setMounted((current) => new Set(current).add(next));
          onTabChange?.(next);
        }}
        items={tabs.map((tab) => {
          const Icon = TAB_ICONS[tab];
          return {
            key: tab,
            label: (
              <span
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 6,
                }}
              >
                <Icon size={14} />
                {t(`personalization.tabs.${tab}`)}
              </span>
            ),
            children: null,
          };
        })}
      />
      <PersonalizationPanels
        key={`${scope}:${agentId}`}
        agentId={agentId}
        agentState={agentState}
        tabs={tabs}
        activeTab={activeTab}
        isMounted={(tab) => mounted.has(tab)}
        scope={scope}
        canWrite={canWrite}
        featureWorkspaceTab={featureWorkspaceTab}
        onFeatureWorkspaceTabChange={onFeatureWorkspaceTabChange}
      />
    </>
  );
}
