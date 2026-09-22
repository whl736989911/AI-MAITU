/**
 * Which kinds of agent this deployment holds — the one answer every surface that
 * splits itself by ``kind`` reads.
 *
 * A surface may show a branch for a kind, or show nothing for it, but the answer
 * has to be a fact about the deployment rather than about whatever is on screen:
 * a table is paged and a filter moves, so a branch that came and went with the
 * current page would be a different page every page. The deployment's own agent
 * list is therefore the source — the same rows Admin → Users reads — and it is
 * read once per mount, not per render.
 *
 * ``GET /agents?scope=all`` is the deployment's list, and the ``agents`` list in
 * ``AgentContext`` is the same question narrowed by what the caller may see.
 * A caller who may read the former gets the deployment's answer; everyone else
 * gets the answer for every agent they can reach, which is the closest a surface
 * can honestly come to it without a permission they do not hold. Neither is ever
 * derived from the current page, filter, row or selection — that is the whole
 * point.
 *
 * An empty result is "no such kind", not "not asked yet": a fresh deployment
 * with no agents has no kinds to draw either, and a branch that appears once
 * agents exist is the branch appearing for the first time.
 */

import { useEffect, useMemo, useState } from "react";
import { request } from "../api/request";
import { useAgent, type OctopAgent } from "../context/AgentContext";
import { indexAgentsByKind, type AgentKindPresence } from "../utils/agentKindCounts";
import { userCan } from "../utils/permissions";
import { useCurrentUser } from "./useCurrentUser";

/** The permission the deployment-wide agent list is read behind (``agents.py``). */
const DEPLOYMENT_LIST_PERMISSION = "users";

export function useAgentKindPresence(): AgentKindPresence {
  const { agents } = useAgent();
  const user = useCurrentUser();
  const seesDeployment = userCan(user, DEPLOYMENT_LIST_PERMISSION);
  const [deployment, setDeployment] = useState<OctopAgent[] | null>(null);

  useEffect(() => {
    if (!seesDeployment) {
      setDeployment(null);
      return;
    }
    let cancelled = false;
    void request<OctopAgent[]>("/agents?scope=all")
      .then((rows) => {
        if (!cancelled) setDeployment(rows);
      })
      .catch(() => {
        // The caller may read the list but the read failed: fall back to the
        // agents they can see rather than claiming the deployment holds
        // nothing. A subset can under-report a kind; it never invents one.
        if (!cancelled) setDeployment(null);
      });
    return () => {
      cancelled = true;
    };
  }, [seesDeployment]);

  // One object for as long as the list behind it is the same one, so a surface
  // may put the answer in a dependency list without re-running every render.
  const list = deployment ?? agents;
  return useMemo(() => indexAgentsByKind(list).held, [list]);
}
