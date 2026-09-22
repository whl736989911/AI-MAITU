/**
 * Who holds which *kind* of agent — the tally behind the Admin → Users
 * 「专家」/「功能」 columns and the same pair of numbers on a user card.
 *
 * ``GET /agents?scope=all`` hands back one flat list, and this page reads it
 * two ways:
 *
 * * **Per owner** — a row's own two counts, so a user's experts are not
 *   counted with the agents their features run on.
 * * **Instance-wide** — whether a kind exists at all, which decides whether
 *   its column is drawn. A kind nobody holds anywhere would be a header
 *   promising agents the deployment does not have, over a column of zeroes.
 *
 * Which kind a row is comes from :func:`isFeatureAgent` and nothing else: an
 * id is not asked whether it looks like a feature's (``utils/agentKind.ts``).
 * A row without ``kind`` is an expert, which is what every agent was before
 * the column existed.
 */

import type { OctopAgent } from "../../../context/AgentContext";
import { isFeatureAgent } from "../../../utils/agentKind";

/** One owner's agents, split by kind. */
export interface AgentKindCounts {
  /** Ordinary agents — a person's own experts. */
  experts: number;
  /** Agents features run on. */
  features: number;
}

/** The tally of an owner with no agents; rows outnumber owners with agents. */
export const NO_AGENT_KIND_COUNTS: AgentKindCounts = { experts: 0, features: 0 };

/** How the page reads the flat list: per owner, and per deployment. */
export interface AgentKindIndex {
  /** Owner id → that owner's tally. Owners without agents are absent. */
  countsByUserId: Map<number, AgentKindCounts>;
  /** Whether the deployment holds a kind at all — i.e. whether it is drawn. */
  held: { experts: boolean; features: boolean };
}

/**
 * Index an ``/agents?scope=all`` list by owner and by kind.
 *
 * The two questions answer from different sets of rows on purpose: a tally
 * needs an owner to hang on, while a kind that exists somewhere is a kind the
 * page can look up — a feature whose author was deleted still means this
 * deployment runs features.
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
