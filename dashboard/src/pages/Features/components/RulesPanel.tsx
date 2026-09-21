/**
 * Rules panel — the review surface of the self-improvement loop.
 *
 * Every rule here was *proposed* by the extractor: nothing enters a prompt
 * before a human approves it, so the draft group is styled as a decision
 * waiting to be made rather than as ordinary list content. Provenance
 * (how many corrections it came from, which run ids, who proposed it) is shown
 * with each rule — a rule nobody can audit is a rule nobody should approve.
 */

import { Alert, Button, Popconfirm, Spin, Tag, Tooltip } from "antd";
import { Check, Sparkles, X } from "lucide-react";
import { useTranslation } from "react-i18next";
import type {
  FeatureRule,
  FeatureRuleStatus,
} from "../../../api/modules/features";
import { EmptyState } from "../../../components/EmptyState";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import styles from "../index.module.less";
import type { FeatureLearning } from "./useFeatureLearning";

const STATUS_LABEL_KEY: Record<FeatureRuleStatus, string> = {
  draft: "features.ruleStatusDraft",
  approved: "features.ruleStatusApproved",
  rejected: "features.ruleStatusRejected",
};

/** Catalog order: what needs a decision first, then the recorded calls. */
const STATUS_ORDER: FeatureRuleStatus[] = ["draft", "approved", "rejected"];

function RuleCard({
  rule,
  learning,
}: {
  rule: FeatureRule;
  learning: FeatureLearning;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const reviewing = learning.reviewingRuleId === rule.id;
  const draft = rule.status === "draft";
  const sources = rule.source_task_ids;

  return (
    <article
      className={`${styles.ruleCard} ${
        draft
          ? styles.ruleCardDraft
          : rule.status === "approved"
          ? styles.ruleCardApproved
          : styles.ruleCardRejected
      }`}
    >
      <div className={styles.ruleTop}>
        <Tag className={styles.aiTag} icon={<Sparkles size={11} />}>
          {t("features.ruleAiProposed")}
        </Tag>
        <Tag
          className={`${styles.ruleStateTag} ${
            draft
              ? styles.tagDraft
              : rule.status === "approved"
              ? styles.tagApproved
              : styles.tagRejected
          }`}
        >
          {t(STATUS_LABEL_KEY[rule.status])}
        </Tag>
      </div>

      <p className={styles.ruleText}>{rule.rule_text}</p>
      {draft && (
        <div className={styles.ruleNote}>{t("features.ruleNeedsReview")}</div>
      )}

      <div className={styles.ruleMeta}>
        <span className={styles.ruleMetaItem}>
          {sources.length > 0 ? (
            <Tooltip title={sources.join(", ")}>
              <span>
                {t("features.ruleSources", { count: sources.length })}
              </span>
            </Tooltip>
          ) : (
            t("features.ruleSources", { count: 0 })
          )}
        </span>
        <span className={styles.ruleMetaItem}>
          {rule.proposed_by === "ai"
            ? t("features.ruleProposedByAi")
            : t("features.ruleProposedByUser", { id: rule.proposed_by })}
        </span>
        <span className={styles.ruleMetaItem}>
          {t("features.ruleCreatedAt", {
            time: formatServerDateTime(rule.created_at, timeZone),
          })}
        </span>
        {rule.reviewed_at !== null && (
          <span className={styles.ruleMetaItem}>
            {t("features.ruleReviewedAt", {
              time: formatServerDateTime(rule.reviewed_at, timeZone),
            })}
          </span>
        )}
        {rule.approved_by !== null && (
          <span className={styles.ruleMetaItem}>
            {t("features.ruleReviewedBy", { id: rule.approved_by })}
          </span>
        )}
      </div>

      {draft && (
        <div className={styles.ruleActions}>
          <Popconfirm
            title={t("features.ruleApproveTitle")}
            description={t("features.ruleApproveDesc")}
            okText={t("features.ruleApprove")}
            cancelText={t("common.cancel")}
            onConfirm={() => void learning.reviewRule(rule, true)}
          >
            <Button
              size="small"
              type="primary"
              icon={<Check size={12} />}
              loading={reviewing}
            >
              {t("features.ruleApprove")}
            </Button>
          </Popconfirm>
          <Popconfirm
            title={t("features.ruleRejectTitle")}
            description={t("features.ruleRejectDesc")}
            okText={t("features.ruleReject")}
            cancelText={t("common.cancel")}
            okButtonProps={{ danger: true }}
            onConfirm={() => void learning.reviewRule(rule, false)}
          >
            <Button
              size="small"
              danger
              icon={<X size={12} />}
              loading={reviewing}
            >
              {t("features.ruleReject")}
            </Button>
          </Popconfirm>
        </div>
      )}
    </article>
  );
}

export default function RulesPanel({
  learning,
}: {
  learning: FeatureLearning;
}) {
  const { t } = useTranslation();
  const { rules, rulesLoading, extracting, extractNotice } = learning;

  return (
    <div className={styles.panel}>
      <div className={styles.panelHead}>
        <div className={styles.panelHeading}>
          <div className={styles.panelTitle}>{t("features.rulesTitle")}</div>
          <div className={styles.panelHint}>{t("features.rulesSubtitle")}</div>
        </div>
        <Button
          className={styles.extractButton}
          icon={<Sparkles size={14} />}
          loading={extracting}
          onClick={() => void learning.extractRules()}
        >
          {t("features.rulesExtract")}
        </Button>
      </div>

      {extractNotice && (
        <Alert
          type={extractNotice.kind}
          showIcon
          message={t(extractNotice.titleKey)}
          description={extractNotice.detail}
        />
      )}

      {rulesLoading && rules.length === 0 ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : rules.length === 0 ? (
        <EmptyState
          title={t("features.rulesNone")}
          description={t("features.rulesNoneHint")}
        />
      ) : (
        STATUS_ORDER.map((status) => {
          const group = rules.filter((rule) => rule.status === status);
          if (group.length === 0) return null;
          return (
            <section className={styles.group} key={status}>
              <div className={styles.groupHead}>
                <h3 className={styles.groupTitle}>
                  {t(STATUS_LABEL_KEY[status])}
                </h3>
                <span className={styles.groupCount}>{group.length}</span>
              </div>
              <div className={styles.ruleList}>
                {group.map((rule) => (
                  <RuleCard key={rule.id} rule={rule} learning={learning} />
                ))}
              </div>
            </section>
          );
        })
      )}
    </div>
  );
}
