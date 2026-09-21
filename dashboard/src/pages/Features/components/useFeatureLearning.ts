/**
 * Self-improvement state of one feature: the finalize handoff that turns a
 * draft into learning signal, the rules induced from it, and the promoted
 * case library.
 *
 * The page owns the run and the draft editor; everything the loop *persists*
 * lives here, so an action taken in one pane (promote, approve) immediately
 * agrees with the next render of the others.
 *
 * Two backend answers are not failures in the user's sense and are handled as
 * hints instead of error toasts:
 *   - ``FEATURE_TASK_FINALIZED`` (409) — another tab finalized this run first.
 *   - ``FEATURE_RULE_NO_SAMPLES`` (409) — there is simply nothing to learn from yet.
 */

import { useCallback, useState } from "react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureCase,
  type FeatureFinalizedTask,
  type FeatureRule,
} from "../../../api/modules/features";
import { useAsyncResource } from "../../../hooks/useAsyncResource";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import { message } from "@/utils/antdMessage";

const EMPTY_RULES: FeatureRule[] = [];
const EMPTY_CASES: FeatureCase[] = [];

/** A backend answer the user should read as guidance, not as an error. */
export interface LearningNotice {
  kind: "info" | "warning";
  /** i18n key of the short headline; the panel renders it. */
  titleKey: string;
  /** Server-supplied detail, when it adds anything to the headline. */
  detail?: string;
}

export interface FeatureLearning {
  rules: FeatureRule[];
  rulesLoading: boolean;
  /** Draft rules still waiting for a human call — drives the tab badge. */
  pendingRules: number;
  refreshRules: () => Promise<void>;
  extracting: boolean;
  extractRules: () => Promise<void>;
  extractNotice: LearningNotice | null;
  reviewingRuleId: string | null;
  reviewRule: (rule: FeatureRule, approve: boolean) => Promise<void>;

  cases: FeatureCase[];
  casesLoading: boolean;
  refreshCases: () => Promise<void>;

  /** Finalized record of the run on screen, ``null`` while it is still a draft. */
  finalized: FeatureFinalizedTask | null;
  finalizing: boolean;
  /** Friendly text shown when the run was finalized by someone else first. */
  finalizeConflict: string | null;
  finalize: (taskId: string, final: string) => Promise<void>;
  /** Forget the previous run's lifecycle before a new one starts. */
  resetRun: () => void;

  promotedTaskId: string | null;
  promoting: boolean;
  promote: (taskId: string, note?: string) => Promise<void>;
}

export function useFeatureLearning(
  featureId: string | undefined,
): FeatureLearning {
  const { t } = useTranslation();
  const id = featureId ?? "";
  const enabled = Boolean(featureId);

  const {
    data: rules,
    loading: rulesLoading,
    refresh: refreshRules,
    setData: setRules,
  } = useAsyncResource(
    EMPTY_RULES,
    async () => (await featuresApi.listRules(id)).rules,
    [id],
    {
      enabled,
      errorFallback: t("features.rulesLoadFailed"),
      t,
      logLabel: "features.rules",
    },
  );

  const {
    data: cases,
    loading: casesLoading,
    refresh: refreshCases,
  } = useAsyncResource(
    EMPTY_CASES,
    async () => (await featuresApi.listCases(id)).cases,
    [id],
    {
      enabled,
      errorFallback: t("features.casesLoadFailed"),
      t,
      logLabel: "features.cases",
    },
  );

  const [finalized, setFinalized] = useState<FeatureFinalizedTask | null>(null);
  const [finalizing, setFinalizing] = useState(false);
  const [finalizeConflict, setFinalizeConflict] = useState<string | null>(null);
  const [promotedTaskId, setPromotedTaskId] = useState<string | null>(null);
  const [promoting, setPromoting] = useState(false);
  const [extracting, setExtracting] = useState(false);
  const [extractNotice, setExtractNotice] = useState<LearningNotice | null>(
    null,
  );
  const [reviewingRuleId, setReviewingRuleId] = useState<string | null>(null);

  const resetRun = useCallback(() => {
    setFinalized(null);
    setFinalizeConflict(null);
    setPromotedTaskId(null);
  }, []);

  const finalize = useCallback(
    async (taskId: string, final: string) => {
      setFinalizing(true);
      setFinalizeConflict(null);
      try {
        setFinalized(await featuresApi.finalizeTask(taskId, final));
        message.success(t("features.finalized"));
      } catch (err) {
        if (parseApiError(err)?.code === "FEATURE_TASK_FINALIZED") {
          // The server is right: the stored final is whoever got there first,
          // so the editor locks rather than offering an edit that cannot save.
          setFinalizeConflict(
            apiErrorMessage(err, t("features.finalizedConflictTitle"), t),
          );
        } else {
          message.error(apiErrorMessage(err, t("features.finalizeFailed"), t));
        }
      } finally {
        setFinalizing(false);
      }
    },
    [t],
  );

  const promote = useCallback(
    async (taskId: string, note?: string) => {
      setPromoting(true);
      try {
        await featuresApi.promoteTask(taskId, note);
        setPromotedTaskId(taskId);
        message.success(t("features.promoted"));
        await refreshCases();
      } catch (err) {
        if (parseApiError(err)?.code === "FEATURE_TASK_NOT_FINALIZED") {
          message.warning(
            apiErrorMessage(err, t("features.promoteNeedsFinal"), t),
          );
        } else {
          message.error(apiErrorMessage(err, t("features.promoteFailed"), t));
        }
      } finally {
        setPromoting(false);
      }
    },
    [refreshCases, t],
  );

  const reviewRule = useCallback(
    async (rule: FeatureRule, approve: boolean) => {
      setReviewingRuleId(rule.id);
      try {
        const updated = await (approve
          ? featuresApi.approveRule(rule.id)
          : featuresApi.rejectRule(rule.id));
        setRules((previous) =>
          previous.map((item) => (item.id === updated.id ? updated : item)),
        );
        message.success(
          approve ? t("features.ruleApproved") : t("features.ruleRejected"),
        );
      } catch (err) {
        if (parseApiError(err)?.code === "FEATURE_RULE_REVIEWED") {
          // Someone else decided first; their call stands, so reload rather
          // than leave a button that can only fail again.
          message.warning(
            apiErrorMessage(err, t("features.ruleReviewConflict"), t),
          );
          await refreshRules();
        } else {
          message.error(
            apiErrorMessage(err, t("features.ruleReviewFailed"), t),
          );
        }
      } finally {
        setReviewingRuleId(null);
      }
    },
    [refreshRules, setRules, t],
  );

  const extractRules = useCallback(async () => {
    setExtracting(true);
    setExtractNotice(null);
    try {
      const result = await featuresApi.extractRules(id);
      if (result.rules.length === 0) {
        setExtractNotice({
          kind: "info",
          titleKey: "features.rulesExtractEmpty",
        });
      } else {
        message.success(
          t("features.rulesExtracted", { count: result.rules.length }),
        );
      }
      await refreshRules();
    } catch (err) {
      const code = parseApiError(err)?.code;
      if (code === "FEATURE_RULE_NO_SAMPLES") {
        setExtractNotice({
          kind: "info",
          titleKey: "features.rulesExtractNoSamples",
          detail: apiErrorMessage(err, t("features.rulesExtractNoSamples"), t),
        });
      } else if (code === "FEATURE_RULE_EXTRACTION_FAILED") {
        setExtractNotice({
          kind: "warning",
          titleKey: "features.rulesExtractFailed",
          detail: apiErrorMessage(err, t("features.rulesExtractFailed"), t),
        });
      } else {
        message.error(
          apiErrorMessage(err, t("features.rulesExtractFailed"), t),
        );
      }
    } finally {
      setExtracting(false);
    }
  }, [id, refreshRules, t]);

  return {
    rules,
    rulesLoading,
    pendingRules: rules.filter((rule) => rule.status === "draft").length,
    refreshRules,
    extracting,
    extractRules,
    extractNotice,
    reviewingRuleId,
    reviewRule,
    cases,
    casesLoading,
    refreshCases,
    finalized,
    finalizing,
    finalizeConflict,
    finalize,
    resetRun,
    promotedTaskId,
    promoting,
    promote,
  };
}
