import { Select, Spin } from "antd";
import { useEffect, useMemo } from "react";
import { useTranslation } from "react-i18next";
import { useAgent, type OctopAgent } from "../context/AgentContext";
import { isFeatureAgent } from "../utils/agentKind";
import { ownedExperts, ownedFeatures } from "../utils/sharedExpert";
import { iconForName } from "../pages/Experts/components/iconForName";
import styles from "./AgentSelector.module.less";

interface AgentSelectorProps {
  style?: React.CSSProperties;
  className?: string;
  /** auto = chips when ≤6 agents, otherwise select */
  variant?: "auto" | "select" | "bar";
  showLabel?: boolean;
  /**
   * Which half of the caller's agents this bar offers: their experts (the
   * default, and everything an agent-scoped page has always shown) or their
   * features. A feature's agent is told apart by its kind — see
   * ``utils/agentKind`` — so a surface that needs both puts two of these side by
   * side rather than teaching one bar to sort them.
   */
  scope?: "experts" | "features";
}

function agentAccent(agent: OctopAgent): string {
  const cfg = agent.config ?? {};
  const fromConfig = typeof cfg.color === "string" ? cfg.color : null;
  return agent.color || fromConfig || "#2563eb";
}

function AgentChip({
  agent,
  active,
  onSelect,
}: {
  agent: OctopAgent;
  active: boolean;
  onSelect: (id: string) => void;
}) {
  const accent = agentAccent(agent);
  return (
    <button
      type="button"
      className={active ? styles.chipActive : styles.chip}
      style={{ "--chip-accent": accent } as React.CSSProperties}
      onClick={() => onSelect(agent.agent_id)}
      title={agent.description ?? agent.name}
    >
      <span className={styles.chipIcon}>
        {iconForName(agent.icon_name, 12)}
      </span>
      <span className={styles.chipName}>{agent.name}</span>
      <span className={styles.stateDot} data-state={agent.state} />
    </button>
  );
}

/**
 * Agent picker for agent-scoped pages. Persists selection via AgentContext.
 */
export default function AgentSelector({
  style,
  className,
  variant = "auto",
  showLabel = true,
  scope = "experts",
}: AgentSelectorProps) {
  const { t } = useTranslation();
  const { agents, activeAgentId, setActiveAgent, loading } = useAgent();
  const featuresBar = scope === "features";
  const selectable = useMemo(
    () => (featuresBar ? ownedFeatures(agents) : ownedExperts(agents)),
    [agents, featuresBar],
  );

  useEffect(() => {
    if (loading || selectable.length === 0) return;
    if (
      activeAgentId &&
      selectable.some((agent) => agent.agent_id === activeAgentId)
    ) {
      return;
    }
    // Neither bar pulls the page off the other half's choice: the experts' bar
    // keeps adopting a default (as it always has) except while the pointer is
    // already on a feature — the one selection only the features' bar can have
    // made — and the features' bar never adopts. Choosing a feature is a
    // decision, not a default.
    const activeRow = agents.find((agent) => agent.agent_id === activeAgentId);
    if (featuresBar || (activeRow && isFeatureAgent(activeRow))) return;
    setActiveAgent(selectable[0]?.agent_id ?? null);
  }, [activeAgentId, agents, loading, selectable, setActiveAgent, featuresBar]);

  if (loading) {
    return (
      <div className={`${styles.wrap} ${className ?? ""}`} style={style}>
        <Spin size="small" />
      </div>
    );
  }

  if (selectable.length === 0) return null;

  /**
   * What this bar names. With no pointer at all it previews its first choice —
   * what the effect above is about to adopt. With the pointer on the *other*
   * half it names nothing: showing a row that is not among its own choices would
   * claim a scope this bar does not hold.
   */
  const current =
    selectable.find((agent) => agent.agent_id === activeAgentId) ??
    (activeAgentId ? undefined : selectable[0]);
  const currentId = current?.agent_id;
  const label = t(
    featuresBar ? "agentSelector.featureLabel" : "agentSelector.label",
  );
  const useBar =
    variant === "bar" || (variant === "auto" && selectable.length <= 6);

  return (
    <div className={`${styles.wrap} ${className ?? ""}`} style={style}>
      {showLabel && <span className={styles.label}>{label}</span>}

      {useBar ? (
        <div className={styles.bar} role="tablist" aria-label={label}>
          {selectable.map((agent) => (
            <AgentChip
              key={agent.agent_id}
              agent={agent}
              active={agent.agent_id === currentId}
              onSelect={setActiveAgent}
            />
          ))}
        </div>
      ) : (
        <Select
          className={styles.select}
          value={currentId}
          placeholder={
            featuresBar ? t("agentSelector.featurePlaceholder") : undefined
          }
          onChange={(id) => setActiveAgent(id)}
          listHeight={360}
          popupMatchSelectWidth={320}
          optionLabelProp="label"
          options={selectable.map((agent) => {
            const accent = agentAccent(agent);
            return {
              value: agent.agent_id,
              label: (
                <span className={styles.optionRow}>
                  <span className={styles.optionIcon} style={{ color: accent }}>
                    {iconForName(agent.icon_name, 12)}
                  </span>
                  <span className={styles.chipName}>{agent.name}</span>
                </span>
              ),
              title: agent.name,
            };
          })}
          optionRender={(opt) => {
            const agent = selectable.find((a) => a.agent_id === opt.value);
            if (!agent) return opt.label;
            const accent = agentAccent(agent);
            return (
              <div className={styles.optionRowMulti}>
                <span className={styles.optionIcon} style={{ color: accent }}>
                  {iconForName(agent.icon_name, 12)}
                </span>
                <div className={styles.optionMeta}>
                  <div className={styles.optionName}>{agent.name}</div>
                  {agent.description ? (
                    <div className={styles.optionDesc}>{agent.description}</div>
                  ) : null}
                </div>
                <span className={styles.stateDot} data-state={agent.state} />
              </div>
            );
          }}
        />
      )}
    </div>
  );
}
