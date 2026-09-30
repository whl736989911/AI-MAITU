import type { OctopRole } from "../api/modules/auth";
import { isFeatureAgent } from "./agentKind";

export interface SharedExpertAccess {
  is_shared?: boolean;
  is_owner?: boolean;
  /** Feature-specific server verdict; ownership alone does not decide after publication. */
  can_manage?: boolean;
  /** What the row is — see ``utils/agentKind.ts``. Absent means an expert. */
  kind?: string | null;
}

export function isSharedExpertViewer(agent: SharedExpertAccess): boolean {
  return agent.is_shared === true && agent.is_owner === false;
}

/** True when the current user may manage this expert (not a share-only viewer). */
export function isOwnedExpert(agent: SharedExpertAccess): boolean {
  return !isSharedExpertViewer(agent);
}

/**
 * Experts are managed by their owner or a system administrator. Features use
 * the server's explicit verdict: drafts belong to their author, while published
 * features belong to the administrators of their enterprise.
 *
 * The role may still be ``null`` (the current user is in flight); read as "not an
 * administrator", which only means a card is drawn without its controls until the
 * answer lands.
 */
export function canManageExpert(
  agent: SharedExpertAccess,
  role: OctopRole | null,
): boolean {
  if (isFeatureAgent(agent) && agent.can_manage !== undefined) {
    return agent.can_manage;
  }
  return agent.is_owner !== false || role === "admin";
}

/**
 * Resolve the expert whose skill catalog can be shown in chat.
 *
 * Shared-expert viewers have read access to the expert's skills; ownership is
 * only required for mutations. Keep readiness/loading as the only UI gates so
 * the composer can inspect and select skills exposed by a shared expert.
 */
export function chatSkillCatalogAgentId(
  agentId: string | null | undefined,
  agentChatReady: boolean,
  agentsLoading: boolean,
): string | null {
  if (!agentChatReady || agentsLoading) return null;
  return agentId ?? null;
}

/**
 * Experts the user owns — for Experts / Personalization / agent bars.
 *
 * A feature's agent is *not* one of them, and the kind is what says so: a
 * feature's author owns it, so ownership alone would offer it as an expert on
 * every surface that picks one — beside the feature's own list, and with a write
 * rule that is not an expert's (``CAPABILITY_POLICY`` in
 * ``PersonalizationPanels``). A feature is reached from its own list, where its
 * agent is configured by the same surfaces an expert's is; the experts' pickers
 * are about the experts a person holds.
 */
export function ownedExperts<T extends SharedExpertAccess>(agents: T[]): T[] {
  return agents.filter(
    (agent) => isOwnedExpert(agent) && !isFeatureAgent(agent),
  );
}

/**
 * Features the caller may configure, as distinct from read-only feature cards.
 * For features the server resolves enterprise role ownership after publication.
 */
export function ownedFeatures<T extends SharedExpertAccess>(agents: T[]): T[] {
  return agents.filter(
    (agent) =>
      isFeatureAgent(agent) && (agent.can_manage ?? isOwnedExpert(agent)),
  );
}
