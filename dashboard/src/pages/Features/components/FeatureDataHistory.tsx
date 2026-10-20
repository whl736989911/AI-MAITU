/**
 * A record's audit trail — what happened to it, who did it, and what changed.
 *
 * Each entry is one server-recorded change (create / update / publish / new file
 * version). Field-level items are listed as ``before → after`` pairs so a reader
 * can tell a correction from a rewrite without opening two versions.
 */

import { useEffect, useState } from "react";
import { Alert, Empty, Spin, Tag } from "antd";
import { useTranslation } from "react-i18next";

import type { FeatureDataChange } from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataHistoryProps {
  agentId: string;
  recordId: string;
  /** Bumped by the parent after a save, so the trail reloads with the record. */
  refreshKey?: number;
}

function valueText(value: unknown): string {
  if (value === undefined || value === null || value === "") return "—";
  if (typeof value === "boolean") return value ? "true" : "false";
  if (typeof value === "object") {
    try {
      return JSON.stringify(value);
    } catch {
      return String(value);
    }
  }
  return String(value);
}

export default function FeatureDataHistory({
  agentId,
  recordId,
  refreshKey = 0,
}: FeatureDataHistoryProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [loading, setLoading] = useState(true);
  const [changes, setChanges] = useState<FeatureDataChange[]>([]);
  const [failure, setFailure] = useState<unknown>(null);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      setFailure(null);
      try {
        const rows = await featureDataApi.history(agentId, recordId);
        if (!cancelled) setChanges(rows);
      } catch (error) {
        if (!cancelled) setFailure(error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [agentId, recordId, refreshKey]);

  if (loading) {
    return (
      <div className={styles.loading}>
        <Spin size="small" />
      </div>
    );
  }
  if (failure !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("features.data.historyLoadFailed")}
        description={apiErrorMessage(
          failure,
          t("features.data.historyLoadFailed"),
          t,
        )}
      />
    );
  }
  if (changes.length === 0) {
    return (
      <Empty
        className={styles.fileEmpty}
        image={Empty.PRESENTED_IMAGE_SIMPLE}
        description={t("features.data.historyEmpty")}
      />
    );
  }

  return (
    <div className={styles.historyList}>
      {changes.map((change) => (
        <div className={styles.historyRow} key={change.id}>
          <div className={styles.historyHead}>
            <Tag bordered={false}>
              {change.kind ?? t("features.data.history")}
            </Tag>
            <span className={styles.fileMeta}>
              {formatServerDateTime(change.created_at, timeZone)}
            </span>
            {change.summary?.trim() ? (
              <span className={styles.historySummary}>{change.summary}</span>
            ) : null}
          </div>
          {(change.items ?? []).length > 0 ? (
            <ul className={styles.historyItems}>
              {(change.items ?? []).map((item, index) => (
                <li key={`${change.id}-${index}`}>
                  {item.field ? `${item.field}: ` : ""}
                  {valueText(item.before)} → {valueText(item.after)}
                </li>
              ))}
            </ul>
          ) : null}
        </div>
      ))}
    </div>
  );
}
