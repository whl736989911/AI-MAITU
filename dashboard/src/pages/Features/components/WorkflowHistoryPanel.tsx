import { useEffect, useState } from "react";
import {
  Alert,
  Button,
  Card,
  List,
  Popconfirm,
  Space,
  Spin,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";

import {
  featureWorkflowApi,
  type WorkflowChange,
  type WorkflowRunItem,
} from "../../../api/modules/featureWorkflow";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { apiErrorMessage } from "../../../utils/apiError";

/** The caller's private layer and evidence; never a cross-user activity feed. */
export default function WorkflowHistoryPanel({
  agentId,
  canWrite,
}: {
  agentId: string;
  canWrite: boolean;
}) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [runs, setRuns] = useState<WorkflowRunItem[]>([]);
  const [changes, setChanges] = useState<WorkflowChange[]>([]);
  const [loading, setLoading] = useState(true);
  const [undoing, setUndoing] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    setError(null);
    void Promise.all([
      featureWorkflowApi.runs(agentId),
      featureWorkflowApi.changes(agentId),
    ])
      .then(([runResult, changeResult]) => {
        if (cancelled) return;
        setRuns(runResult.runs);
        setChanges(changeResult.changes);
      })
      .catch((reason: unknown) => {
        if (!cancelled)
          setError(
            apiErrorMessage(reason, t("features.workflow.loadFailed"), t),
          );
      })
      .finally(() => {
        if (!cancelled) setLoading(false);
      });
    return () => {
      cancelled = true;
    };
    // The caller's data changes when the feature changes, not when i18n re-renders.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [agentId]);

  const undo = async (id: string) => {
    setUndoing(id);
    setError(null);
    try {
      const result = await featureWorkflowApi.revertChange(agentId, id);
      setChanges((current) =>
        current.map((item) => (item.id === id ? result : item)),
      );
      // Overlay edits have their own editor in the feature's personalization entry.
    } catch (reason) {
      setError(apiErrorMessage(reason, t("workflowChange.revertFailed"), t));
    } finally {
      setUndoing(null);
    }
  };

  if (loading) return <Spin tip={t("features.workflow.historyLoading")} />;

  return (
    <Space direction="vertical" size="middle" style={{ width: "100%" }}>
      {error && <Alert type="error" showIcon message={error} />}
      <Card size="small" title={t("features.workflow.overviewTitle")}>
        <Typography.Paragraph type="secondary">
          {t("features.workflow.runsHint")}
        </Typography.Paragraph>
        <List
          dataSource={runs}
          locale={{ emptyText: t("features.workflow.runsEmpty") }}
          renderItem={(run) => (
            <List.Item key={run.id}>
              <Space direction="vertical" size={2}>
                <Typography.Text strong>
                  {formatServerDateTime(run.created_at, timeZone)}
                </Typography.Text>
                <Typography.Text>
                  {t("features.workflow.runValues")}:{" "}
                  {JSON.stringify(run.inputs)}
                </Typography.Text>
                {run.thread_id && (
                  <Typography.Text type="secondary">
                    {t("features.workflow.runThread")}: {run.thread_id}
                  </Typography.Text>
                )}
              </Space>
            </List.Item>
          )}
        />
      </Card>
      <Card size="small" title={t("features.workflow.changesTitle")}>
        <List
          dataSource={changes}
          locale={{ emptyText: t("features.workflow.changesEmpty") }}
          renderItem={(change) => (
            <List.Item
              key={change.id}
              actions={
                change.status === "applied" &&
                (change.target === "overlay" || canWrite)
                  ? [
                      <Popconfirm
                        key="undo"
                        title={t("workflowChange.revertConfirm")}
                        onConfirm={() => void undo(change.id)}
                      >
                        <Button
                          size="small"
                          loading={undoing === change.id}
                          disabled={undoing !== null}
                        >
                          {t("workflowChange.revert")}
                        </Button>
                      </Popconfirm>,
                    ]
                  : undefined
              }
            >
              <Space direction="vertical" size={2}>
                <Space>
                  <Typography.Text strong>{change.summary}</Typography.Text>
                  <Tag>
                    {t(
                      change.target === "overlay"
                        ? "workflowChange.targetOverlay"
                        : "workflowChange.targetDefinition",
                    )}
                  </Tag>
                  <Tag
                    color={change.status === "applied" ? "green" : "default"}
                  >
                    {t(
                      change.status === "applied"
                        ? "workflowChange.applied"
                        : "workflowChange.reverted",
                    )}
                  </Tag>
                </Space>
                <Typography.Text type="secondary">
                  {formatServerDateTime(change.created_at, timeZone)}
                </Typography.Text>
              </Space>
            </List.Item>
          )}
        />
      </Card>
    </Space>
  );
}
