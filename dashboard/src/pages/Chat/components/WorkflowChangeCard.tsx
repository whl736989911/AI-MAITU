import { useState } from "react";
import { Alert, Button, Card, Popconfirm, Tag, Typography } from "antd";
import { useTranslation } from "react-i18next";

import {
  featureWorkflowApi,
  type WorkflowChangeItem,
} from "../../../api/modules/featureWorkflow";
import { apiErrorMessage } from "../../../utils/apiError";

export interface WorkflowChangeResult {
  change_id: string;
  target: "definition" | "overlay";
  summary: string;
  items: WorkflowChangeItem[];
  status: "applied" | "reverted";
}

/** Only successful, complete tool results become cards. A tool refusal is not a change. */
export function collectWorkflowChanges(
  messages: readonly {
    toolData?: { name?: string; output?: string };
  }[],
): WorkflowChangeResult[] {
  const changes = new Map<string, WorkflowChangeResult>();
  for (const message of messages) {
    if (
      message.toolData?.name !== "feature_workflow_change" ||
      !message.toolData.output
    )
      continue;
    try {
      const value: unknown = JSON.parse(message.toolData.output);
      if (!value || typeof value !== "object" || Array.isArray(value)) continue;
      const row = value as Record<string, unknown>;
      if (
        typeof row.change_id !== "string" ||
        !row.change_id ||
        typeof row.summary !== "string" ||
        !Array.isArray(row.items) ||
        !["definition", "overlay"].includes(String(row.target)) ||
        !["applied", "reverted"].includes(String(row.status))
      )
        continue;
      changes.set(row.change_id, row as unknown as WorkflowChangeResult);
    } catch {
      // An unfinished stream or a tool failure is not an applied improvement.
    }
  }
  return [...changes.values()];
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined) return "—";
  return typeof value === "string" ? value : JSON.stringify(value);
}

export default function WorkflowChangeCard({
  agentId,
  change,
  canUndo,
}: {
  agentId: string;
  change: WorkflowChangeResult;
  canUndo: boolean;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState(change.status);
  const [reverting, setReverting] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const undo = async () => {
    setReverting(true);
    setError(null);
    try {
      const result = await featureWorkflowApi.revertChange(
        agentId,
        change.change_id,
      );
      setStatus(result.status as "applied" | "reverted");
    } catch (reason) {
      setError(apiErrorMessage(reason, t("workflowChange.revertFailed"), t));
    } finally {
      setReverting(false);
    }
  };

  return (
    <Card
      size="small"
      title={t("workflowChange.title")}
      style={{ maxWidth: 620 }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          marginBottom: 8,
        }}
      >
        <Tag>
          {t(
            `workflowChange.target${
              change.target === "definition" ? "Definition" : "Overlay"
            }`,
          )}
        </Tag>
        <Tag color={status === "applied" ? "green" : "default"}>
          {t(`workflowChange.${status}`)}
        </Tag>
        {canUndo && status === "applied" && (
          <Popconfirm
            title={t("workflowChange.revertConfirm")}
            onConfirm={undo}
          >
            <Button size="small" loading={reverting} disabled={reverting}>
              {t("workflowChange.revert")}
            </Button>
          </Popconfirm>
        )}
      </div>
      <Typography.Paragraph strong>{change.summary}</Typography.Paragraph>
      {change.items.map((item, index) => (
        <div key={`${item.path}:${index}`} style={{ marginTop: 8 }}>
          <Typography.Text type="secondary">
            {t("workflowChange.path")}: {item.path}
          </Typography.Text>
          <div>
            {t("workflowChange.before")}: {displayValue(item.before)}
          </div>
          <div>
            {t("workflowChange.after")}: {displayValue(item.after)}
          </div>
        </div>
      ))}
      {error && (
        <Alert type="error" showIcon message={error} style={{ marginTop: 8 }} />
      )}
    </Card>
  );
}
