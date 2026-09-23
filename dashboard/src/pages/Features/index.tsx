/**
 * Features — the caller's own list, and what a card on it can do.
 *
 * A feature *is* an agent (``utils/agentKind.ts``): this page is the same grid the
 * Experts page shows its own experts in, built from the same card
 * (``AgentCard``), reading the same ``/agents`` list and filtering it by ``kind``.
 * Nothing here is a second rendering of an agent — a feature that looked like
 * anything but an agent on this page would be a claim the model does not make.
 *
 * What the card offers a reader is the card's own answer, which is already the
 * matrix: its author reaches the start switch, the workspace, the reload, the edit
 * and the catalogs, an administrator reaches them on any feature in front of them
 * (``canManageExpert`` in ``utils/sharedExpert``, the server's own "the owner, or
 * an administrator"), and a caller of one reaches the conversation and nothing
 * that writes. The page adds no gate of its own, so the two cannot disagree.
 *
 * ── Where a feature is configured ───────────────────────────────────────────
 * The card opens the experts' own drawer for its agent definition and the same
 * capability catalogs used by Personalization. Its workflow action opens this
 * page's drawer for the feature-specific run definition; callers can read it,
 * while only its author can write it.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Drawer, Spin, Tooltip } from "antd";
import { LayoutGrid, Plus, RefreshCw } from "lucide-react";
import { message } from "@/utils/antdMessage";

import PageShell from "../../layouts/PageShell";
import { useAgent, type OctopAgent } from "../../context/AgentContext";
import { isFeatureAgent } from "../../utils/agentKind";
import { AgentCard } from "../Experts/components/AgentCard";
import EditAgentDrawer from "../Experts/components/EditAgentDrawer";
import { EmptyStateIcon } from "../../components/EmptyState";
import FeatureCreateDrawer from "./components/FeatureCreateDrawer";
import FeatureWorkflowPanel from "./components/FeatureWorkflowPanel";
// The experts' own grid, toolbar and empty-state styles: a feature's list is the
// same list, so it is the same stylesheet rather than a look-alike of it.
import styles from "../Experts/index.module.less";

/**
 * The features in front of the caller, the ones they defined first.
 *
 * Order is what makes the two roles readable at a glance — "mine" is the list the
 * page is for, and a feature somebody else defined is next to it with the card's
 * own ``fromOwner`` tag.
 */
function orderFeatures(features: OctopAgent[]): OctopAgent[] {
  return [...features].sort((a, b) => {
    const mine = (agent: OctopAgent) => (agent.is_owner === false ? 1 : 0);
    return mine(a) - mine(b) || a.name.localeCompare(b.name);
  });
}

export default function FeaturesPage() {
  const { t } = useTranslation();
  const { agents, refresh, loading } = useAgent();

  const features = useMemo(
    () => orderFeatures(agents.filter(isFeatureAgent)),
    [agents],
  );
  /** Local copy so start/stop and delete land without waiting for a refetch. */
  const [localFeatures, setLocalFeatures] = useState<OctopAgent[]>(features);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  /** The feature the card's pencil was pressed on — the drawer's row, or none. */
  const [editFeature, setEditFeature] = useState<OctopAgent | null>(null);
  const [workflowFeature, setWorkflowFeature] = useState<OctopAgent | null>(
    null,
  );

  useEffect(() => {
    setLocalFeatures(features);
  }, [features]);

  const handleRefresh = useCallback(async () => {
    setRefreshing(true);
    try {
      await refresh({ silent: true, force: true });
    } catch (err: unknown) {
      message.error(
        err instanceof Error ? err.message : t("features.loadFailed"),
      );
    } finally {
      setRefreshing(false);
    }
  }, [refresh, t]);

  const handleStateChange = useCallback((agentId: string, newState: string) => {
    setLocalFeatures((prev) =>
      prev.map((a) => (a.agent_id === agentId ? { ...a, state: newState } : a)),
    );
  }, []);

  const handleDeleted = useCallback(
    (agentId: string) => {
      setLocalFeatures((prev) => prev.filter((a) => a.agent_id !== agentId));
      void refresh({ silent: true, force: true });
    },
    [refresh],
  );

  const openCreate = useCallback(() => setCreateOpen(true), []);

  /**
   * What the drawer hands back is the row it wrote, so the card follows the
   * server's copy of it without re-reading the whole list — the same handling
   * the Experts page gives the same drawer.
   */
  const handleEditSaved = useCallback(
    (
      updated: Pick<
        OctopAgent,
        | "agent_id"
        | "name"
        | "description"
        | "default_model"
        | "is_shared"
        | "color"
        | "icon_url"
      >,
    ) => {
      setEditFeature(null);
      setLocalFeatures((prev) =>
        prev.map((a) =>
          a.agent_id === updated.agent_id ? { ...a, ...updated } : a,
        ),
      );
    },
    [],
  );

  const refreshButton = (
    <Tooltip title={t("common.refresh")}>
      <button
        className={styles.toolbarIconBtn}
        onClick={() => void handleRefresh()}
        disabled={refreshing}
        type="button"
      >
        <RefreshCw
          size={14}
          className={refreshing ? styles.spinning : undefined}
        />
      </button>
    </Tooltip>
  );

  const createButton = (
    <button className={styles.toolbarBtn} onClick={openCreate} type="button">
      <Plus size={14} />
      {t("features.create")}
    </button>
  );

  const content =
    loading && localFeatures.length === 0 ? (
      <div className={styles.loadingState}>
        <Spin />
      </div>
    ) : localFeatures.length === 0 ? (
      <div className={styles.emptyState}>
        <EmptyStateIcon icon={LayoutGrid} />
        <div className={styles.emptyTitle}>{t("features.empty")}</div>
        <div className={styles.emptyHint}>{t("features.emptyHint")}</div>
        <div className={styles.emptyActions}>
          {refreshButton}
          <button
            className={styles.emptyAction}
            onClick={openCreate}
            type="button"
          >
            {t("features.create")}
          </button>
        </div>
      </div>
    ) : (
      <>
        <div className={styles.gridToolbar}>
          <span className={styles.gridCount}>
            {t("features.total", { count: localFeatures.length })}
          </span>
          <div className={styles.gridToolbarRight}>
            {refreshButton}
            {createButton}
          </div>
        </div>
        <div className={styles.cardGrid}>
          {localFeatures.map((feature) => (
            <AgentCard
              key={feature.agent_id}
              agent={feature}
              iconName={feature.icon_name}
              iconUrl={feature.icon_url}
              accentColor={feature.color}
              // The card's own id row, labelled as what it holds here: a
              // feature's agent id, not an expert's.
              idLabelKey="features.agentId"
              // A feature is edited where an expert is: the experts' own drawer,
              // opened here over the feature's row (``EditAgentDrawer``).
              onEdit={(agentId) =>
                setEditFeature(
                  localFeatures.find((a) => a.agent_id === agentId) ?? null,
                )
              }
              onWorkflow={(agentId) =>
                setWorkflowFeature(
                  localFeatures.find((a) => a.agent_id === agentId) ?? null,
                )
              }
              onDeleted={handleDeleted}
              onStateChange={handleStateChange}
            />
          ))}
        </div>
      </>
    );

  return (
    <PageShell
      title={t("pageShell.features.title")}
      subtitle={t("pageShell.features.subtitle")}
    >
      {content}

      {/* The experts' own editor, mounted at the page's level the way the Experts
          page mounts it: the card's pencil opens it over the list. */}
      <EditAgentDrawer
        open={!!editFeature}
        agent={editFeature}
        titleKey="features.editDefinition"
        onClose={() => setEditFeature(null)}
        onSaved={handleEditSaved}
      />
      <Drawer
        open={workflowFeature !== null}
        title={`${workflowFeature?.name ?? ""} · ${t("features.tabWorkflow")}`}
        width="min(960px, 100vw)"
        onClose={() => setWorkflowFeature(null)}
        destroyOnHidden
      >
        {workflowFeature && (
          <FeatureWorkflowPanel
            key={workflowFeature.agent_id}
            agentId={workflowFeature.agent_id}
            canWrite={workflowFeature.is_owner !== false}
          />
        )}
      </Drawer>

      <FeatureCreateDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        // A new feature lands on this list, next to the ones already here: the
        // drawer's own toast has named it, the refetch puts its card in the grid,
        // and that card is where it is configured (see this file's own header).
        onCreated={() => {
          setCreateOpen(false);
          void refresh({ silent: true, force: true });
        }}
      />
    </PageShell>
  );
}
