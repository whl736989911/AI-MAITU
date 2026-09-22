/**
 * The feature page's scope bar — which feature these surfaces are configuring,
 * and the way to another one.
 *
 * The Personalization page answers the same question with ``AgentSelector``: a row
 * under the title naming the agent the panels below are pointed at, and a control
 * to change it. This is that row for a feature's own page, and it differs in the
 * one way the model differs: a feature's scope is the page's route, so picking
 * another feature navigates to it rather than moving a global selection.
 *
 * That is deliberate, and it is what keeps the expert side unchanged. Flipping the
 * app's active agent onto a feature would move every other agent-scoped surface
 * with it — chat's composer, the Personalization page's own scope — and for a
 * caller it would point an expert's configuration page at an agent they may not
 * write. A feature's page is a page *about* one feature; where the pointer sits is
 * not its business.
 *
 * It wears the agents' own selector stylesheet — the same label, the same option
 * rows, the same state dot — because it is that control; what it names is not the
 * same thing, and the label says so.
 */

import { Select, Tag } from "antd";
import { useTranslation } from "react-i18next";
import type { OctopAgent } from "../../../context/AgentContext";
import { ExpertIcon } from "../../Experts/components/iconForName";
import styles from "../../../components/AgentSelector.module.less";

export interface FeatureScopeBarProps {
  /** The feature this page is about. */
  feature: OctopAgent;
  /** The caller's own features — the ones they may switch to. */
  ownFeatures: OctopAgent[];
  /** Open another feature's page. */
  onSwitch: (agentId: string) => void;
}

export default function FeatureScopeBar({
  feature,
  ownFeatures,
  onSwitch,
}: FeatureScopeBarProps) {
  const { t } = useTranslation();

  /**
   * The feature on screen is always among the choices, even for a caller who
   * cannot switch to it: the bar exists to name the scope, and a control that
   * dropped the current one would leave the page unable to say what it is
   * configuring.
   */
  const choices = [
    feature,
    ...ownFeatures.filter((agent) => agent.agent_id !== feature.agent_id),
  ];

  return (
    <div className={styles.wrap}>
      <span className={styles.label}>{t("features.scopeLabel")}</span>

      <Select
        size="small"
        className={styles.select}
        value={feature.agent_id}
        onChange={onSwitch}
        listHeight={360}
        popupMatchSelectWidth={320}
        optionLabelProp="label"
        options={choices.map((agent) => ({
          value: agent.agent_id,
          label: (
            <span className={styles.optionRow}>
              <span className={styles.optionIcon}>
                <ExpertIcon
                  iconUrl={agent.icon_url}
                  iconName={agent.icon_name}
                  size={agent.icon_url ? 14 : 11}
                />
              </span>
              <span className={styles.chipName}>{agent.name}</span>
              <span className={styles.stateDot} data-state={agent.state} />
            </span>
          ),
          title: agent.name,
        }))}
        optionRender={(opt) => {
          const agent = choices.find((a) => a.agent_id === opt.value);
          if (!agent) return opt.label;
          return (
            <div className={styles.optionRowMulti}>
              <span className={styles.optionIcon}>
                <ExpertIcon
                  iconUrl={agent.icon_url}
                  iconName={agent.icon_name}
                  size={agent.icon_url ? 14 : 11}
                />
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

      {/* The agent id, said out loud: it is the id every panel below runs on, and
          it is what a channel binding or a log line will name. */}
      <code style={{ fontSize: 12, color: "var(--fn-text-tertiary)" }}>
        {feature.agent_id}
      </code>

      {feature.is_owner === false && (
        <Tag color="blue">
          {t("features.definedBy", {
            name: feature.owner_username ?? t("features.unknownAuthor"),
          })}
        </Tag>
      )}
    </div>
  );
}
