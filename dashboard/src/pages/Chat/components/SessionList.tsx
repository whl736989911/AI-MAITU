import { memo, useCallback, useMemo, useState, useRef, useEffect } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { Dropdown } from "antd";
import type { MenuProps } from "antd";
import {
  Pencil,
  MoreHorizontal,
  Trash2,
  Pin,
  PinOff,
  Search,
  GitFork,
} from "lucide-react";
import type { Session } from "../hooks/useSessions";
import type { OctopAgent } from "../../../context/AgentContext";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import {
  emptyAgentAccessFor,
  emptyAgentAccessKey,
  emptyAgentAccessPath,
} from "../utils/emptyAgentAccess";
import { isAgentChatReady } from "../../../utils/agentError";
import { isFeatureAgent } from "../../../utils/agentKind";
import { showConfirmModal } from "../../../utils/confirmModal";
import { ExpertIcon } from "../../Experts/components/iconForName";
import SessionChannelIcon from "./SessionChannelIcon";
import SharedExpertHint from "./SharedExpertHint";
import styles from "../index.module.less";
import { isImeComposing } from "../utils/ime";
import { onStreamEvent } from "../hooks/chatStore";
import { isPendingThread } from "../hooks/useSessions";

function AgentUnreadBadge({ count }: { count: number }) {
  const { t } = useTranslation();
  if (!count || count <= 0) return null;
  return (
    <span
      className={styles.agentUnreadBadge}
      aria-label={t("chat.unreadMessages", "未读消息")}
    >
      {count > 99 ? "99+" : count}
    </span>
  );
}

interface SessionItemProps {
  session: Session;
  isActive: boolean;
  working: boolean;
  pending: boolean;
  onSelect: (id: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  forkDisabled?: boolean;
  forkDisabledHint?: string;
}

const SessionItem = memo(function SessionItem({
  session,
  isActive,
  onSelect,
  onDelete,
  onRename,
  onPin,
  onFork,
  forkDisabled,
  working,
  pending,
  forkDisabledHint,
}: SessionItemProps) {
  const { t } = useTranslation();
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState(session.name);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (!isEditing) setEditValue(session.name);
  }, [session.name, isEditing]);

  useEffect(() => {
    if (isEditing) {
      inputRef.current?.focus();
      inputRef.current?.select();
    }
  }, [isEditing]);

  const commitEdit = useCallback(() => {
    const trimmed = editValue.trim();
    if (trimmed && trimmed !== session.name) {
      onRename(session.id, trimmed);
    } else {
      setEditValue(session.name);
    }
    setIsEditing(false);
  }, [editValue, session.name, session.id, onRename]);

  const itemForkDisabled = Boolean(forkDisabled) || !session.hasActivity;
  const itemForkHint = !session.hasActivity
    ? t("chat.forkNoAssistant")
    : forkDisabledHint;

  const menuItems: MenuProps["items"] = [
    {
      key: "pin",
      label: session.pinned
        ? t("chat.unpin", "取消置顶")
        : t("chat.pin", "置顶"),
      icon: session.pinned ? <PinOff size={14} /> : <Pin size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onPin(session.id, !session.pinned);
      },
    },
    {
      key: "fork",
      label: t("chat.fork", "分叉"),
      icon: <GitFork size={14} />,
      disabled: itemForkDisabled,
      title: itemForkDisabled && itemForkHint ? itemForkHint : undefined,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        onFork(session.id);
      },
    },
    {
      key: "rename",
      label: t("common.rename"),
      icon: <Pencil size={14} />,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        setIsEditing(true);
      },
    },
    {
      key: "delete",
      label: t("common.delete", "Delete"),
      icon: <Trash2 size={14} />,
      danger: true,
      onClick: ({ domEvent }) => {
        domEvent.stopPropagation();
        showConfirmModal({
          title: t("chat.deleteSessionConfirm"),
          okText: t("common.delete"),
          cancelText: t("common.cancel"),
          okButtonProps: { danger: true },
          onOk: () => {
            onDelete(session.id);
          },
        });
      },
    },
  ];

  return (
    <div
      className={`${styles.sessionRow} ${
        isActive ? styles.sessionRowActive : ""
      } ${session.pinned ? styles.sessionRowPinned : ""}`}
      onClick={() => {
        if (!isEditing) onSelect(session.id);
      }}
      role="button"
      tabIndex={0}
      title={session.name}
      aria-label={session.name}
      onKeyDown={(e) => {
        if (isImeComposing(e)) return;
        if (e.key === "Enter" && !isEditing) onSelect(session.id);
      }}
    >
      <SessionChannelIcon
        channelType={session.channelType}
        size={12}
        className={styles.sessionRowIcon}
      />
      {working ? (
        <span
          className={styles.sessionRowWorkingDot}
          title={
            pending
              ? t("chat.sessionPending", "Starting…")
              : t("chat.sessionWorking")
          }
          aria-label={
            pending
              ? t("chat.sessionPending", "Starting…")
              : t("chat.sessionWorking")
          }
        />
      ) : null}
      {isEditing ? (
        <input
          ref={inputRef}
          className={styles.sessionNameInput}
          value={editValue}
          onChange={(e) => setEditValue(e.target.value)}
          onBlur={commitEdit}
          onKeyDown={(e) => {
            if (isImeComposing(e)) return;
            if (e.key === "Enter") commitEdit();
            if (e.key === "Escape") {
              setIsEditing(false);
            }
          }}
          onClick={(e) => e.stopPropagation()}
        />
      ) : (
        <>
          <span className={styles.sessionRowTitle}>{session.name}</span>
          {session.pinned ? (
            <span
              className={styles.sessionRowPinIndicator}
              title={t("chat.unpin")}
            >
              <Pin size={12} strokeWidth={2} />
            </span>
          ) : null}
          <Dropdown
            menu={{ items: menuItems }}
            trigger={["click"]}
            placement="bottomRight"
          >
            <button
              type="button"
              className={styles.sessionRowMore}
              aria-label={t("common.more", "More")}
              onClick={(e) => e.stopPropagation()}
            >
              <MoreHorizontal size={15} />
            </button>
          </Dropdown>
        </>
      )}
    </div>
  );
});

interface AgentCardProps {
  agent: OctopAgent;
  sessions: Session[];
  activeId: string | null;
  searchQuery: string;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onFetchAllSessions: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
  pinnedAgent: boolean;
  onToggleAgentPin: () => void;
  workingThreadIds: ReadonlySet<string>;
}

function ActiveAgentCard({
  agent,
  sessions,
  activeId,
  searchQuery,
  hasMore,
  loadingMore,
  onLoadMore,
  onFetchAllSessions,
  onSelect,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
  pinnedAgent,
  onToggleAgentPin,
  workingThreadIds,
}: AgentCardProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";

  const filteredSessions = useMemo(() => {
    const q = searchQuery.trim().toLowerCase();
    if (!q) return sessions;
    return sessions.filter((s) => s.name.toLowerCase().includes(q));
  }, [sessions, searchQuery]);

  const fetchAllRequestedRef = useRef(false);
  useEffect(() => {
    const searching = Boolean(searchQuery.trim());
    if (!searching) {
      fetchAllRequestedRef.current = false;
      return;
    }
    if (fetchAllRequestedRef.current) return;
    fetchAllRequestedRef.current = true;
    onFetchAllSessions();
  }, [searchQuery, onFetchAllSessions]);

  const showExpandMore = hasMore && !searchQuery.trim();
  const sessionsEnabled = isAgentChatReady(agent.state);

  return (
    <div
      className={styles.agentCardActive}
      style={{
        background: `${accent}08`,
        borderColor: `${accent}18`,
      }}
    >
      <div className={styles.agentCardProfile}>
        <div
          className={styles.agentCardAvatar}
          style={{
            color: accent,
            background: `${accent}14`,
            boxShadow: `0 0 0 1px ${accent}22`,
          }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={16}
          />
        </div>
        <div className={styles.agentCardInfo}>
          <div className={styles.agentCardNameRow}>
            <div className={styles.agentNameCluster}>
              <div className={styles.agentCardName}>{agent.name}</div>
              <SharedExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
            <button
              type="button"
              aria-label={pinnedAgent ? t("chat.unpin") : t("chat.pin")}
              title={pinnedAgent ? t("chat.unpin") : t("chat.pin")}
              onClick={onToggleAgentPin}
              style={{
                border: 0,
                background: "transparent",
                color: "var(--fn-text-tertiary)",
                cursor: "pointer",
              }}
            >
              {pinnedAgent ? <PinOff size={13} /> : <Pin size={13} />}
            </button>
          </div>
          {agent.description ? (
            <div className={styles.agentCardDesc}>{agent.description}</div>
          ) : (
            <div className={styles.agentCardDescMuted}>
              {t("chat.agentNoDescription", "暂无描述")}
            </div>
          )}
        </div>
      </div>

      <div className={styles.agentCardSessions}>
        {!sessionsEnabled ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.agentNotRunningHint")}
          </div>
        ) : sessions.length === 0 ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSessionsYet", "直接发消息即可开始对话")}
          </div>
        ) : filteredSessions.length === 0 ? (
          <div className={styles.agentCardSessionsEmpty}>
            {t("chat.noSearchResults", "没有匹配的会话")}
          </div>
        ) : (
          <>
            {filteredSessions.map((s) => (
              <SessionItem
                key={s.id}
                session={s}
                isActive={activeId === s.id}
                working={workingThreadIds.has(s.id)}
                pending={isPendingThread(s.id)}
                onSelect={(id) => onSelect(id, agent.agent_id)}
                onDelete={onDelete}
                onRename={onRename}
                onPin={onPin}
                onFork={onFork}
                forkDisabled={
                  activeId === s.id ? activeForkDisabled : undefined
                }
                forkDisabledHint={
                  activeId === s.id ? activeForkDisabledHint : undefined
                }
              />
            ))}
            {showExpandMore ? (
              <button
                type="button"
                className={styles.sessionLoadMore}
                onClick={onLoadMore}
                disabled={loadingMore}
              >
                {loadingMore
                  ? t("common.loading")
                  : t("chat.expandMore", "展开更多")}
              </button>
            ) : null}
          </>
        )}
      </div>
    </div>
  );
}

interface AgentRowProps {
  agent: OctopAgent;
  onSelect: () => void;
  pinnedAgent: boolean;
  onToggleAgentPin: () => void;
}

function InactiveAgentRow({
  agent,
  onSelect,
  pinnedAgent,
  onToggleAgentPin,
}: AgentRowProps) {
  const { t } = useTranslation();
  const accent = agent.color || "#6366f1";
  return (
    <div style={{ position: "relative" }}>
      <button type="button" className={styles.agentRow} onClick={onSelect}>
        <div
          className={styles.agentRowAvatar}
          style={{ color: accent, background: `${accent}12` }}
        >
          <ExpertIcon
            iconUrl={agent.icon_url}
            iconName={agent.icon_name}
            size={14}
          />
        </div>
        <div className={styles.agentRowInfo}>
          <div className={styles.agentRowNameRow}>
            <div className={styles.agentNameCluster}>
              <div className={styles.agentRowName}>{agent.name}</div>
              <SharedExpertHint agent={agent} />
            </div>
            <AgentUnreadBadge count={agent.unread_count ?? 0} />
          </div>
          <div className={styles.agentRowDesc}>{agent.description || "—"}</div>
        </div>
      </button>
      <button
        type="button"
        aria-label={
          pinnedAgent ? t("chat.unpin", "Unpin") : t("chat.pin", "Pin")
        }
        title={pinnedAgent ? t("chat.unpin", "Unpin") : t("chat.pin", "Pin")}
        onClick={onToggleAgentPin}
        style={{
          position: "absolute",
          right: 8,
          top: 8,
          border: 0,
          background: "transparent",
          color: "var(--fn-text-tertiary)",
          cursor: "pointer",
        }}
      >
        {pinnedAgent ? <PinOff size={13} /> : <Pin size={13} />}
      </button>
    </div>
  );
}
interface SessionListProps {
  agents: OctopAgent[];
  sessions: Session[];
  activeId: string | null;
  activeAgentId: string | null;
  hasMore: boolean;
  loadingMore: boolean;
  onLoadMore: () => void;
  onFetchAllSessions: () => void;
  onSelect: (sessionId: string, agentId: string) => void;
  onAgentSelect: (agentId: string) => void;
  onDelete: (id: string) => void;
  onRename: (id: string, name: string) => void;
  onPin: (id: string, pinned: boolean) => void;
  onFork: (id: string) => void;
  activeForkDisabled?: boolean;
  activeForkDisabledHint?: string;
}

export default function SessionList({
  agents,
  sessions,
  activeId,
  activeAgentId,
  hasMore,
  loadingMore,
  onLoadMore,
  onFetchAllSessions,
  onSelect,
  onAgentSelect,
  onDelete,
  onRename,
  onPin,
  onFork,
  activeForkDisabled,
  activeForkDisabledHint,
}: SessionListProps) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const currentUser = useCurrentUser();
  const emptyAgentAccess = emptyAgentAccessFor(currentUser);
  const emptyAgentAccessKeyName = emptyAgentAccessKey(emptyAgentAccess);
  const [searchQuery, setSearchQuery] = useState("");
  const [workingThreadIds, setWorkingThreadIds] = useState<ReadonlySet<string>>(
    () => new Set(),
  );
  useEffect(
    () =>
      onStreamEvent((event) => {
        setWorkingThreadIds((previous) => {
          const working = event.kind !== "streamEnd";
          if (previous.has(event.sessionId) === working) return previous;
          const next = new Set(previous);
          if (working) next.add(event.sessionId);
          else next.delete(event.sessionId);
          return next;
        });
      }),
    [],
  );
  const agentPinsStorageKey = `octop:sidebar-agent-pins:${
    currentUser?.id ?? "anonymous"
  }`;
  const [pinnedAgentIds, setPinnedAgentIds] = useState<Set<string>>(() => {
    try {
      return new Set(
        JSON.parse(localStorage.getItem(agentPinsStorageKey) || "[]"),
      );
    } catch {
      return new Set();
    }
  });
  useEffect(() => {
    try {
      setPinnedAgentIds(
        new Set(JSON.parse(localStorage.getItem(agentPinsStorageKey) || "[]")),
      );
    } catch {
      setPinnedAgentIds(new Set());
    }
  }, [agentPinsStorageKey]);
  const toggleAgentPin = (agentId: string) => {
    setPinnedAgentIds((previous) => {
      const next = new Set(previous);
      if (next.has(agentId)) next.delete(agentId);
      else next.add(agentId);
      try {
        localStorage.setItem(agentPinsStorageKey, JSON.stringify([...next]));
      } catch {
        /* ignore */
      }
      return next;
    });
  };

  const sortedAgents = useMemo(
    () =>
      [...agents].sort(
        (a, b) =>
          Number(pinnedAgentIds.has(b.agent_id)) -
            Number(pinnedAgentIds.has(a.agent_id)) || b.id - a.id,
      ),
    [agents, pinnedAgentIds],
  );

  const expandedAgentId = useMemo(
    () => activeAgentId ?? sortedAgents[0]?.agent_id ?? null,
    [activeAgentId, sortedAgents],
  );

  // The sidebar lists the caller's agents, and an agent says which half of them
  // it belongs to (``utils/agentKind``). Both halves are the same rows in the
  // same order; the features get a heading so a feature is readable as a feature
  // rather than as another expert. A caller with no feature renders the one
  // group this list has always rendered.
  const query = searchQuery.trim().toLowerCase();
  const matchesAgent = (agent: OctopAgent) =>
    !query ||
    agent.name.toLowerCase().includes(query) ||
    agent.agent_id === expandedAgentId;
  const expertRows = sortedAgents.filter(
    (agent) => !isFeatureAgent(agent) && matchesAgent(agent),
  );
  const featureRows = sortedAgents.filter(
    (agent) => isFeatureAgent(agent) && matchesAgent(agent),
  );

  const renderAgentRow = (agent: OctopAgent) =>
    agent.agent_id === expandedAgentId ? (
      <ActiveAgentCard
        key={agent.agent_id}
        agent={agent}
        sessions={sessions}
        workingThreadIds={workingThreadIds}
        activeId={activeId}
        searchQuery={searchQuery}
        hasMore={hasMore}
        loadingMore={loadingMore}
        onLoadMore={onLoadMore}
        onFetchAllSessions={onFetchAllSessions}
        onSelect={onSelect}
        onDelete={onDelete}
        onRename={onRename}
        onPin={onPin}
        onFork={onFork}
        activeForkDisabled={activeForkDisabled}
        activeForkDisabledHint={activeForkDisabledHint}
        pinnedAgent={pinnedAgentIds.has(agent.agent_id)}
        onToggleAgentPin={() => toggleAgentPin(agent.agent_id)}
      />
    ) : (
      <InactiveAgentRow
        key={agent.agent_id}
        agent={agent}
        onSelect={() => onAgentSelect(agent.agent_id)}
        pinnedAgent={pinnedAgentIds.has(agent.agent_id)}
        onToggleAgentPin={() => toggleAgentPin(agent.agent_id)}
      />
    );

  return (
    <div className={styles.sessionList}>
      <div className={styles.sessionSearchWrap}>
        <Search
          size={14}
          className={styles.sessionSearchIcon}
          strokeWidth={2}
        />
        <input
          type="search"
          className={styles.sessionSearchInput}
          value={searchQuery}
          onChange={(e) => setSearchQuery(e.target.value)}
          placeholder={t("chat.searchSessions", "搜索会话或专家")}
          aria-label={t("chat.searchSessions", "搜索会话或专家")}
        />
      </div>

      {agents.length === 0 ? (
        <div className={styles.sessionEmptyAgents}>
          <p className={styles.sessionEmptyAgentsText}>
            {t(`chat.noAgents${emptyAgentAccessKeyName}Hint`)}
          </p>
          {emptyAgentAccess !== "none" ? (
            <button
              type="button"
              className={styles.sessionEmptyAgentsLink}
              onClick={() => navigate(emptyAgentAccessPath(emptyAgentAccess))}
            >
              {t(`chat.noAgents${emptyAgentAccessKeyName}Action`)}
            </button>
          ) : null}
        </div>
      ) : (
        <div className={styles.sessionItems}>
          {expertRows.map(renderAgentRow)}
          {featureRows.length > 0 ? (
            <>
              <div className={styles.sessionGroupLabel}>
                {t("chat.featuresGroup", "功能")}
              </div>
              {featureRows.map(renderAgentRow)}
            </>
          ) : null}
        </div>
      )}
    </div>
  );
}
