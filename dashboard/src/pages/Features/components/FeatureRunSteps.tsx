/**
 * The step-level view of one run: where it is, what it is waiting for, and what
 * can be done about it.
 *
 * Three facts shape this surface:
 *   - A run that stopped at a gate is *the same run*, still open. Approving
 *     continues it; it never starts a second one.
 *   - Nothing here is inferred from the definition. Every step's status, gate,
 *     artifacts and error come from the run itself, so a definition edited after
 *     the run started cannot make this view lie about what happened.
 *   - A refusal is shown as it came. An edit the server rejects (a value that
 *     does not fit the artifact's schema, a gate that does not allow editing) is
 *     reported verbatim instead of being retried or quietly dropped.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { Alert, Button, Input, Modal, Tag, Tooltip } from "antd";
import { FileSearch, RefreshCw, ShieldCheck, Undo2, UserCheck, Wrench } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  featuresApi,
  type FeatureRunArtifact,
  type FeatureRunStatus,
  type FeatureRunStep,
  type FeatureStepRun,
  type FeatureStepStatus,
} from "../../../api/modules/features";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatMessageTime } from "../../../utils/formatMessageTime";
import { message } from "@/utils/antdMessage";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import FeatureRunAudit from "./FeatureRunAudit";
import {
  artifactsBefore,
  parseArtifactEdits,
  seedDrafts,
  type ArtifactDrafts,
} from "./featureArtifacts";
import styles from "../index.module.less";

const RUN_STATUS_LABEL_KEYS: Record<FeatureRunStatus, string> = {
  running: "features.runStatusRunning",
  awaiting_gate: "features.runStatusAwaitingGate",
  succeeded: "features.runStatusSucceeded",
  failed: "features.runStatusFailed",
  escalated: "features.runStatusEscalated",
};

const RUN_STATUS_COLORS: Record<FeatureRunStatus, string> = {
  running: "processing",
  awaiting_gate: "warning",
  succeeded: "success",
  failed: "error",
  escalated: "volcano",
};

const STEP_STATUS_LABEL_KEYS: Record<FeatureStepStatus, string> = {
  pending: "features.runStepStatusPending",
  running: "features.runStepStatusRunning",
  succeeded: "features.runStepStatusSucceeded",
  failed: "features.runStepStatusFailed",
  escalated: "features.runStepStatusEscalated",
  voided: "features.runStepStatusVoided",
};

const STEP_STATUS_COLORS: Record<FeatureStepStatus, string> = {
  pending: "default",
  running: "processing",
  succeeded: "success",
  failed: "error",
  escalated: "volcano",
  voided: "default",
};

const GATE_LABEL_KEYS: Record<string, string> = {
  auto: "features.settingsStepGateAuto",
  confirm: "features.settingsStepGateConfirm",
  validate: "features.settingsStepGateValidate",
};

/** Artifact names and their declared schema, which is all a step row needs. */
function ArtifactTags({ artifacts }: { artifacts: FeatureRunArtifact[] }) {
  const { t } = useTranslation();
  if (artifacts.length === 0) {
    return (
      <span className={styles.runStepEmpty}>{t("features.runStepNoArtifacts")}</span>
    );
  }
  return (
    <div className={styles.runArtifactTags}>
      {artifacts.map((artifact) => (
        <Tag key={artifact.name} className={styles.runArtifactTag}>
          {artifact.name}
          <span className={styles.runArtifactSchema}>{artifact.schema}</span>
        </Tag>
      ))}
    </div>
  );
}

/**
 * The editors for the artifacts a person may change.
 *
 * A draft is kept per artifact and the *server's* value wins only when it moved
 * on its own (another tab, another person): typing here survives a refresh of
 * the same gate, because losing a half-written correction to a background
 * reload is exactly how a correction gets delivered without its author.
 */
function ArtifactEditors({
  artifacts,
  drafts,
  onChange,
}: {
  artifacts: FeatureRunArtifact[];
  drafts: ArtifactDrafts;
  onChange: (name: string, text: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <>
      {artifacts.map((artifact) => (
        <div className={styles.runArtifactEditor} key={artifact.name}>
          <div className={styles.runArtifactEditorHead}>
            <code>{artifact.name}</code>
            <span className={styles.runArtifactSchema}>{artifact.schema}</span>
            <span className={styles.runStepEmpty}>
              {t("features.runArtifactJsonHint")}
            </span>
          </div>
          <Input.TextArea
            className={styles.promptEditor}
            value={drafts[artifact.name] ?? ""}
            autoSize={{ minRows: 4, maxRows: 18 }}
            onChange={(event) => onChange(artifact.name, event.target.value)}
          />
        </div>
      ))}
    </>
  );
}

export interface FeatureRunStepsProps {
  featureId: string;
  run: FeatureStepRun;
  /** The run as the server answered last — approving and rewinding go through it. */
  onRunChange: (next: FeatureStepRun) => void;
}

export default function FeatureRunSteps({
  featureId,
  run,
  onRunChange,
}: FeatureRunStepsProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [busy, setBusy] = useState(false);
  const [auditOpen, setAuditOpen] = useState(false);
  const [gateDrafts, setGateDrafts] = useState<ArtifactDrafts>({});
  const [rewind, setRewind] = useState<{
    step: FeatureRunStep;
    artifacts: FeatureRunArtifact[];
  } | null>(null);
  const [rewindDrafts, setRewindDrafts] = useState<ArtifactDrafts>({});
  /** What the server held when this gate was last rendered, per artifact. */
  const serverTexts = useRef<ArtifactDrafts>({});

  const gate = run.pending_gate;

  useEffect(() => {
    if (!gate) {
      serverTexts.current = {};
      setGateDrafts({});
      return;
    }
    const server = seedDrafts(gate.artifacts);
    setGateDrafts((current) => {
      const next: ArtifactDrafts = {};
      for (const [name, text] of Object.entries(server)) {
        const followedTheServer = serverTexts.current[name] !== text;
        next[name] = followedTheServer ? text : current[name] ?? text;
      }
      return next;
    });
    serverTexts.current = server;
  }, [gate]);

  const refresh = useCallback(async () => {
    setBusy(true);
    try {
      onRunChange(await featuresApi.getFeatureRun(featureId, run.task_id));
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.runActionFailed"), t));
    } finally {
      setBusy(false);
    }
  }, [featureId, run.task_id, onRunChange, t]);

  const approve = useCallback(async () => {
    if (!gate) return;
    const parsed = parseArtifactEdits(gate.artifacts, gateDrafts);
    if ("failure" in parsed) {
      message.error(
        t("features.runInvalidJson", { artifact: parsed.failure.artifact }),
      );
      return;
    }
    setBusy(true);
    try {
      onRunChange(
        await featuresApi.approveFeatureRun(
          featureId,
          run.task_id,
          Object.keys(parsed.edits).length > 0 ? parsed.edits : undefined,
        ),
      );
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.runActionFailed"), t));
    } finally {
      setBusy(false);
    }
  }, [featureId, gate, gateDrafts, onRunChange, run.task_id, t]);

  const confirmRewind = useCallback(async () => {
    if (!rewind) return;
    const parsed = parseArtifactEdits(rewind.artifacts, rewindDrafts);
    if ("failure" in parsed) {
      message.error(
        t("features.runInvalidJson", { artifact: parsed.failure.artifact }),
      );
      return;
    }
    setBusy(true);
    try {
      onRunChange(
        await featuresApi.rewindFeatureRun(
          featureId,
          run.task_id,
          rewind.step.id,
          Object.keys(parsed.edits).length > 0 ? parsed.edits : undefined,
        ),
      );
      setRewind(null);
    } catch (err) {
      message.error(apiErrorMessage(err, t("features.runActionFailed"), t));
    } finally {
      setBusy(false);
    }
  }, [featureId, onRunChange, rewind, rewindDrafts, run.task_id, t]);

  return (
    <section className={styles.runPanel}>
      <div className={styles.runHead}>
        <Tag color={RUN_STATUS_COLORS[run.status]}>
          {t(RUN_STATUS_LABEL_KEYS[run.status])}
        </Tag>
        <code className={styles.runTaskId}>{run.task_id}</code>
        <div className={styles.runHeadTools}>
          <Button
            size="small"
            icon={<FileSearch size={13} />}
            onClick={() => setAuditOpen(true)}
          >
            {t("features.runAuditOpen")}
          </Button>
          <Button
            size="small"
            icon={<RefreshCw size={13} />}
            loading={busy}
            onClick={() => void refresh()}
          >
            {t("common.refresh")}
          </Button>
        </div>
      </div>

      {run.status === "escalated" && (
        <Alert
          type="warning"
          showIcon
          message={t("features.runEscalatedTitle")}
          description={t("features.runEscalatedHint")}
        />
      )}

      {run.status === "failed" && (
        <Alert
          type="error"
          showIcon
          message={t("features.runFailedTitle")}
          description={t("features.runFailedHint")}
        />
      )}

      {gate && (
        <div className={styles.runGate}>
          <div className={styles.runGateHead}>
            <Tag color="warning">
              {gate.gate === "validate" ? (
                <ShieldCheck size={12} />
              ) : (
                <UserCheck size={12} />
              )}{" "}
              {t(GATE_LABEL_KEYS[gate.gate])}
            </Tag>
            <span className={styles.runGateStep}>
              {t("features.runGateStep", { step: gate.name })}
            </span>
          </div>
          <div className={styles.blockHint}>{t("features.runGateDeliverables")}</div>

          {gate.allow_edit ? (
            <>
              <div className={styles.blockHint}>
                {t("features.runGateEditHint")}
              </div>
              <ArtifactEditors
                artifacts={gate.artifacts}
                drafts={gateDrafts}
                onChange={(name, text) =>
                  setGateDrafts((current) => ({ ...current, [name]: text }))
                }
              />
            </>
          ) : (
            <>
              <Alert
                type="info"
                showIcon
                message={t("features.runGateReadOnly")}
              />
              <ArtifactValues artifacts={gate.artifacts} />
            </>
          )}

          <div className={styles.runGateFooter}>
            <Button
              type="primary"
              loading={busy}
              icon={<UserCheck size={14} />}
              onClick={() => void approve()}
            >
              {t("features.runGateApprove")}
            </Button>
          </div>
        </div>
      )}

      <ol className={styles.runStepList}>
        {run.steps.map((step) => (
          <li
            key={step.id}
            className={step.voided ? styles.runStepVoided : styles.runStep}
          >
            <div className={styles.runStepHead}>
              <span className={styles.runStepSeq}>{step.seq + 1}</span>
              <span className={styles.runStepName}>{step.name}</span>
              <code className={styles.runStepId}>{step.id}</code>
              <Tag>{t(GATE_LABEL_KEYS[step.gate])}</Tag>
              <Tag color={STEP_STATUS_COLORS[step.status]}>
                {t(STEP_STATUS_LABEL_KEYS[step.status])}
              </Tag>
              {step.attempts > 1 && (
                <span className={styles.runStepMeta}>
                  {t("features.runStepAttempts", { count: step.attempts })}
                </span>
              )}
              {step.started_at !== null && (
                <span className={styles.runStepMeta}>
                  {formatMessageTime(step.started_at, timeZone)}
                </span>
              )}
              <div className={styles.runStepTools}>
                {step.status !== "pending" && !step.voided && (
                  <>
                    <Tooltip title={t("features.runStepRewindHint")}>
                      <Button
                        type="text"
                        size="small"
                        icon={<Undo2 size={13} />}
                        onClick={() => {
                          setRewindDrafts({});
                          setRewind({ step, artifacts: [] });
                        }}
                      >
                        {t("features.runStepRewind")}
                      </Button>
                    </Tooltip>
                    <Tooltip title={t("features.runStepRewindFixHint")}>
                      <Button
                        type="text"
                        size="small"
                        icon={<Wrench size={13} />}
                        onClick={() => {
                          const artifacts = artifactsBefore(run.steps, step.seq);
                          setRewindDrafts(seedDrafts(artifacts));
                          setRewind({ step, artifacts });
                        }}
                      >
                        {t("features.runStepRewindFix")}
                      </Button>
                    </Tooltip>
                  </>
                )}
              </div>
            </div>

            {step.error && (
              <Alert type="error" showIcon message={step.error} />
            )}

            <ArtifactTags artifacts={step.artifacts} />
          </li>
        ))}
      </ol>

      <Modal
        open={rewind !== null}
        title={t("features.runRewindTitle", {
          step: rewind?.step.name ?? "",
        })}
        okText={t("features.runRewindConfirm")}
        cancelText={t("common.cancel")}
        confirmLoading={busy}
        width="min(720px, 94vw)"
        onOk={() => void confirmRewind()}
        onCancel={() => setRewind(null)}
      >
        <div className={styles.blockHint}>{t("features.runRewindHint")}</div>
        {rewind && rewind.artifacts.length === 0 ? (
          <Alert
            type="info"
            showIcon
            message={t("features.runRewindNoEdits")}
          />
        ) : (
          rewind && (
            <ArtifactEditors
              artifacts={rewind.artifacts}
              drafts={rewindDrafts}
              onChange={(name, text) =>
                setRewindDrafts((current) => ({ ...current, [name]: text }))
              }
            />
          )
        )}
      </Modal>

      <FeatureRunAudit
        featureId={featureId}
        taskId={run.task_id}
        open={auditOpen}
        onClose={() => setAuditOpen(false)}
      />
    </section>
  );
}

/** Read-only artifact values, for a gate that does not allow editing them. */
function ArtifactValues({ artifacts }: { artifacts: FeatureRunArtifact[] }) {
  const { t } = useTranslation();
  if (artifacts.length === 0) {
    return (
      <div className={styles.runStepEmpty}>{t("features.runStepNoArtifacts")}</div>
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
          <pre className={styles.runValue}>
            {typeof artifact.value === "string"
              ? artifact.value
              : JSON.stringify(artifact.value, null, 2)}
          </pre>
        </div>
      ))}
    </>
  );
}
