import { useEffect, useState } from "react";
import { Progress } from "antd";
import { useTranslation } from "react-i18next";
import type { MemoryMaintenanceStatus } from "../hooks/useMemoryMaintenance";
import styles from "./MemoryMaintenanceBanner.module.less";

function formatBytes(n?: number | null): string {
  if (!n || n <= 0) return "";
  const gb = 1024 ** 3;
  const mb = 1024 ** 2;
  if (n >= gb) return `${(n / gb).toFixed(1)} GB`;
  if (n >= mb) return `${Math.round(n / mb)} MB`;
  return `${Math.max(1, Math.round(n / 1024))} KB`;
}

interface MemoryMaintenanceBannerProps {
  status: MemoryMaintenanceStatus;
  blocking: boolean;
}

export default function MemoryMaintenanceBanner({
  status,
  blocking,
}: MemoryMaintenanceBannerProps) {
  const { t } = useTranslation();
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const timer = setInterval(() => setNow(Date.now()), 1000);
    return () => clearInterval(timer);
  }, []);

  const phase = status.phase;
  const terminal =
    phase === "done" || phase === "failed" || phase === "skipped";
  const elapsed = status.started_at
    ? Math.max(0, Math.floor(now / 1000 - status.started_at))
    : 0;
  const size = formatBytes(status.file_bytes);
  const total = status.total ?? 0;
  const percent =
    status.percent ??
    (phase === "done"
      ? 100
      : total > 0 && status.scanned != null
      ? Math.min(100, Math.round((status.scanned / total) * 100))
      : 10);
  const title = t(`chat.memoryMaintenance.${phase}`, {
    defaultValue: t("chat.memoryMaintenance.compacting"),
  });
  const hint = terminal
    ? t(`chat.memoryMaintenance.hint${phase[0].toUpperCase()}${phase.slice(1)}`)
    : blocking
    ? t("chat.memoryMaintenance.hintBlocking")
    : t("chat.memoryMaintenance.hintQueued");
  const progressStatus =
    phase === "failed" ? "exception" : phase === "done" ? "success" : "active";
  const scanned =
    status.scanned != null && status.total != null
      ? `${status.scanned} / ${status.total}`
      : "";

  return (
    <div className={styles.banner} role="status">
      <div className={styles.titleRow}>
        <span className={styles.title}>{title}</span>
        {size ? <span className={styles.meta}>{size}</span> : null}
        {scanned ? <span className={styles.meta}>{scanned}</span> : null}
        {!terminal && elapsed > 0 ? (
          <span className={styles.meta}>
            {t("chat.memoryMaintenance.elapsed", { seconds: elapsed })}
          </span>
        ) : null}
      </div>
      <div className={styles.hint}>
        {status.error || hint}
        {phase === "skipped" && status.skipped_reason
          ? ` ${status.skipped_reason}`
          : ""}
      </div>
      {!terminal ? (
        <Progress
          percent={percent}
          status={progressStatus}
          showInfo={false}
          size="small"
        />
      ) : null}
    </div>
  );
}
