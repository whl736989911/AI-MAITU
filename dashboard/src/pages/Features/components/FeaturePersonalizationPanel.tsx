/**
 * A feature's own configuration surfaces, on the feature's own page.
 *
 * The same panels an expert is configured with — skills, tools, plugins,
 * subagents, MBTI, memory, channels and the persona files — pointed at the agent
 * the server gave this definition (``POST /features/{id}/agent``, idempotent,
 * ``feat-<id>``). Nothing here is a second implementation of them: this module
 * owns the scope, the tab row and the three states a scope can be in, and the
 * panels themselves are the shared stack both pages render.
 *
 * The tab row is the Personalization page's own control (``PathTabsSegmented``),
 * so a second-level row on this page is the same control rather than a look-alike
 * — the URL keeps the choice, exactly as it does there.
 *
 * Three states, none of them "empty":
 *   - opening: the agent is being materialized;
 *   - failed: the call or the status read refused, shown as itself with a retry,
 *     because panels pointed at an agent that is not there answer "nothing
 *     installed", which reads as a fact about the feature;
 *   - open: the panels, with the agent they are pointed at named above them.
 */

import { useMemo } from "react";
import { Alert, Button, Spin } from "antd";
import { RefreshCw, UserCog } from "lucide-react";
import { useTranslation } from "react-i18next";
import { PathTabsSegmented } from "../../../layouts/PageShell";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useFeatureAgent } from "../../../hooks/useFeatureAgent";
import { useIsMobile } from "../../../hooks/useIsMobile";
import { usePathTabs } from "../../../hooks/usePathTabs";
import { apiErrorMessage } from "../../../utils/apiError";
import { userCan } from "../../../utils/permissions";
import PersonalizationPanels, {
  FEATURE_PERSONALIZATION_TABS,
  TAB_ICONS,
  type PersonalizationTab,
} from "../../Agent/Personalization/components/PersonalizationPanels";
import scopeStyles from "../../Agent/Personalization/index.module.less";
import styles from "../index.module.less";

/** The tab a feature's surfaces open on, and the one an unshowable URL falls to. */
const DEFAULT_TAB: PersonalizationTab = "skills";

export default function FeaturePersonalizationPanel({
  featureId,
  canWrite,
}: {
  featureId: string;
  /**
   * Whether this caller may configure the definition. The page only renders this
   * panel for whoever may — the server refuses to open a feature's agent to
   * anyone else — so it is handed the same answer rather than assuming it.
   */
  canWrite: boolean;
}) {
  const { t } = useTranslation();
  const isMobile = useIsMobile();
  const user = useCurrentUser();
  const agent = useFeatureAgent(featureId);

  // Channels are the caller's own to reach, not the feature's: the permission
  // that gates them gates this row too.
  const isAllowed = useMemo(
    () => (tab: PersonalizationTab) =>
      tab !== "channels" || userCan(user, "channels"),
    [user],
  );

  const { activeTab, handleTabChange, isMounted } =
    usePathTabs<PersonalizationTab>({
      basePath: `/features/${featureId}/personalization`,
      tabs: FEATURE_PERSONALIZATION_TABS,
      storageKey: "octop:features:personalization:tab",
      defaultTab: DEFAULT_TAB,
      isAllowed,
    });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: FEATURE_PERSONALIZATION_TABS.filter((value) => isAllowed(value)).map(
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

  return (
    <div className={styles.featurePanelStack}>
      <div className={styles.panelTabs}>
        <PathTabsSegmented pathTabs={pathTabs} isMobile={isMobile} />
        {agent.agentId !== null && (
          // Whose agent these panels configure, said the way the Personalization
          // page's scope bar says it: it is app-owned and appears in nobody's
          // expert list, so nothing else on screen would name it.
          <div className={scopeStyles.scopeBound}>
            <span className={scopeStyles.scopeBoundLabel}>
              <UserCog size={13} strokeWidth={2} />
            </span>
            <code className={scopeStyles.scopeAgentId}>{agent.agentId}</code>
            <span
              className={scopeStyles.scopeAgentState}
              data-state={agent.state}
            >
              {agent.state}
            </span>
          </div>
        )}
      </div>

      {agent.agentId === null ? (
        <div className={scopeStyles.scopeStatus}>
          {agent.loading ? (
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
                agent.failure,
                t("features.scopeFeatureAgentFailed"),
                t,
              )}
              action={
                <Button
                  size="small"
                  icon={<RefreshCw size={13} />}
                  onClick={agent.retry}
                >
                  {t("features.settingsCapabilityRetry")}
                </Button>
              }
            />
          )}
        </div>
      ) : (
        <PersonalizationPanels
          agentId={agent.agentId}
          agentState={agent.state}
          tabs={FEATURE_PERSONALIZATION_TABS}
          activeTab={isAllowed(activeTab) ? activeTab : DEFAULT_TAB}
          isMounted={isMounted}
          scope="feature"
          canWrite={canWrite}
        />
      )}
    </div>
  );
}
