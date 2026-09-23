/**
 * 工作流 — what the feature asks for, the steps it runs, what comes back.
 *
 * This is the definition's editor: the document ``.octop/workflow.json`` holds,
 * edited by its author and read by callers once active. The four sections edit the
 * document itself — the form holds *the document*, not a form-shaped copy of it —
 * so what the JSON tab shows is what saving writes, and saving writes exactly what
 * the server validates.
 *
 * **Nothing invalid leaves the browser.** The editor validates the document before
 * it sends it (``workflowDocument`` mirrors the server's own checks), so a save that
 * cannot succeed is refused on the spot, and the same list is what the sections
 * mark their problems with. The server still validates again and answers with every
 * problem at once; that answer is rendered whole — split from ``details.reason`` —
 * because a refusal that hides problems behind a generic sentence costs the author a
 * round-trip per problem.
 *
 * **The status control is the difference the server makes.** A draft is checked for
 * shape and may still be half-written; an active definition must stand on its own
 * (every step named, identified and told what to do). Nothing else in the document
 * changes: publishing is the same document, declared runnable.
 *
 * **A stored file that cannot be read is said, not hidden.** The read answers
 * ``error`` for a definition that was hand-edited into something invalid; the
 * editor shows the reason and starts from an empty document, because the broken one
 * is not a document this build can offer to fix in place.
 *
 * Read-only for anyone but the author: a caller may read the active workflow
 * (the run form is built from it) and never write it, so the controls are disabled
 * and the reason is on screen rather than implied by their absence.
 */

import { useEffect, useMemo, useState } from "react";
import { Alert, Button, Popconfirm, Segmented, Spin, Tag } from "antd";
import { message } from "@/utils/antdMessage";
import { useTranslation } from "react-i18next";

import {
  featureWorkflowApi,
  type FeatureWorkflow,
} from "../../../api/modules/featureWorkflow";
import { apiErrorMessage, parseApiError } from "../../../utils/apiError";
import { showConfirmModal } from "../../../utils/confirmModal";
import {
  documentJson,
  emptyWorkflow,
  normalizeWorkflow,
  readWorkflowJson,
  validateWorkflowDocument,
  workflowToJson,
} from "../utils/workflowDocument";
import WorkflowInputsSection from "./WorkflowInputsSection";
import WorkflowStepsSection from "./WorkflowStepsSection";
import WorkflowOutputsSection from "./WorkflowOutputsSection";
import WorkflowRulesSection from "./WorkflowRulesSection";
import WorkflowHistoryPanel from "./WorkflowHistoryPanel";
import styles from "./FeatureWorkflowPanel.module.less";

export interface FeatureWorkflowPanelProps {
  /** The feature's agent id — the definition lives in that agent's workspace. */
  agentId: string;
  /** Whether the caller may write it, read off the feature row's ``is_owner``. */
  canWrite: boolean;
}

type EditorMode = "form" | "json";

export default function FeatureWorkflowPanel({
  agentId,
  canWrite,
}: FeatureWorkflowPanelProps) {
  const { t } = useTranslation();
  const [loading, setLoading] = useState(true);
  /**
   * Why the read failed, kept as the error itself and worded at render time.
   *
   * Deliberately not a message: the load effect must depend on the feature alone.
   * A message built inside it would capture ``t``, whose identity changes with the
   * language — and an effect that re-runs on every render, as ``t`` does in a
   * re-rendering parent (and in every test harness that stubs i18n), would read the
   * definition over and over and clear what the author is in the middle of doing.
   */
  const [loadFailure, setLoadFailure] = useState<unknown>(null);
  const [saving, setSaving] = useState(false);
  const [removing, setRemoving] = useState(false);
  const [workflow, setWorkflow] = useState(emptyWorkflow);
  /** The reason a stored definition cannot be read, straight from the read. */
  const [storedError, setStoredError] = useState<string | null>(null);
  /** Whether the server holds a definition — a feature need not declare one. */
  const [stored, setStored] = useState(false);
  const [storedStatus, setStoredStatus] = useState<"draft" | "active">("draft");
  const [mode, setMode] = useState<EditorMode>("form");
  const [jsonText, setJsonText] = useState("");
  const [problems, setProblems] = useState<readonly string[]>([]);
  /** Whether ``problems`` is what the editor refused or what the server refused. */
  const [problemsFromServer, setProblemsFromServer] = useState(false);

  useEffect(() => {
    let cancelled = false;
    const load = async () => {
      setLoading(true);
      try {
        const answer = await featureWorkflowApi.get(agentId);
        if (cancelled) return;
        const loaded = answer.workflow ?? emptyWorkflow();
        setStored(answer.workflow !== null);
        setStoredError(answer.error);
        setStoredStatus(
          answer.workflow?.status === "active" ? "active" : "draft",
        );
        setWorkflow(loaded);
        // The read's own copy, verbatim: the JSON tab shows what the file holds.
        setJsonText(documentJson(loaded));
        setProblems([]);
      } catch (error) {
        if (cancelled) return;
        setLoadFailure(error);
      } finally {
        if (!cancelled) setLoading(false);
      }
    };
    void load();
    return () => {
      cancelled = true;
    };
  }, [agentId]);

  // The document's own problems, recomputed as it is edited: they are what the
  // outline's badges mark and what a save is refused on. A document is a handful of
  // steps, so this is far cheaper than it looks — and it is the same list the server
  // would answer with.
  const liveProblems = useMemo(
    () =>
      mode === "form"
        ? validateWorkflowDocument(normalizeWorkflow(workflow))
        : [],
    [mode, workflow],
  );
  const markedProblems = problems.length > 0 ? problems : liveProblems;

  /** Every edit drops the previous list: it is about a document that no longer is. */
  const updateWorkflow = (next: FeatureWorkflow) => {
    setWorkflow(next);
    setProblems([]);
  };

  const handleModeChange = (nextMode: EditorMode) => {
    if (nextMode === mode) return;
    if (nextMode === "json") {
      // Whatever the form holds is the document — including its problems, which the
      // JSON tab shows as the same list rather than hiding behind a valid-looking text.
      setJsonText(workflowToJson(workflow));
      setProblems([]);
      setMode("json");
      return;
    }
    const read = readWorkflowJson(jsonText);
    if (read.workflow === null) {
      setProblems(read.problems);
      setProblemsFromServer(false);
      message.error(t("features.workflow.jsonInvalid"));
      return;
    }
    setWorkflow(read.workflow);
    setProblems([]);
    setMode("form");
  };
  const persistDocument = async (document: FeatureWorkflow) => {
    setSaving(true);
    try {
      const answer = await featureWorkflowApi.put(agentId, document);
      const storedWorkflow = answer.workflow ?? emptyWorkflow();
      setStoredStatus(storedWorkflow.status === "active" ? "active" : "draft");
      setStored(answer.workflow !== null);
      setStoredError(answer.error);
      setWorkflow(storedWorkflow);
      setJsonText(documentJson(storedWorkflow));
      setProblems([]);
      message.success(t("features.workflow.saved"));
    } catch (error) {
      const parsed = parseApiError(error);
      const reason = parsed?.details?.reason;
      if (parsed?.code === "WORKFLOW_INVALID" && typeof reason === "string") {
        // The server joins its problems with "; " — unsplit, one long sentence would
        // be the same "invalid definition" the field-level list exists to avoid.
        const refused = reason.split("; ").filter((item) => item.trim() !== "");
        setProblems(refused);
        setProblemsFromServer(true);
      } else {
        message.error(
          apiErrorMessage(error, t("features.workflow.saveFailed"), t),
        );
      }
    } finally {
      setSaving(false);
    }
  };

  const handleSave = () => {
    let document = normalizeWorkflow(workflow);
    if (mode === "json") {
      const read = readWorkflowJson(jsonText);
      if (read.workflow === null) {
        setProblems(read.problems);
        setProblemsFromServer(false);
        return;
      }
      document = read.workflow;
    } else {
      const refused = validateWorkflowDocument(document);
      if (refused.length > 0) {
        setProblems(refused);
        setProblemsFromServer(false);
        return;
      }
    }
    if (document.status === "active" && storedStatus !== "active") {
      showConfirmModal({
        title: t("features.workflow.publish"),
        content: t("features.workflow.publishConfirm"),
        onOk: () => persistDocument(document),
      });
      return;
    }
    void persistDocument(document);
  };

  const handleRemove = async () => {
    setRemoving(true);
    try {
      await featureWorkflowApi.remove(agentId);
      const blank = emptyWorkflow();
      setWorkflow(blank);
      setJsonText(documentJson(blank));
      setStored(false);
      setStoredError(null);
      setProblems([]);
      setStoredStatus("draft");
      message.success(t("features.workflow.removed"));
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.workflow.removeFailed"), t),
      );
    } finally {
      setRemoving(false);
    }
  };

  if (loading) {
    return (
      <div className={styles.loading}>
        <Spin />
      </div>
    );
  }

  // A definition that could not be read is not an empty one: an editor here would
  // offer to write a document this caller may not write at all (an expert's agent
  // has no workflow), or overwrite a feature's definition in the wrong workspace.
  if (loadFailure !== null) {
    return (
      <Alert
        type="error"
        showIcon
        message={t("features.workflow.loadFailed")}
        description={apiErrorMessage(
          loadFailure,
          t("features.workflow.loadFailed"),
          t,
        )}
      />
    );
  }

  const readOnly = !canWrite;

  return (
    <div className={styles.panel}>
      <p className={styles.intro}>{t("features.workflow.intro")}</p>

      {storedError === null ? null : (
        <Alert
          type="warning"
          showIcon
          className={styles.notice}
          message={t("features.workflow.storedInvalid")}
          description={storedError}
        />
      )}

      {readOnly ? (
        <Alert
          type="info"
          showIcon
          className={styles.notice}
          message={t("features.workflow.authorOnly")}
        />
      ) : null}

      <div className={styles.toolbar}>
        <Segmented
          value={mode}
          onChange={(value) => handleModeChange(value as EditorMode)}
          options={[
            { value: "form", label: t("features.workflow.modeForm") },
            { value: "json", label: t("features.workflow.modeJson") },
          ]}
        />
        {stored ? null : (
          <Tag bordered={false}>{t("features.workflow.notDefined")}</Tag>
        )}
        <span className={styles.toolbarGap} />
        <span className={styles.statusControl}>
          <span className={styles.fieldLabel}>
            {t("features.workflow.statusLabel")}
          </span>
          <Segmented
            value={workflow.status === "active" ? "active" : "draft"}
            disabled={readOnly}
            onChange={(value) =>
              updateWorkflow({
                ...workflow,
                status: value === "active" ? "active" : "draft",
              })
            }
            options={[
              { value: "draft", label: t("features.workflow.statusDraft") },
              { value: "active", label: t("features.workflow.statusActive") },
            ]}
          />
        </span>
      </div>
      <p className={styles.statusHint}>{t("features.workflow.statusHint")}</p>

      {problems.length > 0 ? (
        <Alert
          type="error"
          showIcon
          className={styles.notice}
          message={
            problemsFromServer
              ? t("features.workflow.refusalServer")
              : t("features.workflow.refusalClient")
          }
          description={
            <ul className={styles.problemList}>
              {problems.map((problem) => (
                <li key={problem}>{problem}</li>
              ))}
            </ul>
          }
        />
      ) : null}

      {mode === "json" ? (
        <div className={styles.jsonPane}>
          <p className={styles.sectionHint}>
            {t("features.workflow.jsonHint")}
          </p>
          <textarea
            className={styles.jsonArea}
            value={jsonText}
            spellCheck={false}
            readOnly={readOnly}
            aria-label={t("features.workflow.jsonArea")}
            onChange={(event) => {
              setJsonText(event.target.value);
              setProblems([]);
            }}
          />
        </div>
      ) : (
        <div className={styles.body}>
          <WorkflowInputsSection
            inputs={workflow.inputs ?? { type: "object", properties: {} }}
            problems={markedProblems}
            readOnly={readOnly}
            onChange={(inputs) => updateWorkflow({ ...workflow, inputs })}
          />
          <WorkflowStepsSection
            steps={workflow.steps ?? []}
            problems={markedProblems}
            active={workflow.status === "active"}
            readOnly={readOnly}
            onChange={(steps) => updateWorkflow({ ...workflow, steps })}
          />
          <WorkflowOutputsSection
            outputs={workflow.outputs ?? []}
            problems={markedProblems}
            readOnly={readOnly}
            onChange={(outputs) => updateWorkflow({ ...workflow, outputs })}
          />
          <WorkflowRulesSection
            rules={workflow.rules ?? []}
            problems={markedProblems}
            readOnly={readOnly}
            onChange={(rules) => updateWorkflow({ ...workflow, rules })}
          />
        </div>
      )}

      {readOnly ? null : (
        <div className={styles.footer}>
          {stored ? (
            <Popconfirm
              title={t("features.workflow.removeConfirm")}
              onConfirm={() => void handleRemove()}
            >
              <Button danger loading={removing}>
                {t("features.workflow.removeWorkflow")}
              </Button>
            </Popconfirm>
          ) : null}
          <Button
            type="primary"
            loading={saving}
            onClick={() => void handleSave()}
          >
            {t("common.save")}
          </Button>
        </div>
      )}
      <WorkflowHistoryPanel agentId={agentId} canWrite={canWrite} />
    </div>
  );
}
