/**
 * Who holds which *kind* of agent — the one tally behind every surface that
 * tells agents apart by ``kind``.
 *
 * A list of agents is read two ways here:
 *
 * * **Per owner** — that owner's two counts, so a user's experts are not
 *   counted with the features they defined.
 * * **Instance-wide** — whether a kind exists at all, which is what decides
 *   whether a kind's branch is drawn at all. A branch nobody holds anywhere
 *   would be a header promising agents the deployment does not have, over a
 *   row of zeroes.
 *
 * Which kind a row is comes from :func:`isFeatureAgent` and nothing else: an id
 * is not asked whether it looks like a feature's (``utils/agentKind.ts``). A row
 * without ``kind`` is an expert, which is what every agent was before the column
 * existed.
 *
 * The two questions answer from different sets of rows on purpose: a tally needs
 * an owner to hang on, while a kind that exists somewhere is a kind the page can
 * look up — a feature whose author was deleted still means this deployment runs
 * features.
 */

import type { OctopAgent } from "../context/AgentContext";
import { isFeatureAgent } from "./agentKind";

/** One owner's agents, split by kind. */
export interface AgentKindCounts {
  /** Ordinary agents — a person's own experts. */
  experts: number;
  /** Agents features run on. */
  features: number;
}

/** The tally of an owner with no agents; rows outnumber owners with agents. */
export const NO_AGENT_KIND_COUNTS: AgentKindCounts = { experts: 0, features: 0 };

/** The two kinds, as "does this list hold one". */
export interface AgentKindPresence {
  experts: boolean;
  features: boolean;
}

/** How a flat list is read: per owner, and per deployment. */
export interface AgentKindIndex {
  /** Owner id → that owner's tally. Owners without agents are absent. */
  countsByUserId: Map<number, AgentKindCounts>;
  /** Whether the list holds a kind at all — i.e. whether it is drawn. */
  held: AgentKindPresence;
}

/**
 * Index an agent list by owner and by kind.
 *
 * Callers hand this the widest list their surface is about: the deployment's own
 * rows where the surface reports on the deployment, the caller's own rows where
 * the surface offers the caller a choice. Either way the answer is one value for
 * a whole list, never one per page or per row — see
 * ``hooks/useAgentKindPresence.ts`` for the deployment's.
 */
export function indexAgentsByKind(
  agents: readonly OctopAgent[],
): AgentKindIndex {
  const countsByUserId = new Map<number, AgentKindCounts>();
  const held = { experts: false, features: false };
  for (const agent of agents) {
    const isFeature = isFeatureAgent(agent);
    if (isFeature) held.features = true;
    else held.experts = true;

    const userId = agent.user_id;
    if (userId == null) continue;
    const counts = countsByUserId.get(userId) ?? { experts: 0, features: 0 };
    if (isFeature) counts.features += 1;
    else counts.experts += 1;
    countsByUserId.set(userId, counts);
  }
  return { countsByUserId, held };
}
