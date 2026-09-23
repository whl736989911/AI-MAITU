/**
 * A feature's own page — what it is, and everything it is configured with.
 *
 * The page is the Personalization page's shape, twice: a title row with the first
 * level of tabs, the scope bar the experts' bar sits in, and then the panels —
 * because a feature *is* an agent (``utils/agentKind.ts``) and its capabilities are
 * the experts' own panels (``PersonalizationPanels``). The first level is what makes
 * it a feature's page rather than an expert's:
 *
 *   - **definition** — the half of the row an expert keeps elsewhere
 *     (``FeatureDefinitionPanel``): what the feature is, its agent, its author,
 *     its model, its welcome and the defaults a call opens with;
 *   - **workflow** — what a run does (``FeatureWorkflowPanel``): the form it asks
 *     the caller for, the fixed steps, the deliverables and the rules. An expert
 *     has no run to declare, so this tab has no counterpart on the expert side;
 *   - **personalization** — the identical stack of capability tabs, one level down
 *     (``FeaturePersonalizationPanel``), where ``PathTabsSegmented`` draws the row
 *     the Personalization page draws in its title.
 *
 * **The scope is the route.** ``:agentId`` is the feature's agent id — the id every
 * panel is built from — so the page reads it off the URL like ``/chat/:agentId``
 * does, and the app's active agent is left where it was. See ``FeatureScopeBar`` for
 * why that matters to the expert side.
 *
 * **A feature the caller cannot see is said, not blanked.** The row comes from
 * ``/agents`` (owned plus shared, ``kind`` included), so an id that is not in the
 * list is one this caller may not reach: the page says so and points back at the
 * list, rather than rendering panels pointed at nothing — which would read on
 * screen as a feature with nothing configured.
 *
 * **Read-only is the row's own answer.** ``is_owner`` on the row is who may
 * configure the feature, which is the same answer the server gives
 * (``api/common/agent.py``): the author writes it, a caller reads it. The page hands
 * that one boolean to both tabs; neither invents a second rule.
 */

import { useCallback, useMemo } from "react";
import { useNavigate, useParams } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { Alert, Button, Spin } from "antd";
import { ArrowLeft, Settings2, UserCog, Workflow } from "lucide-react";

import PageShell from "../../layouts/PageShell";
import { useAgent } from "../../context/AgentContext";
import { isFeatureAgent } from "../../utils/agentKind";
import { usePathTabs } from "../../hooks/usePathTabs";
import type { PathTabOption } from "../../layouts/PageShell";
import FeatureScopeBar from "./components/FeatureScopeBar";
import FeatureDefinitionPanel from "./components/FeatureDefinitionPanel";
import FeaturePersonalizationPanel from "./components/FeaturePersonalizationPanel";
import FeatureWorkflowPanel from "./components/FeatureWorkflowPanel";
import styles from "./index.module.less";

/** The three levels this page offers a feature, in order. */
type FeatureTab = "definition" | "workflow" | "personalization";

const FEATURE_TABS = [
  "definition",
  "workflow",
  "personalization",
] as const satisfies readonly FeatureTab[];

const TAB_ICONS = {
  definition: Settings2,
  workflow: Workflow,
  personalization: UserCog,
} as const;

const TAB_LABEL_KEYS: Record<FeatureTab, string> = {
  definition: "features.tabDefinition",
  workflow: "features.tabWorkflow",
  personalization: "features.tabPersonalization",
};

export default function FeatureDetailPage() {
  const { agentId } = useParams<{ agentId: string }>();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { agents, loading, refresh } = useAgent();

  const feature = useMemo(
    () => agents.find((a) => a.agent_id === agentId) ?? null,
    [agents, agentId],
  );
  /** The caller's own features, for the scope bar's switcher. */
  const ownFeatures = useMemo(
    () => agents.filter((a) => isFeatureAgent(a) && a.is_owner !== false),
    [agents],
  );

  const { activeTab, handleTabChange, isMounted } = usePathTabs<FeatureTab>({
    basePath: `/features/${agentId ?? ""}`,
    tabs: FEATURE_TABS,
    storageKey: "octop:features:tab",
    defaultTab: "definition",
  });

  const pathTabs = useMemo(
    () => ({
      value: activeTab,
      onChange: handleTabChange,
      options: FEATURE_TABS.map((value) => {
        const Icon = TAB_ICONS[value];
        return {
          value,
          label: t(TAB_LABEL_KEYS[value]),
          icon: <Icon size={14} strokeWidth={2} />,
        } satisfies PathTabOption;
      }),
    }),
    [activeTab, handleTabChange, t],
  );

  const reload = useCallback(() => {
    void refresh({ silent: true, force: true });
  }, [refresh]);

  if (feature === null) {
    if (loading) {
      return (
        <div className={styles.loading}>
          <Spin size="large" />
        </div>
      );
    }
    return (
      <PageShell title={t("pageShell.features.title")}>
        <Alert
          type="warning"
          showIcon
          message={t("features.notFound")}
          description={t("features.notFoundHint")}
          action={
            <Button
              size="small"
              icon={<ArrowLeft size={13} />}
              onClick={() => navigate("/features")}
            >
              {t("features.backToList")}
            </Button>
          }
        />
      </PageShell>
    );
  }

  // The row's own answer to "may this caller configure the feature": its author
  // owns it, and the server reads the same field.
  const canWrite = feature.is_owner !== false;

  return (
    <PageShell
      title={feature.name}
      subtitle={feature.description ?? undefined}
      pathTabs={pathTabs}
      agentScoped
      agentBar={
        <FeatureScopeBar
          feature={feature}
          ownFeatures={ownFeatures}
          onSwitch={(next) => navigate(`/features/${next}/${activeTab}`)}
        />
      }
    >
      <div className={styles.page}>
        {isMounted("definition") && (
          <div
            className={styles.tabPanel}
            style={{ display: activeTab === "definition" ? "flex" : "none" }}
            aria-hidden={activeTab !== "definition"}
          >
            <FeatureDefinitionPanel
              feature={feature}
              canWrite={canWrite}
              onSaved={reload}
            />
          </div>
        )}

        {isMounted("workflow") && (
          <div
            className={styles.tabPanel}
            style={{ display: activeTab === "workflow" ? "flex" : "none" }}
            aria-hidden={activeTab !== "workflow"}
          >
            {/* Keyed by the feature: switching features starts from that feature's
                own document, not the previous one's mode and selected step. */}
            <FeatureWorkflowPanel
              key={feature.agent_id}
              agentId={feature.agent_id}
              canWrite={canWrite}
            />
          </div>
        )}

        {isMounted("personalization") && (
          <div
            className={styles.tabPanel}
            style={{
              display: activeTab === "personalization" ? "flex" : "none",
            }}
            aria-hidden={activeTab !== "personalization"}
          >
            <FeaturePersonalizationPanel
              feature={feature}
              canWrite={canWrite}
            />
          </div>
        )}
      </div>
    </PageShell>
  );
}
