/**
 * Features share the agent card and list with Experts. Enterprise administrators
 * additionally see drafts from their enterprise; the server supplies each
 * feature's `can_manage` verdict, so published ownership need not be inferred
 * from a personal user id.
 */

import { useCallback, useEffect, useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import { Button, Spin, Tooltip } from "antd";
import { ArrowLeft, LayoutGrid, Plus, RefreshCw } from "lucide-react";
import { useSearchParams } from "react-router-dom";
import { message } from "@/utils/antdMessage";

import PageShell from "../../layouts/PageShell";
import { useAgent, type OctopAgent } from "../../context/AgentContext";
import { isFeatureAgent } from "../../utils/agentKind";
import { useUserRole } from "../../hooks/useUserRole";
import { canManageExpert } from "../../utils/sharedExpert";
import { AgentCard } from "../Experts/components/AgentCard";
import EditAgentDrawer from "../Experts/components/EditAgentDrawer";
import { EmptyStateIcon } from "../../components/EmptyState";
import FeatureCreateDrawer from "./components/FeatureCreateDrawer";
import ResourceSettings from "../Agent/Personalization/components/ResourceSettings";
import styles from "../Experts/index.module.less";
import drawerStyles from "./components/FeatureDrawerTabs.module.less";

/** Show the caller's own draft features before other visible features. */
function orderFeatures(features: OctopAgent[]): OctopAgent[] {
  return [...features].sort((a, b) => {
    const mine = (agent: OctopAgent) => (agent.is_owner === false ? 1 : 0);
    return mine(a) - mine(b) || a.name.localeCompare(b.name);
  });
}

export default function FeaturesPage() {
  const { t } = useTranslation();
  const { agents, refresh, loading } = useAgent();
  const role = useUserRole();
  const [searchParams, setSearchParams] = useSearchParams();

  const features = useMemo(
    () => orderFeatures(agents.filter(isFeatureAgent)),
    [agents],
  );
  /** Local copy so start/stop and delete land without waiting for a refetch. */
  const [localFeatures, setLocalFeatures] = useState<OctopAgent[]>(features);
  const [refreshing, setRefreshing] = useState(false);
  const [createOpen, setCreateOpen] = useState(false);
  const [editFeature, setEditFeature] = useState<OctopAgent | null>(null);
  const selectedFeatureId = searchParams.get("feature");
  const selectedFeature =
    localFeatures.find((feature) => feature.agent_id === selectedFeatureId) ??
    null;
  const openResourceSettings = useCallback(
    (agentId: string, tab: string) => {
      const next = new URLSearchParams(searchParams);
      next.set("feature", agentId);
      next.set("settings", tab);
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );
  const openFeatureWorkspaceTab = useCallback(
    (tab: "workflow" | "data") => {
      const next = new URLSearchParams(searchParams);
      next.set("feature_tab", tab);
      setSearchParams(next);
    },
    [searchParams, setSearchParams],
  );
  const closeResourceSettings = useCallback(() => {
    const next = new URLSearchParams(searchParams);
    next.delete("feature");
    next.delete("settings");
    next.delete("feature_tab");
    setSearchParams(next);
  }, [searchParams, setSearchParams]);

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
      if (selectedFeatureId === agentId) closeResourceSettings();
      void refresh({ silent: true, force: true });
    },
    [closeResourceSettings, refresh, selectedFeatureId],
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
              idLabelKey="features.agentId"
              onEdit={(agentId) =>
                setEditFeature(
                  localFeatures.find((a) => a.agent_id === agentId) ?? null,
                )
              }
              onConfigure={(agentId) => openResourceSettings(agentId, "memory")}
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
      {!selectedFeature && content}
      {selectedFeature && (
        <section className={drawerStyles.resourceWorkspace}>
          <div className={drawerStyles.resourceHeader}>
            <div>
              <Button
                type="text"
                icon={<ArrowLeft size={15} />}
                onClick={closeResourceSettings}
              >
                {t("common.back")}
              </Button>
              <h2>{selectedFeature.name}</h2>
              {selectedFeature.description && (
                <p>{selectedFeature.description}</p>
              )}
            </div>
            <span>
              {t("features.agentId")}: {selectedFeature.agent_id}
            </span>
          </div>
          <ResourceSettings
            key={selectedFeature.agent_id}
            agentId={selectedFeature.agent_id}
            agentState={selectedFeature.state}
            scope="feature"
            canWrite={canManageExpert(selectedFeature, role)}
            activeTab={searchParams.get("settings")}
            featureWorkspaceTab={
              searchParams.get("feature_tab") === "data" ? "data" : "workflow"
            }
            onFeatureWorkspaceTabChange={openFeatureWorkspaceTab}
            onTabChange={(tab) =>
              openResourceSettings(selectedFeature.agent_id, tab)
            }
          />
        </section>
      )}
      <EditAgentDrawer
        open={!!editFeature}
        agent={editFeature}
        titleKey="features.editDefinition"
        onClose={() => setEditFeature(null)}
        onSaved={handleEditSaved}
      />
      <FeatureCreateDrawer
        open={createOpen}
        onClose={() => setCreateOpen(false)}
        onCreated={() => {
          setCreateOpen(false);
          void refresh({ silent: true, force: true });
        }}
      />
    </PageShell>
  );
}
