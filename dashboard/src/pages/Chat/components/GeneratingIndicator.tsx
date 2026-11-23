import { useTranslation } from "react-i18next";
import { useElapsedSince } from "../../../hooks/useElapsedSeconds";
import type { ChatGeneratingPhase } from "./generatingGate";
import styles from "../index.module.less";

interface GeneratingIndicatorProps {
  onCancel?: () => void;
  /** Wall-clock start of the current turn, including waits between tool calls. */
  startedAt?: number | null;
  phase: ChatGeneratingPhase;
}

/** A single status derived from the turn's received events. */
export default function GeneratingIndicator({
  onCancel,
  startedAt = null,
  phase,
}: GeneratingIndicatorProps) {
  const { t } = useTranslation();
  const elapsed = useElapsedSince(startedAt ?? 0);
  const label = t(`chat.liveProgress.${phase}`);

  return (
    <div className={styles.turnInset}>
      <div className={styles.generatingIndicator} role="status">
        <span className={styles.thinkingDot} />
        <span className={styles.thinkingDot} />
        <span className={styles.thinkingDot} />
        <span className={styles.generatingText}>
          {label}
          {startedAt ? (
            <span aria-hidden="true">
              {" · "}
              {t("chat.liveProgress.elapsed", { seconds: elapsed })}
            </span>
          ) : null}
        </span>
        {onCancel && (
          <button
            className={styles.thinkingCancelBtn}
            onClick={onCancel}
            type="button"
            title={t("common.cancel")}
          >
            {t("common.cancel")}
          </button>
        )}
      </div>
    </div>
  );
}
