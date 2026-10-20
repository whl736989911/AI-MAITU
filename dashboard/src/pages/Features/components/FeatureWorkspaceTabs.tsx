/**
 * The two views every feature-management surface shows: its workflow and its
 * business data. Kept in one component so the Features page, the card's
 * workflow drawer and the Personalization page cannot drift apart again.
 */

import { Tabs } from "antd";
import { useTranslation } from "react-i18next";

import FeatureDataPanel from "./FeatureDataPanel";
import FeatureWorkflowPanel from "./FeatureWorkflowPanel";

export interface FeatureWorkspaceTabsProps {
  agentId: string;
  /** Workflow/schema management: the feature's managers. */
  canWrite: boolean;
  /** Create/edit records; the server still enforces record ownership. */
  canEditRecords?: boolean;
  enterpriseManaged?: boolean;
  onSaved?: () => void;
  defaultTab?: "workflow" | "data";
  className?: string;
}

export default function FeatureWorkspaceTabs({
  agentId,
  canWrite,
  canEditRecords = true,
  enterpriseManaged = false,
  onSaved,
  defaultTab = "workflow",
  className,
}: FeatureWorkspaceTabsProps) {
  const { t } = useTranslation();
  return (
    <Tabs
      className={className}
      defaultActiveKey={defaultTab}
      items={[
        {
          key: "workflow",
          label: t("features.tabWorkflow"),
          children: (
            <FeatureWorkflowPanel
              key={`workflow-${agentId}`}
              agentId={agentId}
              canWrite={canWrite}
              enterpriseManaged={enterpriseManaged}
              onSaved={onSaved}
            />
          ),
        },
        {
          key: "data",
          label: t("features.data.tabLabel"),
          children: (
            <FeatureDataPanel
              key={`data-${agentId}`}
              agentId={agentId}
              canWrite={canWrite}
              canEditRecords={canEditRecords}
            />
          ),
        },
      ]}
    />
  );
}
