/**
 * The feature's own agent, opened and read the way every surface that configures
 * one needs it.
 *
 * ``POST /features/{id}/agent`` *is* the "open this feature's personalization
 * surface" call: it materializes the agent on first use and answers with it on
 * every call after (``created: false``). Its id is the server's (``feat-<id>``),
 * never derived here, and the runtime state comes from the agent status endpoint
 * — the panels need both before they may mount, because a panel rendered without
 * a live agent answers "nothing installed, nothing configured", which reads on
 * screen as a fact about the feature rather than as a page that has not loaded.
 *
 * A failure is therefore returned as itself rather than as an empty agent, and
 * so is "not answered yet": the two pages that configure a feature's agent (the
 * Personalization page's feature scope, and the feature's own page) show the same
 * three states from the same place.
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { featuresApi } from "../api/modules/features";
import { octopAgentsApi } from "../api/modules/octopAgents";

export interface FeatureAgentState {
  /** The feature's agent, or ``null`` while it is being opened, or after a failure. */
  agentId: string | null;
  /** Its runtime state as the server records it; ``""`` until that is known. */
  state: string;
  loading: boolean;
  /**
   * What opening it failed with, kept raw so the surface that shows it can word
   * it with the call's own error. ``null`` while it has not failed.
   */
  failure: unknown;
  /** Ask the server again — for a failed open, or a state that has moved on. */
  retry: () => void;
}

export function useFeatureAgent(featureId: string | null): FeatureAgentState {
  const [agent, setAgent] = useState<{
    agent_id: string;
    state: string;
  } | null>(null);
  const [loading, setLoading] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  /**
   * Which open is the current one. Two calls can be in flight at once (a scope
   * switch, or a retry while the first is still going), and the older answer
   * landing last would point every panel at the agent of a feature the page has
   * already left.
   */
  const ticketRef = useRef(0);

  const open = useCallback(async (id: string) => {
    const ticket = ++ticketRef.current;
    setLoading(true);
    try {
      const personalization = await featuresApi.personalizeFeature(id);
      const { state } = await octopAgentsApi.getAgentStatus(
        personalization.agent_id,
      );
      if (ticketRef.current !== ticket) return;
      setAgent({ agent_id: personalization.agent_id, state });
      setFailure(null);
    } catch (err) {
      if (ticketRef.current !== ticket) return;
      setAgent(null);
      setFailure(err);
    } finally {
      if (ticketRef.current === ticket) setLoading(false);
    }
  }, []);

  useEffect(() => {
    if (featureId === null) {
      // No scope: nothing in flight may land on one we have left.
      ticketRef.current += 1;
      setAgent(null);
      setFailure(null);
      setLoading(false);
      return;
    }
    void open(featureId);
  }, [featureId, open]);

  const retry = useCallback(() => {
    if (featureId !== null) void open(featureId);
  }, [featureId, open]);

  return {
    // Read through the scope as well as the state: the effect that responds to a
    // scope change runs after the render that saw it, and a panel handed last
    // feature's agent for that one render would be configuring the wrong one.
    agentId: featureId === null ? null : agent?.agent_id ?? null,
    state: featureId === null ? "" : agent?.state ?? "",
    loading,
    // Both are forgotten the moment the scope does: they describe one feature's
    // agent, and a page that has moved on must not show the last one's.
    failure: featureId === null ? null : failure,
    retry,
  };
}
