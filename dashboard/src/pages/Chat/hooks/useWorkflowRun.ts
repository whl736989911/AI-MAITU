/**
 * A feature's workflow, as a chat thread needs it: the form it asks for, the run
 * this thread already has, and the files that run produced.
 *
 * **The card is a convenience, never a gate.** Every read here fails soft — a
 * definition that cannot be read leaves the thread exactly as it was, because a
 * feature nobody can render a card for is still a feature you can talk to.
 *
 * **A run has two sources and they answer different questions.** The local one is
 * what *this* session submitted a moment ago (`markSubmitted`): the run is on its
 * way, but the row the server keeps is written as the turn starts, so waiting for
 * it would flash the form back under the caller's hands. The server one is the
 * caller's own runs (`GET /agents/{id}/workflow/runs`), matched by thread — the
 * only way to know, after a reload, that this conversation already ran.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import {
  featureWorkflowApi,
  type FeatureWorkflow,
  type WorkflowInputs,
  type WorkflowRunItem,
} from "../../../api/modules/featureWorkflow";
import { isFeatureAgent } from "../../../utils/agentKind";
import { PENDING_THREAD_ID } from "../constants";
import { declaredInputs } from "../utils/featureRun";
import { fetchAndSyncSessionArtifacts } from "./useSessions";

/** The run a thread has: its values, and its identity when the server wrote it. */
export interface ThreadRun {
  /** The run's id, or ``null`` for one this session submitted but has not seen listed. */
  id: string | null;
  /** Unix seconds it was submitted at, when the server wrote it. */
  createdAt: number | null;
  inputs: Record<string, unknown>;
}

export interface UseWorkflowRunResult {
  /** The definition behind the form, for the outputs a run is read under. */
  definition: FeatureWorkflow | null;
  /** The declared form, or ``null`` when there is nothing to show. */
  inputs: WorkflowInputs | null;
  /** What this thread already ran, if anything. */
  run: ThreadRun | null;
  /** Whether the declared form is still being read. */
  loading: boolean;
  /** Record a submission made here, so the card turns read-only at once. */
  markSubmitted: (inputs: Record<string, unknown>) => void;
}

export function useWorkflowRun({
  agentId,
  agentKind,
  threadId,
  enabled = true,
}: {
  agentId: string | null | undefined;
  agentKind: string | null | undefined;
  threadId: string | null | undefined;
  enabled?: boolean;
}): UseWorkflowRunResult {
  const isFeature = isFeatureAgent({ kind: agentKind });
  const [definition, setDefinition] = useState<FeatureWorkflow | null>(null);
  const [runs, setRuns] = useState<WorkflowRunItem[]>([]);
  const [loading, setLoading] = useState(false);
  const [submission, setSubmission] = useState<{
    /** The thread it was sent from — ``__pending__`` while that one is still being created. */
    threadId: string | null;
    inputs: Record<string, unknown>;
  } | null>(null);

  useEffect(() => {
    if (!enabled || !isFeature || !agentId) {
      setDefinition(null);
      setRuns([]);
      setLoading(false);
      return;
    }
    let cancelled = false;
    setLoading(true);
    void (async () => {
      // Both reads are the caller's own: a definition they may reach, and the runs
      // they themselves submitted. A refusal on either is not an error the thread
      // should show — it just means the card knows less.
      const [workflowRead, runsRead] = await Promise.all([
        featureWorkflowApi.get(agentId).catch(() => null),
        featureWorkflowApi.runs(agentId).catch(() => null),
      ]);
      if (cancelled) return;
      setDefinition(workflowRead?.workflow ?? null);
      setRuns(runsRead?.runs ?? []);
      setLoading(false);
    })();
    return () => {
      cancelled = true;
    };
  }, [agentId, isFeature, enabled]);

  // A submission belongs to the agent it was made against — switching features
  // must not carry "already ran" onto another feature's thread.
  useEffect(() => {
    setSubmission(null);
  }, [agentId]);

  useEffect(() => {
    // The thread a run was sent from did not exist yet when it was sent: follow the
    // id it was created as, so the card does not slide back to a form the caller
    // has already filled in.
    setSubmission((current) => {
      if (!current) return current;
      const unresolved =
        current.threadId === null || current.threadId === PENDING_THREAD_ID;
      if (!unresolved) return current;
      if (!threadId || threadId === PENDING_THREAD_ID) return current;
      return { ...current, threadId };
    });
  }, [threadId]);

  const serverRun = useMemo(() => {
    if (!threadId || threadId === PENDING_THREAD_ID) return null;
    return runs.find((item) => item.thread_id === threadId) ?? null;
  }, [runs, threadId]);

  const run = useMemo<ThreadRun | null>(() => {
    if (submission) {
      const unresolved =
        submission.threadId === null ||
        submission.threadId === PENDING_THREAD_ID;
      if (submission.threadId === threadId || unresolved) {
        return { id: null, createdAt: null, inputs: submission.inputs };
      }
    }
    if (!serverRun) return null;
    return {
      id: serverRun.id,
      createdAt: serverRun.created_at,
      inputs: serverRun.inputs,
    };
  }, [submission, serverRun, threadId]);

  const markSubmitted = useCallback(
    (inputs: Record<string, unknown>) => {
      setSubmission({ threadId: threadId ?? null, inputs });
    },
    [threadId],
  );

  return {
    definition,
    inputs: declaredInputs(definition),
    run,
    loading,
    markSubmitted,
  };
}

/**
 * The files this thread produced. Artifacts are collected per thread by the
 * platform (`threads.artifacts`) and read back through the history endpoint; they
 * are re-read when a turn ends, because that is when a run's write tools land.
 */
export function useRunArtifacts({
  agentId,
  threadId,
  isStreaming,
  enabled = true,
}: {
  agentId: string | null | undefined;
  threadId: string | null | undefined;
  isStreaming: boolean;
  enabled?: boolean;
}): string[] {
  const [artifacts, setArtifacts] = useState<string[]>([]);
  const [refreshToken, setRefreshToken] = useState(0);
  const wasStreamingRef = useRef(isStreaming);

  useEffect(() => {
    if (!enabled || !agentId || !threadId || threadId === PENDING_THREAD_ID) {
      setArtifacts([]);
      return;
    }
    let cancelled = false;
    void fetchAndSyncSessionArtifacts(agentId, threadId).then((paths) => {
      if (!cancelled) setArtifacts(paths);
    });
    return () => {
      cancelled = true;
    };
  }, [agentId, threadId, refreshToken, enabled]);

  useEffect(() => {
    const previous = wasStreamingRef.current;
    wasStreamingRef.current = isStreaming;
    if (!previous || isStreaming) return;
    // The turn just ended — what it wrote is on the thread now, a moment later.
    const timer = window.setTimeout(() => setRefreshToken((n) => n + 1), 400);
    return () => window.clearTimeout(timer);
  }, [isStreaming]);

  return artifacts;
}
