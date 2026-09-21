/**
 * What one step dispatched, and the ceiling that bounded it (design 7.7 / 7.8).
 *
 * On an ``orchestrate`` step the platform does not choose the plan — the model
 * decides how to split the step and how many subagents to run, and the platform
 * only caps how many run at once (``max_parallel``) and records what happened.
 * This is that record, and it is the only answer a finished run can give to "what
 * did this step actually do": without it a wrong result cannot be traced back to
 * the sub-task that produced it, which is the whole point of 7.8.
 *
 * So every fact the record carries is shown — which subagent each dispatch ran
 * as, the task it was given, what it answered, how long it ran, how many were
 * running when it started, and how many were held back by the ceiling. Nothing is
 * summarised away and nothing is filled in: a dispatch that recorded no answer
 * says so rather than showing a blank, and a result that was cut short says that
 * too. The run view and the audit render this same record from the same response
 * field, so the two can never describe different runs.
 *
 * A step that neither orchestrated nor ran as a named subagent carries no record
 * at all, and that is the only case where the section is hidden.
 */

import { Alert, Tag } from "antd";
import { useTranslation } from "react-i18next";
import type {
  FeatureStepDecomposition,
  FeatureStepDispatch,
} from "../../../api/modules/features";
import { formatMessageTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { epochMillis } from "./featureArtifacts";
import styles from "../index.module.less";

/** A dispatch ends like a step does, but it has only two endings. */
const STATUS_LABEL_KEYS: Record<string, string> = {
  succeeded: "features.runStepStatusSucceeded",
  failed: "features.runStepStatusFailed",
};

const STATUS_COLORS: Record<string, string> = {
  succeeded: "success",
  failed: "error",
};

/** One dispatch: what it was given, what it answered, and how it was scheduled. */
function Dispatch({ dispatch }: { dispatch: FeatureStepDispatch }) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const started = epochMillis(dispatch.started_at);
  const ended = epochMillis(dispatch.ended_at);
  const seconds =
    ended !== null && started !== null
      ? Math.round((ended - started) / 1000)
      : null;

  return (
    <div className={styles.dispatch}>
      <div className={styles.dispatchHead}>
        <span className={styles.runStepSeq}>{dispatch.ordinal}</span>
        <span className={styles.dispatchRole}>{dispatch.role}</span>
        <Tag color={STATUS_COLORS[dispatch.status]}>
          {t(STATUS_LABEL_KEYS[dispatch.status] ?? dispatch.status)}
        </Tag>
        {dispatch.truncated && (
          <Tag>{t("features.runStepDispatchTruncated")}</Tag>
        )}
      </div>

      <div className={styles.dispatchTask}>{dispatch.task}</div>

      <div className={styles.dispatchMeta}>
        {started !== null && (
          <span>{formatMessageTime(started, timeZone)}</span>
        )}
        {seconds !== null && (
          <span>
            {t("features.runStepDispatchDuration", { seconds })}
          </span>
        )}
        <span>
          {t("features.runStepDispatchSlots", { count: dispatch.slots })}
        </span>
        {dispatch.waited && (
          <span>
            {dispatch.waited_ms > 0
              ? t("features.runStepDispatchWaitedMs", { ms: dispatch.waited_ms })
              : t("features.runStepDispatchQueued")}
          </span>
        )}
      </div>

      {dispatch.error && <Alert type="error" showIcon message={dispatch.error} />}

      {dispatch.result ? (
        <pre className={styles.runValue}>{dispatch.result}</pre>
      ) : (
        !dispatch.error && (
          <div className={styles.runStepEmpty}>
            {t("features.runStepDispatchNoAnswer")}
          </div>
        )
      )}
    </div>
  );
}

export interface FeatureDecompositionProps {
  /** The record as the run reports it; ``null``/absent means the step dispatched nothing to record. */
  decomposition: FeatureStepDecomposition | null | undefined;
}

export default function FeatureDecomposition({
  decomposition,
}: FeatureDecompositionProps) {
  const { t } = useTranslation();
  if (!decomposition) return null;

  const dispatches = decomposition.dispatches;

  return (
    <div className={styles.dispatchList}>
      <div className={styles.dispatchTitle}>
        {t("features.runStepDecomposition", { count: dispatches.length })}
      </div>

      <div className={styles.dispatchMeta}>
        <span>
          {decomposition.declared === null
            ? t("features.runStepDispatchCeilingDefault", {
                ceiling: decomposition.ceiling,
              })
            : t("features.runStepDispatchCeilingDeclared", {
                ceiling: decomposition.ceiling,
              })}
        </span>
        <span>
          {t("features.runStepDispatchPeak", { peak: decomposition.peak })}
        </span>
        <span>
          {t("features.runStepDispatchWaited", { count: decomposition.waited })}
        </span>
        {decomposition.role !== null && (
          <span>
            {t("features.runStepDispatchRole", { role: decomposition.role })}
          </span>
        )}
      </div>

      {dispatches.length === 0 ? (
        <div className={styles.runStepEmpty}>
          {t("features.runStepDispatchNone")}
        </div>
      ) : (
        dispatches.map((dispatch) => (
          <Dispatch key={dispatch.ordinal} dispatch={dispatch} />
        ))
      )}
    </div>
  );
}
