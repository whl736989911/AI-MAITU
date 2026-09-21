/**
 * Rules panel — the review surface of the self-improvement loop.
 *
 * Every rule here was *proposed* by the extractor: nothing enters a prompt
 * before a human approves it, so the draft group is styled as a decision
 * waiting to be made rather than as ordinary list content. Provenance
 * (how many corrections it came from, which run ids, who proposed it) is shown
 * with each rule — a rule nobody can audit is a rule nobody should approve.
 *
 * A rule also lives in one of three layers, and *that* is the first thing this
 * panel has to say: your own rules (yours to approve, live immediately), your
 * department's, and the company's. Injection reads all three, narrowest first,
 * so the list is grouped the same way — and who may press approve on a rule
 * depends entirely on which layer it is in.
 */

import { useState } from "react";
import {
  Alert,
  Button,
  Input,
  Popconfirm,
  Select,
  Spin,
  Tag,
  Tooltip,
} from "antd";
import type { LucideIcon } from "lucide-react";
import {
  Building2,
  Check,
  Globe2,
  Send,
  Sparkles,
  UserRound,
  X,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import type { OctopUser } from "../../../api/modules/auth";
import type {
  FeatureRule,
  FeatureRuleScope,
  FeatureRuleStatus,
} from "../../../api/modules/features";
import { BRAND } from "../../../brand.generated";
import { EmptyState } from "../../../components/EmptyState";
import { useCurrentUser } from "../../../hooks/useCurrentUser";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { isSystemAdmin, userCan } from "../../../utils/permissions";
import styles from "../index.module.less";
import type { FeatureLearning, RuleSubmitTarget } from "./useFeatureLearning";

const STATUS_LABEL_KEY: Record<FeatureRuleStatus, string> = {
  draft: "features.ruleStatusDraft",
  approved: "features.ruleStatusApproved",
  rejected: "features.ruleStatusRejected",
};

/** Catalog order: what needs a decision first, then the recorded calls. */
const STATUS_ORDER: FeatureRuleStatus[] = ["draft", "approved", "rejected"];

/**
 * The three layers, narrowest first — the order injection reads them, so what
 * the panel shows on top is what the prompt honours first.
 */
const SCOPE_ORDER: FeatureRuleScope[] = ["personal", "unit", "global"];

const SCOPE_LABEL_KEY: Record<FeatureRuleScope, string> = {
  personal: "features.ruleScopePersonal",
  unit: "features.ruleScopeUnit",
  global: "features.ruleScopeGlobal",
};

/** One line per layer saying who the rules in it actually reach. */
const SCOPE_HINT_KEY: Record<FeatureRuleScope, string> = {
  personal: "features.ruleScopePersonalHint",
  unit: "features.ruleScopeUnitHint",
  global: "features.ruleScopeGlobalHint",
};

/** What picking this layer for an extraction will read. */
const SCOPE_EXTRACT_HINT_KEY: Record<FeatureRuleScope, string> = {
  personal: "features.rulesExtractHintPersonal",
  unit: "features.rulesExtractHintUnit",
  global: "features.rulesExtractHintGlobal",
};

/** The same three faces the sharing page uses for its unit/org scopes. */
const SCOPE_ICON: Record<FeatureRuleScope, LucideIcon> = {
  personal: UserRound,
  unit: Building2,
  global: Globe2,
};

/**
 * Which layer a rule speaks for. Grouping already says this, but a card is
 * read on its own: the colour and the icon make the layer legible without
 * looking up at the heading.
 */
function ScopeTag({ scope }: { scope: FeatureRuleScope }) {
  const { t } = useTranslation();
  const Icon = SCOPE_ICON[scope];
  return (
    <Tooltip title={t(SCOPE_HINT_KEY[scope])}>
      <Tag
        color={
          scope === "global"
            ? BRAND.color.accent
            : scope === "unit"
            ? "blue"
            : undefined
        }
        icon={<Icon size={12} />}
        style={{ marginInlineEnd: 0 }}
      >
        {t(SCOPE_LABEL_KEY[scope])}
      </Tag>
    </Tooltip>
  );
}

/**
 * The layers this user may induce rules from. Reading other people's
 * corrections is a wider act than reading your own, so an option they cannot
 * use is never offered — the server refuses it too.
 */
function extractScopes(user: OctopUser | null): FeatureRuleScope[] {
  if (!userCan(user, "features")) return [];
  const scopes: FeatureRuleScope[] = ["personal"];
  // A unit extraction reads the caller's *own* unit, so it needs one — and
  // either the role that administers it or the admin bypass.
  if (user?.org_unit && (isSystemAdmin(user) || user.role === "unit_admin")) {
    scopes.push("unit");
  }
  if (isSystemAdmin(user)) scopes.push("global");
  return scopes;
}

/** Where this rule may be submitted to; empty means no submit button at all. */
function submitTargets(
  rule: FeatureRule,
  user: OctopUser | null,
): RuleSubmitTarget[] {
  if (!user) return [];
  // Only your own personal rule can be lifted: a unit or global rule is
  // already where submitting would put it.
  if (rule.scope !== "personal" || rule.owner_user_id !== user.id) return [];
  const targets: RuleSubmitTarget[] = [];
  // The copy lands in the submitter's current unit, so a unit-less account has
  // nowhere to send it.
  if (user.org_unit) targets.push("unit");
  if (isSystemAdmin(user)) targets.push("global");
  return targets;
}

/**
 * Whether this user may decide on this rule. Mirrors the server's gates: your
 * own personal rule, a department rule when you administer *that* department,
 * a company rule for the ``admin`` role alone — holding the ``features``
 * permission is never enough to approve something wider than yourself.
 *
 * The caller hides the buttons when this is false; it never disables them, so
 * no button is offered that could only fail.
 */
function canReviewRule(rule: FeatureRule, user: OctopUser | null): boolean {
  if (!userCan(user, "features")) return false;
  switch (rule.scope) {
    case "personal":
      return rule.owner_user_id !== null && rule.owner_user_id === user?.id;
    case "unit":
      return (
        isSystemAdmin(user) ||
        (user?.role === "unit_admin" && rule.unit_key === user.org_unit)
      );
    case "global":
      return isSystemAdmin(user);
  }
}

function RuleCard({
  rule,
  learning,
}: {
  rule: FeatureRule;
  learning: FeatureLearning;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const user = useCurrentUser();
  const [reason, setReason] = useState("");
  const reviewing = learning.reviewingRuleId === rule.id;
  const submitting = learning.submittingRuleId === rule.id;
  const draft = rule.status === "draft";
  const sources = rule.source_task_ids;
  const reviewable = canReviewRule(rule, user);
  const targets = submitTargets(rule, user);
  const unit = rule.unit_label ?? rule.unit_key;

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
        <ScopeTag scope={rule.scope} />
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
        {rule.scope === "unit" && unit !== null && (
          <span className={styles.ruleMetaItem}>
            {t("features.ruleUnitLabel", { unit })}
          </span>
        )}
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

      {(reviewable && draft) || targets.length > 0 ? (
        <div className={styles.ruleActions}>
          {reviewable && draft && (
            <>
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
            </>
          )}
          {targets.includes("unit") && (
            <Popconfirm
              title={t("features.ruleSubmitUnitTitle")}
              description={
                <div className={styles.promoteConfirm}>
                  <div>{t("features.ruleSubmitUnitDesc")}</div>
                  <Input.TextArea
                    value={reason}
                    placeholder={t("features.ruleSubmitReasonPlaceholder")}
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </div>
              }
              okText={t("features.ruleSubmitConfirm")}
              cancelText={t("common.cancel")}
              onConfirm={() => void learning.submitRule(rule, "unit", reason)}
            >
              <Button
                size="small"
                icon={<Send size={12} />}
                loading={submitting}
              >
                {t("features.ruleSubmitToUnit")}
              </Button>
            </Popconfirm>
          )}
          {targets.includes("global") && (
            <Popconfirm
              title={t("features.ruleSubmitGlobalTitle")}
              description={
                <div className={styles.promoteConfirm}>
                  <div>{t("features.ruleSubmitGlobalDesc")}</div>
                  <Input.TextArea
                    value={reason}
                    placeholder={t("features.ruleSubmitReasonPlaceholder")}
                    autoSize={{ minRows: 2, maxRows: 4 }}
                    onChange={(event) => setReason(event.target.value)}
                  />
                </div>
              }
              okText={t("features.ruleSubmitConfirm")}
              cancelText={t("common.cancel")}
              onConfirm={() => void learning.submitRule(rule, "global", reason)}
            >
              <Button
                size="small"
                icon={<Send size={12} />}
                loading={submitting}
              >
                {t("features.ruleSubmitToGlobal")}
              </Button>
            </Popconfirm>
          )}
        </div>
      ) : null}
    </article>
  );
}

export default function RulesPanel({
  learning,
}: {
  learning: FeatureLearning;
}) {
  const { t } = useTranslation();
  const user = useCurrentUser();
  const { rules, rulesLoading, extracting, extractNotice, submitNotice } =
    learning;
  const scopes = extractScopes(user);
  const [extractScope, setExtractScope] =
    useState<FeatureRuleScope>("personal");

  return (
    <div className={styles.panel}>
      <div className={styles.panelHead}>
        <div className={styles.panelHeading}>
          <div className={styles.panelTitle}>{t("features.rulesTitle")}</div>
          <div className={styles.panelHint}>{t("features.rulesSubtitle")}</div>
        </div>
        {scopes.length > 0 && (
          <div className={styles.extractControls}>
            <span className={styles.extractHint}>
              {t(SCOPE_EXTRACT_HINT_KEY[extractScope])}
            </span>
            <Select
              className={styles.extractScope}
              size="small"
              value={extractScope}
              aria-label={t("features.rulesExtractScopeLabel")}
              onChange={setExtractScope}
              options={scopes.map((scope) => ({
                value: scope,
                label: t(SCOPE_LABEL_KEY[scope]),
              }))}
            />
            <Button
              className={styles.extractButton}
              icon={<Sparkles size={14} />}
              loading={extracting}
              onClick={() => void learning.extractRules(extractScope)}
            >
              {t("features.rulesExtract")}
            </Button>
          </div>
        )}
      </div>

      {extractNotice && (
        <Alert
          type={extractNotice.kind}
          showIcon
          message={t(extractNotice.titleKey)}
          description={extractNotice.detail}
        />
      )}

      {submitNotice && (
        <Alert
          type={submitNotice.kind}
          showIcon
          message={t(submitNotice.titleKey)}
          description={submitNotice.detail}
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
        SCOPE_ORDER.map((scope) => {
          const group = rules
            .filter((rule) => rule.scope === scope)
            .sort(
              (a, b) =>
                STATUS_ORDER.indexOf(a.status) - STATUS_ORDER.indexOf(b.status),
            );
          if (group.length === 0) return null;
          return (
            <section className={styles.group} key={scope}>
              <div className={styles.groupHead}>
                <h3 className={styles.groupTitle}>
                  {t(SCOPE_LABEL_KEY[scope])}
                </h3>
                <span className={styles.groupCount}>{group.length}</span>
                <span className={styles.groupHint}>
                  {t(SCOPE_HINT_KEY[scope])}
                </span>
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
