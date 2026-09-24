/**
 * A feature's workflow inputs and results for a chat thread.
 *
 * The pending input form stays in the composer dock. After submission its
 * compact, expandable summary moves to the chat header; produced files remain
 * in the dock below the conversation.
 *
 * Nothing renders for an expert, for a feature that declares no workflow, or for
 * one whose workflow declares no inputs — those threads look exactly as they did
 * before the card existed.
 */

import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createPortal } from "react-dom";

import type { ChatAttachment } from "../hooks/useChat";
import { useRunArtifacts, useWorkflowRun } from "../hooks/useWorkflowRun";
import type { FeatureRunPayload } from "../utils/featureRun";
import WorkflowInputCard from "./WorkflowInputCard";
import WorkflowOutputCard from "./WorkflowOutputCard";
import styles from "../index.module.less";

export interface WorkflowRunCardsProps {
  agentId: string | null | undefined;
  /** The agent's row ``kind`` — only a feature's own agent has a workflow. */
  agentKind: string | null | undefined;
  threadId: string | null | undefined;
  /** The feature's display name, for the turn a run sends. */
  featureName?: string | null;
  /** A turn is in flight, or the thread is still being created. */
  busy: boolean;
  isStreaming: boolean;
  /** Send the run: one ordinary chat turn, carrying this run as ``feature_run``. */
  onRun: (
    text: string,
    attachments: ChatAttachment[],
    payload: FeatureRunPayload,
  ) => void;
}

export default function WorkflowRunCards({
  agentId,
  agentKind,
  threadId,
  featureName,
  busy,
  isStreaming,
  onRun,
}: WorkflowRunCardsProps) {
  const [summaryContainer, setSummaryContainer] =
    useState<HTMLElement | null>(null);
  useEffect(() => {
    setSummaryContainer(
      document.querySelector<HTMLElement>("[data-workflow-input-summary]"),
    );
  }, []);
  const { t } = useTranslation();
  const { inputs, definition, run, loading, markSubmitted } = useWorkflowRun({
    agentId,
    agentKind,
    threadId,
  });
  const artifacts = useRunArtifacts({
    agentId,
    threadId,
    isStreaming,
    enabled: run !== null,
  });

  if (loading || !inputs) return null;

  const name = (featureName ?? "").trim();
  const submit = (
    attachments: ChatAttachment[],
    payload: FeatureRunPayload,
  ) => {
    markSubmitted(payload.inputs);
    onRun(
      name
        ? t("chat.workflow.runMessage", { name })
        : t("chat.workflow.runMessagePlain"),
      attachments,
      payload,
    );
  };

  return (
    <>
      {run && summaryContainer
        ? createPortal(
            <div className={styles.workflowInputSummary}>
              <WorkflowInputCard
                inputs={inputs}
                run={run}
                agentId={agentId ?? ""}
                busy={busy}
                onRun={({ attachments, payload }) =>
                  submit(attachments, payload)
                }
              />
            </div>,
            summaryContainer,
          )
        : null}
      <div className={styles.workflowDock}>
        <div className={styles.workflowDockInner}>
          <div className={styles.workflowStack}>
            {!run ? (
              <WorkflowInputCard
                inputs={inputs}
                run={null}
                agentId={agentId ?? ""}
                busy={busy}
                onRun={({ attachments, payload }) =>
                  submit(attachments, payload)
                }
              />
            ) : null}
            {run ? (
              <WorkflowOutputCard
                agentId={agentId ?? ""}
                files={artifacts}
                outputs={definition?.outputs}
              />
            ) : null}
          </div>
        </div>
      </div>
    </>
  );
}
