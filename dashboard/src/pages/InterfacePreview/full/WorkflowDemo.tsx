import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Input,
  List,
  Modal,
  Space,
  Tag,
  Typography,
} from "antd";
import type { FeatureWorkflow } from "../../../api/modules/featureWorkflow";
import WorkflowInputsSection from "../../Features/components/WorkflowInputsSection";
import WorkflowOutputsSection from "../../Features/components/WorkflowOutputsSection";
import WorkflowRulesSection from "../../Features/components/WorkflowRulesSection";
import WorkflowStepsSection from "../../Features/components/WorkflowStepsSection";
import {
  documentJson,
  readWorkflowJson,
  validateWorkflowDocument,
} from "../../Features/utils/workflowDocument";
import { DemoBoundary } from "./DemoKit";
import styles from "./WorkspacePages.module.less";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { formatServerDateTime } from "../../../utils/formatMessageTime";

interface WorkflowDemoProps {
  featureId: string;
  workflow: FeatureWorkflow;
  t: (key: string) => string;
  onChange: (workflow: FeatureWorkflow) => void;
}

export default function WorkflowDemo({
  featureId,
  workflow,
  t,
  onChange,
}: WorkflowDemoProps) {
  const timeZone = useServerTimezone();
  const [view, setView] = useState<"form" | "json">("form");
  const [jsonText, setJsonText] = useState(() => documentJson(workflow));
  const [jsonError, setJsonError] = useState<string[]>([]);
  const [problems, setProblems] = useState<string[]>([]);
  const [runOpen, setRunOpen] = useState(false);
  const [runParams, setRunParams] = useState(
    '{\n  "contract_text": "示例合同条款",\n  "focus": "付款与解除条款"\n}',
  );
  const [runResult, setRunResult] = useState("");
  const [changes, setChanges] = useState<FeatureWorkflow[]>([]);
  const [saved, setSaved] = useState(true);
  const [runHistory, setRunHistory] = useState<string[]>([]);
  const [publishConfirmOpen, setPublishConfirmOpen] = useState(false);
  const draft = (patch: Partial<FeatureWorkflow>) => {
    setChanges((current) => [workflow, ...current]);
    onChange({ ...workflow, ...patch, status: "draft" });
    setSaved(false);
    setProblems([]);
  };
  const saveJson = () => {
    const parsed = readWorkflowJson(jsonText);
    if (!parsed.workflow) {
      setJsonError(parsed.problems);
      return;
    }
    setJsonError([]);
    onChange(parsed.workflow);
    setSaved(false);
  };
  const publish = () => {
    const candidate = { ...workflow, status: "active" as const };
    const errors = validateWorkflowDocument(candidate);
    setProblems(errors);
    if (!errors.length) onChange(candidate);
  };
  const confirmRun = () => {
    try {
      const params = JSON.parse(runParams) as Record<string, unknown>;
      const errors = validateWorkflowDocument(workflow);
      if (errors.length) {
        setProblems(errors);
        setRunOpen(false);
        return;
      }
      const required = workflow.inputs?.required ?? [];
      const missing = required.filter(
        (key) => params[key] === undefined || params[key] === "",
      );
      if (missing.length) {
        setProblems(missing.map((key) => `run input ${key} is required`));
        return;
      }
      const summary = t("interfacePreview.businessFlow.sampleRunResult");
      setRunResult(summary);
      setRunHistory((current) => [
        `${formatServerDateTime(Date.now() / 1000, timeZone)} · ${summary}`,
        ...current,
      ]);
      setRunOpen(false);
    } catch {
      setProblems(["run parameters must be valid JSON"]);
    }
  };
  const steps = workflow.steps ?? [];
  return (
    <div className={styles.workflowGrid} data-feature-workflow={featureId}>
      <div className={styles.wideCard}>
        <Space wrap>
          <Tag color={workflow.status === "active" ? "green" : "orange"}>
            {workflow.status || "draft"}
          </Tag>
          <Button
            onClick={() => {
              setView("form");
              setJsonText(documentJson(workflow));
            }}
          >
            {t("features.workflow.modeForm")}
          </Button>
          <Button
            onClick={() => {
              setView("json");
              setJsonText(documentJson(workflow));
            }}
          >
            {t("features.workflow.modeJson")}
          </Button>
          <Button onClick={() => setPublishConfirmOpen(true)}>
            {t("features.workflow.publish")}
          </Button>
          <Button
            type="primary"
            onClick={() => {
              setProblems([]);
              setRunOpen(true);
            }}
          >
            {t("chat.workflow.run")}
          </Button>
          <Button
            type="primary"
            onClick={() => {
              setSaved(true);
            }}
          >
            {t("common.save")}
          </Button>
          <Tag color={saved ? "green" : "orange"}>
            {saved
              ? t("features.workflow.saved")
              : t("features.workflow.statusDraft")}
          </Tag>
        </Space>
      </div>
      {problems.length > 0 && (
        <Alert
          className={styles.wideCard}
          type="error"
          showIcon
          message={t("features.workflow.refusalClient")}
          description={
            <ul>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          }
        />
      )}
      {view === "json" ? (
        <Card
          className={styles.wideCard}
          title={t("features.workflow.jsonArea")}
        >
          <Input.TextArea
            rows={18}
            value={jsonText}
            onChange={(event) => setJsonText(event.target.value)}
          />
          {jsonError.length > 0 && (
            <Alert type="error" message={jsonError.join("; ")} />
          )}
          <Button type="primary" onClick={saveJson}>
            {t("common.save")}
          </Button>
        </Card>
      ) : (
        <>
          <WorkflowInputsSection
            inputs={workflow.inputs ?? { type: "object", properties: {} }}
            problems={problems}
            readOnly={false}
            onChange={(inputs) => draft({ inputs })}
          />
          <WorkflowStepsSection
            steps={steps}
            problems={problems}
            active={workflow.status === "active"}
            readOnly={false}
            onChange={(next) => draft({ steps: next })}
          />
          <WorkflowOutputsSection
            outputs={workflow.outputs ?? []}
            problems={problems}
            readOnly={false}
            onChange={(outputs) => draft({ outputs })}
          />
          <WorkflowRulesSection
            rules={workflow.rules ?? []}
            problems={problems}
            readOnly={false}
            onChange={(rules) => draft({ rules })}
          />
        </>
      )}
      <Card
        className={styles.wideCard}
        title={t("interfacePreview.full.workspace.runHistory")}
      >
        <Alert
          type="warning"
          showIcon
          message={t(
            "interfacePreview.full.workspace.workflowHistoryLocalNotice",
          )}
        />
        {runResult && <Typography.Paragraph>{runResult}</Typography.Paragraph>}
        <List
          dataSource={runHistory}
          locale={{ emptyText: t("interfacePreview.full.workspace.noHistory") }}
          renderItem={(entry) => (
            <List.Item>
              {entry}
              <Tag>
                {t("interfacePreview.full.workspace.sampleNotExecuted")}
              </Tag>
            </List.Item>
          )}
        />
        {changes.length > 0 && (
          <Button
            onClick={() => {
              onChange(changes[0]);
              setChanges((current) => current.slice(1));
            }}
          >
            {t("workflowChange.revert")}
          </Button>
        )}
      </Card>
      <div className={styles.wideCard}>
        <DemoBoundary>
          {t("interfacePreview.full.workspace.workflowLocalNote")}
        </DemoBoundary>
      </div>
      <Modal
        title={t("chat.workflow.run")}
        open={runOpen}
        onCancel={() => setRunOpen(false)}
        onOk={confirmRun}
      >
        <Typography.Paragraph>
          {t("interfacePreview.businessFlow.runParameters")}
        </Typography.Paragraph>
        <Input.TextArea
          rows={8}
          value={runParams}
          onChange={(event) => setRunParams(event.target.value)}
        />
        {problems.length > 0 && (
          <Alert type="error" message={problems.join("; ")} />
        )}
        <Alert
          type="info"
          showIcon
          message={t(
            "interfacePreview.full.workspace.workflowHistoryLocalNotice",
          )}
        />
      </Modal>
      <Modal
        title={t("features.workflow.publish")}
        open={publishConfirmOpen}
        onCancel={() => setPublishConfirmOpen(false)}
        onOk={() => {
          publish();
          setPublishConfirmOpen(false);
        }}
      >
        <Typography.Paragraph>
          {t("features.workflow.publishConfirm")}
        </Typography.Paragraph>
      </Modal>
    </div>
  );
}
