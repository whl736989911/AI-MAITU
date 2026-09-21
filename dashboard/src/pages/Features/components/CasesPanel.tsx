/**
 * Cases panel — the few-shot library a feature's future runs are handed.
 *
 * Cases are promoted by hand on purpose: an unreviewed sample is worse than a
 * missing one. Each entry therefore shows *why* it is here (who promoted it,
 * when, the optional note) next to the two things a future prompt reuses — the
 * inputs and the human-approved final text — plus how much a human had to
 * change it.
 */

import { Spin } from "antd";
import { useTranslation } from "react-i18next";
import type {
  FeatureCase,
  FeatureOutputKind,
} from "../../../api/modules/features";
import { EmptyState } from "../../../components/EmptyState";
import LazyMarkdown from "../../../components/Markdown/LazyMarkdown";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import styles from "../index.module.less";
import type { FeatureLearning } from "./useFeatureLearning";

/** Input values are arbitrary JSON: scalars read raw, arrays joined, objects as JSON. */
function inputValueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === null || value === undefined) return "";
  if (Array.isArray(value))
    return value.map((item) => inputValueText(item)).join(" · ");
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

function CaseCard({
  item,
  outputKind,
}: {
  item: FeatureCase;
  outputKind: FeatureOutputKind;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const edits = item.diff.filter((segment) => segment.op !== "keep").length;
  const inputs = Object.entries(item.inputs);

  return (
    <article className={styles.caseCard}>
      <div className={styles.caseHead}>
        <span className={styles.caseTitle}>
          {t("features.caseRun")} <code>{item.task_id}</code>
        </span>
        <span className={styles.caseMetaItem}>
          {t("features.casePromotedAt", {
            time: formatServerDateTime(item.promoted_at, timeZone),
          })}
        </span>
        <span className={styles.caseMetaItem}>
          {t("features.casePromotedBy", { id: item.promoted_by })}
        </span>
        <span className={styles.caseMetaItem}>
          {t("features.caseEdits", { count: edits })}
        </span>
      </div>

      {item.note && <div className={styles.caseNote}>{item.note}</div>}

      <div className={styles.caseBody}>
        <div className={styles.caseInputs}>
          <div className={styles.caseSectionLabel}>
            {t("features.caseInputs")}
          </div>
          {inputs.length === 0 ? (
            <div className={styles.caseEmptyInput}>
              {t("features.caseNoInputs")}
            </div>
          ) : (
            inputs.map(([name, value]) => (
              <div className={styles.caseInputRow} key={name}>
                <span className={styles.caseInputKey}>{name}</span>
                <span className={styles.caseInputValue}>
                  {inputValueText(value)}
                </span>
              </div>
            ))
          )}
        </div>
        <div className={styles.caseFinal}>
          <div className={styles.caseSectionLabel}>
            {t("features.caseFinal")}
          </div>
          {outputKind === "markdown" ? (
            <div className={styles.caseFinalBody}>
              <LazyMarkdown
                content={item.final}
                className={styles.resultMarkdown}
              />
            </div>
          ) : (
            <pre className={`${styles.resultPre} ${styles.caseFinalBody}`}>
              {item.final}
            </pre>
          )}
        </div>
      </div>
    </article>
  );
}

export default function CasesPanel({
  learning,
  outputKind,
}: {
  learning: FeatureLearning;
  outputKind: FeatureOutputKind;
}) {
  const { t } = useTranslation();
  const { cases, casesLoading } = learning;

  return (
    <div className={styles.panel}>
      <div className={styles.panelHead}>
        <div className={styles.panelHeading}>
          <div className={styles.panelTitle}>{t("features.casesTitle")}</div>
          <div className={styles.panelHint}>{t("features.casesSubtitle")}</div>
        </div>
      </div>

      {casesLoading && cases.length === 0 ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : cases.length === 0 ? (
        <EmptyState
          title={t("features.casesNone")}
          description={t("features.casesNoneHint")}
        />
      ) : (
        <div className={styles.caseList}>
          {cases.map((item) => (
            <CaseCard key={item.task_id} item={item} outputKind={outputKind} />
          ))}
        </div>
      )}
    </div>
  );
}
