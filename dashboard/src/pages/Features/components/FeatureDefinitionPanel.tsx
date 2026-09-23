/**
 * The definition — the part of a feature that an expert has no tab for.
 *
 * An expert's identity (name, description, avatar, model, welcome, and the
 * knowledge bases and connectors a conversation opens with) is edited in
 * ``EditAgentDrawer`` from the Experts page, and its capabilities are edited on the
 * Personalization page: two surfaces, neither of which shows what the other holds.
 * A feature has one page, and this is the half of it that says what the feature
 * *is* — so it is the drawer the experts are edited with, opened from a tab that
 * states what it will find there.
 *
 * Nothing here is written inline and nothing is duplicated: this panel reads the
 * agent row the page already has and hands the editing to the experts' own editor.
 * That editor writes through ``PATCH /agents/{id}``, which for a feature row is
 * gated on the row's own kind and owner (``api/common/agent.py``) — the author
 * writes it, everyone else is refused — and this panel offers it to the author
 * only, so a caller is never shown a control whose only outcome is a refusal.
 *
 * What a caller sees instead is the same facts, read, and the reason there is
 * nothing to change: the definition belongs to whoever defined the feature.
 */

import { useState } from "react";
import { Alert, Descriptions, Tag, Typography } from "antd";
import { Pencil } from "lucide-react";
import { useTranslation } from "react-i18next";

import type { OctopAgent } from "../../../context/AgentContext";
import { formatAgentState } from "../../../utils/agentError";
import { ExpertIcon } from "../../Experts/components/iconForName";
import EditAgentDrawer from "../../Experts/components/EditAgentDrawer";
import styles from "../index.module.less";

export interface FeatureDefinitionPanelProps {
  feature: OctopAgent;
  /** Whether the caller may write it — the author, read off the row's ``is_owner``. */
  canWrite: boolean;
  /** Re-read ``/agents`` after a write, so the page follows the server's copy. */
  onSaved: () => void;
}

export default function FeatureDefinitionPanel({
  feature,
  canWrite,
  onSaved,
}: FeatureDefinitionPanelProps) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const accent = feature.color || "#6366f1";
  /**
   * Who defined it, from the row: its owner. ``owner_username`` is only filled in
   * for agents somebody else owns (it is the *shared* owner's name), so the
   * author's own view of their feature would otherwise read as an unknown one.
   */
  const authorName =
    feature.owner_username ??
    (feature.is_owner === false
      ? t("features.unknownAuthor")
      : t("features.authorIsYou"));

  return (
    <div className={styles.definition}>
      {!canWrite && (
        <Alert
          type="info"
          showIcon
          style={{ marginBottom: 12 }}
          message={t("features.definitionAuthorOnly", { author: authorName })}
        />
      )}

      <div className={styles.definitionHeader}>
        <div
          className={styles.definitionIcon}
          style={{ color: accent, background: `${accent}1a` }}
        >
          <ExpertIcon
            iconUrl={feature.icon_url}
            iconName={feature.icon_name}
            size={feature.icon_url ? 42 : 22}
          />
        </div>

        <div className={styles.definitionTitleBlock}>
          <div className={styles.definitionName}>{feature.name}</div>
          <Typography.Text type="secondary" style={{ fontSize: 13 }}>
            {feature.description || t("features.noDescription")}
          </Typography.Text>
        </div>

        {canWrite && (
          <button
            type="button"
            className={styles.definitionEdit}
            onClick={() => setEditing(true)}
          >
            <Pencil size={13} />
            {t("common.edit")}
          </button>
        )}
      </div>

      <Descriptions
        size="small"
        column={{ xs: 1, sm: 1, md: 2 }}
        style={{ marginTop: 16 }}
        items={[
          {
            key: "agentId",
            label: t("features.agentId"),
            children: <code>{feature.agent_id}</code>,
          },
          {
            key: "author",
            label: t("features.author"),
            children: authorName,
          },
          {
            key: "state",
            label: t("features.state"),
            children: <Tag>{formatAgentState(feature.state, t)}</Tag>,
          },
          {
            key: "model",
            label: t("features.defaultModel"),
            children: feature.default_model ?? t("experts.defaultModelAuto"),
          },
          {
            key: "welcome",
            label: t("features.welcome"),
            children: feature.welcome_message || t("features.notSet"),
          },
          {
            key: "knowledge",
            label: t("features.knowledgeBases"),
            children:
              feature.knowledge_base_ids &&
              feature.knowledge_base_ids.length > 0
                ? feature.knowledge_base_ids.join(", ")
                : t("features.notSet"),
          },
          {
            key: "connectors",
            label: t("features.connectors"),
            children:
              feature.mcp_servers && feature.mcp_servers.length > 0
                ? feature.mcp_servers.join(", ")
                : t("features.notSet"),
          },
        ]}
      />

      {/* The experts' own editor, for a feature's row — not a second form. */}
      <EditAgentDrawer
        open={editing}
        agent={feature}
        titleKey="features.editDefinition"
        onClose={() => setEditing(false)}
        onSaved={() => {
          setEditing(false);
          onSaved();
        }}
      />
    </div>
  );
}
