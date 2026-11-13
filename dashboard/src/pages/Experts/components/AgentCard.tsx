// dashboard/src/pages/Experts/components/AgentCard.tsx
import { memo, useCallback, useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Popconfirm, Tag, Tooltip } from "antd";
import { message } from "@/utils/antdMessage";
import { copyText } from "@/utils/copyText";

import {
  Copy,
  Pencil,
  Trash2,
  ChevronRight,
  AlertCircle,
  FolderOpen,
  RefreshCw,
  MessageSquare,
  SlidersHorizontal,
  Power,
  Play,
} from "lucide-react";
import WorkspaceDrawer from "../../Agent/Workspace/components/WorkspaceDrawer";
import { request } from "../../../api/request";
import type { OctopAgent } from "../../../context/AgentContext";
import { useAgent } from "../../../context/AgentContext";
import MbtiPersonaTag from "../../../components/MbtiPersonaTag";
import { ExpertIcon } from "./iconForName";
import {
  formatAgentError,
  formatAgentState,
  isAgentChatReady,
  isAgentModelConfigError,
} from "../../../utils/agentError";
import styles from "../index.module.less";
import {
  canManageExpert,
  isSharedExpertViewer,
} from "../../../utils/sharedExpert";
import { isFeatureAgent } from "../../../utils/agentKind";
import { useUserRole } from "../../../hooks/useUserRole";
import type { PublishedExpert } from "../../../api/modules/publishedExperts";
import PublishTemplateButton from "./PublishTemplateButton";

const STATE_META: Record<
  string,
  { color: string; bg: string; spin?: boolean }
> = {
  running: { color: "#52c41a", bg: "rgba(82,196,26,0.12)" },
  stopped: { color: "#8c8c8c", bg: "rgba(140,140,140,0.10)" },
  created: { color: "#8c8c8c", bg: "rgba(140,140,140,0.10)" },
  failed: { color: "#ff4d4f", bg: "rgba(255,77,79,0.10)" },
  starting: { color: "#1677ff", bg: "rgba(22,119,255,0.10)", spin: true },
  stopping: { color: "#1677ff", bg: "rgba(22,119,255,0.10)", spin: true },
};

function getStateMeta(state: string) {
  return STATE_META[state] ?? STATE_META.stopped;
}

const TRANSIENT = new Set(["starting", "stopping"]);
const MEMORY_MAINTENANCE_ACTIVE: Record<string, true> = {
  waiting: true,
  queued: true,
  backing_up: true,
  pruning: true,
  deduplicating: true,
  compacting: true,
};

const MEMORY_MAINTENANCE_TERMINAL: Record<string, true> = {
  done: true,
  failed: true,
  skipped: true,
};

/**
 * The card's own words about the row it is showing, keyed by kind.
 *
 * A feature's agent is not an expert (``utils/agentKind``), and a card that names it
 * one is making a claim the model does not: its "shared expert · from …" tag and its
 * "click to copy expert ID" tooltip are wrong for a feature's agent in *both*
 * languages. The set is closed and lives here so "which of this card's words depend
 * on what the row is" is one place to read, not something to re-derive per fragment.
 *
 * A row that does not say it is a feature keeps the experts' own key, so nothing an
 * expert's card shows changes — these are the only two words that differ, and both
 * are answered from the row's kind.
 */
const FEATURE_WORDS: Record<string, string> = {
  "experts.share.fromOwner": "features.share.fromOwner",
  "experts.copyAgentId": "features.copyAgentId",
};

export interface AgentCardProps {
  agent: OctopAgent;
  iconName?: string | null;
  iconUrl?: string | null;
  accentColor?: string | null;
  publishedExpert?: PublishedExpert | null;
  onPublishedChange?: () => void;
  onEdit: (agentId: string) => void;
  onConfigure?: (agentId: string) => void;
  onDeleted: (agentId: string) => void;
  onStateChange: (agentId: string, newState: string) => void;
  onPollSettled?: () => void;
  idLabelKey?: string;
}

export const AgentCard = memo(function AgentCard({
  agent,
  iconName,
  iconUrl,
  accentColor,
  publishedExpert = null,
  onPublishedChange,
  onEdit,
  onConfigure,
  onDeleted,
  onStateChange,
  onPollSettled,
  idLabelKey = "experts.agentId",
}: AgentCardProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const { setActiveAgent, refresh: refreshAgents } = useAgent();

  /** The key one of this card's own words about the row is read from — see FEATURE_WORDS. */
  const rowKey = (expertKey: string): string => {
    if (!isFeatureAgent(agent)) return expertKey;
    return FEATURE_WORDS[expertKey] ?? expertKey;
  };

  const [localState, setLocalState] = useState(agent.state);
  const [localError, setLocalError] = useState(agent.last_error);
  const [actionLoading, setActionLoading] = useState(false);
  const [workspaceDrawerOpen, setWorkspaceDrawerOpen] = useState(false);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const [memoryMaintenancePhase, setMemoryMaintenancePhase] = useState<
    string | null
  >(null);
  const maintPollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const terminalMaintenanceSeenRef = useRef<{
    key: string;
    timestamp: number;
  } | null>(null);

  useEffect(() => {
    setLocalState(agent.state);
    setLocalError(agent.last_error);
  }, [agent.state, agent.last_error]);

  // Poll during transient states
  useEffect(() => {
    if (!TRANSIENT.has(localState)) {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
      return;
    }
    pollRef.current = setInterval(() => {
      request<{ state: string; last_error: string | null }>(
        `/agents/${agent.agent_id}/status`,
      )
        .then((s) => {
          setLocalState(s.state);
          setLocalError(s.last_error);
          onStateChange(agent.agent_id, s.state);
          if (!TRANSIENT.has(s.state)) {
            if (pollRef.current) clearInterval(pollRef.current);
            pollRef.current = null;
            onPollSettled?.();
            void refreshAgents({ silent: true });
          }
        })
        .catch(() => {});
    }, 2000);
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
      pollRef.current = null;
    };
  }, [localState, agent.agent_id, onStateChange, onPollSettled, refreshAgents]);

  useEffect(() => {
    if (localState !== "running") {
      setMemoryMaintenancePhase(null);
      terminalMaintenanceSeenRef.current = null;
      if (maintPollRef.current) {
        clearInterval(maintPollRef.current);
        maintPollRef.current = null;
      }
      return;
    }
    let cancelled = false;
    const startedAt = Date.now();
    const pull = () =>
      request<{
        memory_maintenance?: {
          phase?: string;
          updated_at?: number | null;
          started_at?: number | null;
        } | null;
      }>(`/agents/${agent.agent_id}/status`)
        .then((s) => {
          if (cancelled) return;
          const maintenance = s.memory_maintenance;
          const phase = maintenance?.phase ?? "";
          const active = !!MEMORY_MAINTENANCE_ACTIVE[phase];
          const terminal = !!MEMORY_MAINTENANCE_TERMINAL[phase];
          const updatedAt = maintenance?.updated_at;
          let recentTerminal = false;
          if (terminal && maintenance) {
            const key = `${phase}:${updatedAt ?? ""}:${
              maintenance.started_at ?? ""
            }`;
            if (terminalMaintenanceSeenRef.current?.key !== key) {
              terminalMaintenanceSeenRef.current = {
                key,
                timestamp: Date.now() / 1000,
              };
            }
            const finishedAt =
              updatedAt ?? terminalMaintenanceSeenRef.current.timestamp;
            recentTerminal = Date.now() / 1000 - finishedAt <= 15;
          } else {
            terminalMaintenanceSeenRef.current = null;
          }
          setMemoryMaintenancePhase(active || recentTerminal ? phase : null);
          if (
            !active &&
            !recentTerminal &&
            Date.now() - startedAt > 15_000 &&
            maintPollRef.current
          ) {
            clearInterval(maintPollRef.current);
            maintPollRef.current = null;
          }
        })
        .catch(() => {});
    const start = setTimeout(pull, 800);
    maintPollRef.current = setInterval(pull, 2000);
    return () => {
      cancelled = true;
      clearTimeout(start);
      if (maintPollRef.current) {
        clearInterval(maintPollRef.current);
        maintPollRef.current = null;
      }
    };
  }, [localState, agent.agent_id]);

  const isTransient = TRANSIENT.has(localState);

  const handleToggle = useCallback(
    async (checked: boolean) => {
      setActionLoading(true);
      try {
        if (checked) {
          await request(`/agents/${agent.agent_id}/start`, { method: "POST" });
          setLocalState("starting");
          onStateChange(agent.agent_id, "starting");
          message.success(t("experts.agentStarted", { name: agent.name }));
        } else {
          await request(`/agents/${agent.agent_id}/stop`, { method: "POST" });
          setLocalState("stopping");
          onStateChange(agent.agent_id, "stopping");
          message.success(t("experts.agentStopped", { name: agent.name }));
        }
      } catch {
        message.error(
          checked
            ? t("experts.agentStartFailed")
            : t("experts.agentStopFailed"),
        );
      } finally {
        setActionLoading(false);
      }
    },
    [agent.agent_id, agent.name, t, onStateChange],
  );

  const handleDelete = useCallback(async () => {
    try {
      await request(`/agents/${agent.agent_id}`, { method: "DELETE" });
      message.success(t("experts.agentDeleted", { name: agent.name }));
      onDeleted(agent.agent_id);
    } catch {
      message.error(t("experts.agentDeleteFailed"));
    }
  }, [agent.agent_id, agent.name, t, onDeleted]);

  const handleReload = useCallback(async () => {
    setActionLoading(true);
    try {
      await request(`/agents/${agent.agent_id}/reload`, { method: "POST" });
      message.success(t("experts.agentReloadSuccess", { name: agent.name }));
    } catch {
      message.error(t("experts.agentReloadFailed"));
    } finally {
      setActionLoading(false);
    }
  }, [agent.agent_id, agent.name, t]);

  const handleOpenChat = useCallback(() => {
    setActiveAgent(agent.agent_id);
    navigate(`/chat/${agent.agent_id}`);
  }, [agent.agent_id, setActiveAgent, navigate]);

  const copyAgentId = useCallback(async () => {
    const ok = await copyText(agent.agent_id);
    if (ok) message.success(t("common.copied"));
    else message.error(t("common.copyFailed"));
  }, [agent.agent_id, t]);

  const accent = accentColor || "#6366f1";
  const meta = getStateMeta(localState);
  const friendlyError = formatAgentError(localError, t);
  const chatReady = isAgentChatReady(localState);
  const role = useUserRole();
  const canManage = canManageExpert(agent, role);
  const sharedViewer = isSharedExpertViewer(agent);
  const handleConfigure = useCallback(() => {
    if (onConfigure) {
      onConfigure(agent.agent_id);
      return;
    }
    const isFeature = isFeatureAgent(agent);
    const params = new URLSearchParams({
      [isFeature ? "feature" : "expert"]: agent.agent_id,
      settings: isFeature ? "memory" : "skills",
    });
    navigate(`${isFeature ? "/features" : "/experts"}?${params.toString()}`);
  }, [agent, navigate, onConfigure]);

  return (
    <>
      <div
        className={styles.agentCard2}
        style={{ "--agent-accent": accent } as React.CSSProperties}
      >
        {/* Accent top bar */}
        <div className={styles.agentCard2Accent} />

        {/* Header */}
        <div className={styles.agentCard2Header}>
          <div
            className={styles.agentCard2Icon}
            style={{ color: accent, background: `${accent}1a` }}
          >
            <ExpertIcon
              iconUrl={iconUrl ?? agent.icon_url}
              iconName={iconName ?? agent.icon_name}
              size={iconUrl ?? agent.icon_url ? 42 : 22}
            />
          </div>

          <div className={styles.agentCard2TitleBlock}>
            <div className={styles.agentCard2NameRow}>
              <div className={styles.agentCard2Name}>{agent.name}</div>
              {isFeatureAgent(agent) && agent.user_id === null && (
                <Tag color="purple">
                  {t("features.share.enterpriseManaged")}
                </Tag>
              )}
              {agent.is_shared && (
                <Tag color="blue">
                  {sharedViewer && agent.owner_username
                    ? t(rowKey("experts.share.fromOwner"), {
                        name: agent.owner_username,
                      })
                    : t("experts.share.badge")}
                </Tag>
              )}
            </div>
            <div className={styles.agentCardIdRow}>
              <Tooltip title={t(rowKey("experts.copyAgentId"))}>
                <button
                  type="button"
                  className={styles.agentCardId}
                  onClick={() => void copyAgentId()}
                >
                  <span className={styles.agentCardIdLabel}>
                    {t(idLabelKey)}
                  </span>
                  <span className={styles.agentCardIdValue}>
                    {agent.agent_id}
                  </span>
                  <Copy size={11} className={styles.agentCardIdIcon} />
                </button>
              </Tooltip>
            </div>
            <div className={styles.agentCard2Meta}>
              <div
                className={styles.agentCard2State}
                style={{ color: meta.color, background: meta.bg }}
              >
                <span
                  className={meta.spin ? styles.stateDotSpin : styles.stateDot2}
                  style={!meta.spin ? { background: meta.color } : undefined}
                />
                {formatAgentState(localState, t)}
              </div>
              {memoryMaintenancePhase && (
                <Tag
                  color={
                    memoryMaintenancePhase === "done"
                      ? "success"
                      : memoryMaintenancePhase === "failed"
                      ? "error"
                      : memoryMaintenancePhase === "skipped"
                      ? "default"
                      : "processing"
                  }
                >
                  {t(`chat.memoryMaintenance.${memoryMaintenancePhase}`, {
                    defaultValue: t("experts.memorySlimming"),
                  })}
                </Tag>
              )}
              <MbtiPersonaTag value={agent.persona_mbti} />
            </div>
          </div>

          {role === "admin" && (
            <div className={styles.agentCard2HeaderActions}>
              <Tooltip
                title={
                  chatReady
                    ? t("pageShell.workspace.title")
                    : t("workspace.requiresRunning")
                }
                mouseEnterDelay={0.5}
              >
                <button
                  type="button"
                  className={styles.agentCard2EditBtn}
                  disabled={!chatReady}
                  onClick={() => setWorkspaceDrawerOpen(true)}
                  aria-label={t("pageShell.workspace.title")}
                >
                  <FolderOpen size={13} />
                </button>
              </Tooltip>
            </div>
          )}
        </div>

        {/* Description */}
        {agent.description && (
          <p className={styles.agentCard2Desc}>{agent.description}</p>
        )}

        {/* Error */}
        {localState === "failed" && friendlyError && (
          <div className={styles.agentCardErrorWrap}>
            <Tooltip
              title={friendlyError}
              mouseEnterDelay={0.3}
              overlayStyle={{ maxWidth: 360 }}
            >
              <div className={styles.agentCardError}>
                <AlertCircle size={13} className={styles.agentCardErrorIcon} />
                <span className={styles.agentCardErrorText}>
                  {friendlyError}
                </span>
              </div>
            </Tooltip>
            {isAgentModelConfigError(localError) && (
              <button
                type="button"
                className={styles.agentCardErrorAction}
                onClick={() => navigate("/admin/models")}
              >
                {t("modelConfig.configureButton")}
              </button>
            )}
          </div>
        )}

        {/* Footer actions */}
        <div className={styles.agentCard2Footer}>
          {canManage && (
            <>
              <button
                type="button"
                className={styles.agentCard2EditBtn}
                disabled={isTransient || actionLoading}
                onClick={() => void handleToggle(localState !== "running")}
              >
                {localState === "running" ? (
                  <Power size={14} />
                ) : (
                  <Play size={14} />
                )}
                {localState === "running"
                  ? t("experts.stopAgent")
                  : localState === "failed"
                  ? t("experts.retryStart", "重试启动")
                  : t("experts.startAgent")}
              </button>
              <button
                type="button"
                className={styles.agentCard2EditBtn}
                disabled={isTransient || actionLoading}
                onClick={() => void handleReload()}
                aria-label={t("experts.reloadAgent")}
              >
                <RefreshCw size={13} />
                {t("experts.reloadAgent")}
              </button>
              <button
                type="button"
                className={styles.agentCard2EditBtn}
                onClick={() => onEdit(agent.agent_id)}
                aria-label={t("common.edit", "Edit")}
              >
                <Pencil size={13} />
                {t("common.edit", "Edit")}
              </button>
              <Popconfirm
                title={t("experts.confirmDelete", { name: agent.name })}
                description={t("experts.confirmDeleteHint")}
                onConfirm={() => void handleDelete()}
                okText={t("common.delete", "Delete")}
                cancelText={t("common.cancel")}
                okButtonProps={{ danger: true }}
              >
                <button
                  type="button"
                  className={styles.agentCard2DelBtn}
                  aria-label={t("common.delete", "Delete")}
                >
                  <Trash2 size={13} />
                  {t("common.delete", "Delete")}
                </button>
              </Popconfirm>
              {onPublishedChange && (
                <PublishTemplateButton
                  agent={agent}
                  published={publishedExpert}
                  onChanged={onPublishedChange}
                  buttonClassName={styles.agentCard2EditBtn}
                />
              )}
            </>
          )}
          <button
            type="button"
            className={styles.agentCard2EditBtn}
            onClick={handleConfigure}
            aria-label={t("common.configure")}
          >
            <SlidersHorizontal size={14} />
            {t("common.configure")}
          </button>
          {chatReady ? (
            <button
              className={styles.agentCard2ChatBtn}
              style={{ color: accent, borderColor: `${accent}55` }}
              onClick={handleOpenChat}
            >
              <MessageSquare size={13} />
              {t("experts.openChat", "对话")}
              <ChevronRight size={13} />
            </button>
          ) : null}
        </div>
      </div>
      {role === "admin" && (
        <WorkspaceDrawer
          agentId={agent.agent_id}
          open={workspaceDrawerOpen}
          onClose={() => setWorkspaceDrawerOpen(false)}
        />
      )}
    </>
  );
});
