/**
 * The audit of one run: what every step took in, what it produced, and every
 * change a person made to an artifact on the way.
 *
 * The before/after pair is the point of the view. A run that was corrected by
 * hand has to say so afterwards — "this step's input was edited from X to Y at
 * this time, by this account" — otherwise a mistake that survives into a
 * delivery cannot be traced back to the correction that caused it.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Collapse, Modal, Spin, Tag } from "antd";
import { RefreshCw } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureRunArtifact,
  type FeatureRunAudit,
  type FeatureStepStatus,
} from "../../../api/modules/features";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatMessageTime } from "../../../utils/formatMessageTime";
import { epochMillis } from "./featureArtifacts";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import styles from "../index.module.less";

const STEP_STATUS_LABEL_KEYS: Record<FeatureStepStatus, string> = {
  pending: "features.runStepStatusPending",
  running: "features.runStepStatusRunning",
  succeeded: "features.runStepStatusSucceeded",
  failed: "features.runStepStatusFailed",
  escalated: "features.runStepStatusEscalated",
  voided: "features.runStepStatusVoided",
};

const EDIT_SOURCE_LABEL_KEYS: Record<string, string> = {
  approve: "features.runAuditSourceApprove",
  rewind: "features.runAuditSourceRewind",
};

/** A rewind leaves two records behind: the correction, and what it replaced. */
const EDIT_KIND_LABEL_KEYS: Record<string, string> = {
  edit: "features.runAuditKindEdit",
  void: "features.runAuditKindVoid",
};

/** A raw JSON value as the audit reports it; ``null`` is a value, not a blank. */
function valueText(value: unknown): string {
  if (typeof value === "string") return value;
  if (value === undefined) return "—";
  return JSON.stringify(value, null, 2);
}

function ArtifactValueList({ artifacts }: { artifacts: FeatureRunArtifact[] }) {
  const { t } = useTranslation();
  if (artifacts.length === 0) {
    return (
      <div className={styles.runStepEmpty}>{t("features.runAuditNoArtifacts")}</div>
    );
  }
  return (
    <>
      {artifacts.map((artifact) => (
        <div className={styles.runArtifactEditor} key={artifact.name}>
          <div className={styles.runArtifactEditorHead}>
            <code>{artifact.name}</code>
            <span className={styles.runArtifactSchema}>{artifact.schema}</span>
          </div>
          <pre className={styles.runValue}>{valueText(artifact.value)}</pre>
        </div>
      ))}
    </>
  );
}

export interface FeatureRunAuditProps {
  featureId: string;
  taskId: string;
  open: boolean;
  onClose: () => void;
}

export default function FeatureRunAudit({
  featureId,
  taskId,
  open,
  onClose,
}: FeatureRunAuditProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [audit, setAudit] = useState<FeatureRunAudit | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      setAudit(await featuresApi.getFeatureRunAudit(featureId, taskId));
    } catch (err) {
      setAudit(null);
      setError(apiErrorMessage(err, t("features.runAuditLoadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [featureId, taskId, t]);

  useEffect(() => {
    if (open) void load();
  }, [open, load]);

  return (
    <Modal
      open={open}
      title={t("features.runAuditTitle")}
      footer={null}
      width="min(880px, 96vw)"
      onCancel={onClose}
    >
      {loading && (
        <div className={styles.capabilityStatus}>
          <Spin size="small" />
          <span>{t("common.loading")}</span>
        </div>
      )}

      {!loading && error !== null && (
        <Alert
          type="error"
          showIcon
          message={t("features.runAuditLoadFailed")}
          description={error}
          action={
            <Button
              size="small"
              icon={<RefreshCw size={13} />}
              onClick={() => void load()}
            >
              {t("features.runRetry")}
            </Button>
          }
        />
      )}

      {!loading && audit !== null && (
        <div className={styles.audit}>
          <div className={styles.auditMeta}>
            <code>{audit.task_id}</code>
          </div>

          {audit.steps.map((step) => (
            <article className={styles.auditStep} key={step.id}>
              <div className={styles.runStepHead}>
                <span className={styles.runStepSeq}>{step.seq + 1}</span>
                <span className={styles.runStepName}>{step.name}</span>
                <Tag>{t(STEP_STATUS_LABEL_KEYS[step.status])}</Tag>
                {step.attempts > 1 && (
                  <span className={styles.runStepMeta}>
                    {t("features.runStepAttempts", { count: step.attempts })}
                  </span>
                )}
                {epochMillis(step.started_at) !== null && (
                  <span className={styles.runStepMeta}>
                    {formatMessageTime(
                      epochMillis(step.started_at) as number,
                      timeZone,
                    )}
                  </span>
                )}
              </div>

              {step.error && <Alert type="error" showIcon message={step.error} />}

              <div className={styles.auditSection}>
                <div className={styles.auditSectionTitle}>
                  {t("features.runAuditInputs")}
                </div>
                <ArtifactValueList artifacts={step.inputs} />
              </div>

              <div className={styles.auditSection}>
                <div className={styles.auditSectionTitle}>
                  {t("features.runAuditArtifacts")}
                </div>
                <ArtifactValueList artifacts={step.artifacts} />
              </div>

              <div className={styles.auditSection}>
                <div className={styles.auditSectionTitle}>
                  {t("features.runAuditHumanEdits")}
                </div>
                {step.human_edits.length === 0 ? (
                  <div className={styles.runStepEmpty}>
                    {t("features.runAuditNoHumanEdits")}
                  </div>
                ) : (
                  step.human_edits.map((edit, index) => (
                    <div
                      className={styles.auditEdit}
                      key={`${edit.artifact}-${edit.at}-${index}`}
                    >
                      <div className={styles.auditEditHead}>
                        <code>{edit.artifact}</code>
                        <Tag color={edit.kind === "void" ? undefined : "blue"}>
                          {t(EDIT_KIND_LABEL_KEYS[edit.kind] ?? edit.kind)}
                        </Tag>
                        <Tag>{t(EDIT_SOURCE_LABEL_KEYS[edit.source] ?? edit.source)}</Tag>
                        <span className={styles.runStepMeta}>
                          {t("features.runAuditBy")} {edit.by_user_id ?? "—"}
                        </span>
                        <span className={styles.runStepMeta}>
                          {formatMessageTime(epochMillis(edit.at) ?? 0, timeZone)}
                        </span>
                      </div>
                      <div className={styles.auditDiff}>
                        <div className={styles.auditDiffSide}>
                          <span className={styles.auditDiffLabel}>
                            {edit.kind === "void"
                              ? t("features.runAuditBeforeVoided")
                              : t("features.runAuditBefore")}
                          </span>
                          <pre className={styles.runValue}>
                            {valueText(edit.before)}
                          </pre>
                        </div>
                        <div className={styles.auditDiffSide}>
                          <span className={styles.auditDiffLabel}>
                            {t("features.runAuditAfter")}
                          </span>
                          {edit.kind === "void" ? (
                            <div className={styles.runStepEmpty}>
                              {t("features.runAuditVoided")}
                            </div>
                          ) : (
                            <pre className={styles.runValue}>
                              {valueText(edit.after)}
                            </pre>
                          )}
                        </div>
                      </div>
                    </div>
                  ))
                )}
              </div>
            </article>
          ))}

          <Collapse
            ghost
            className={styles.capabilityCollapse}
            items={[
              {
                key: "snapshot",
                label: t("features.runAuditSnapshot"),
                children: (
                  <pre className={styles.runValue}>
                    {JSON.stringify(audit.snapshot, null, 2)}
                  </pre>
                ),
              },
            ]}
          />
        </div>
      )}
    </Modal>
  );
}
