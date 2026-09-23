/**
 * Which kinds of agent this deployment holds — the one answer every surface that
 * splits itself by ``kind`` reads.
 *
 * A surface may show a branch for a kind, or show nothing for it, but the answer
 * has to be a fact about the deployment rather than about whatever is on screen:
 * a table is paged and a filter moves, so a branch that came and went with the
 * current page would be a different page every page. ``GET /agents/kinds`` is
 * that fact — the whole ``agents`` table, asked once — and it is asked for on
 * mount, not per render.
 *
 * **Why not the agent list.** ``GET /agents`` answers "which agents may this
 * caller see": ``scope=all`` is behind the ``users`` permission, so a caller
 * without it would name the branches from a subset and hide one the deployment
 * holds (they hold no feature, so the features' branch would never be drawn). The
 * kinds are not scoped, so they are not read from a scoped list.
 *
 * A read that fails falls back to the agents in ``AgentContext`` rather than
 * claiming the deployment holds nothing: that list is a subset, so it can
 * under-report a kind — it never invents one.
 *
 * An empty answer is "no such kind", not "not asked yet": a fresh deployment with
 * no agents has no kinds to draw either, and a branch that appears once agents
 * exist is the branch appearing for the first time.
 */

import { useEffect, useMemo, useState } from "react";
import { request } from "../api/request";
import { useAgent } from "../context/AgentContext";
import { isFeatureAgent } from "../utils/agentKind";
import {
  indexAgentsByKind,
  type AgentKindPresence,
} from "../utils/agentKindCounts";

/** The kinds the deployment holds, as ``GET /agents/kinds`` answers it. */
interface AgentKindsPayload {
  kinds: string[];
}

/**
 * The deployment's kinds as presence — :func:`indexAgentsByKind`'s rule, applied
 * to the kinds the deployment names instead of to a list of its rows. A kind that
 * is not a feature's is an expert's, exactly as a row without ``kind`` is an
 * expert; the two answers are the same question asked of two shapes.
 */
function presenceFromKinds(kinds: readonly string[]): AgentKindPresence {
  const held = { experts: false, features: false };
  for (const kind of kinds) {
    if (isFeatureAgent({ kind })) held.features = true;
    else held.experts = true;
  }
  return held;
}

export function useAgentKindPresence(): AgentKindPresence {
  const { agents } = useAgent();
  const [kinds, setKinds] = useState<string[] | null>(null);

  useEffect(() => {
    let cancelled = false;
    void request<AgentKindsPayload>("/agents/kinds")
      .then((payload) => {
        if (!cancelled) setKinds(payload.kinds);
      })
      .catch(() => {
        // The read failed: answer from the agents the caller can see rather than
        // claiming the deployment holds nothing.
        if (!cancelled) setKinds(null);
      });
    return () => {
      cancelled = true;
    };
  }, []);

  // One object for as long as the answer behind it is the same one, so a surface
  // may put the answer in a dependency list without re-running every render.
  return useMemo(
    () =>
      kinds === null
        ? indexAgentsByKind(agents).held
        : presenceFromKinds(kinds),
    [kinds, agents],
  );
}
