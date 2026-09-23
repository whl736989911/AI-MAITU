import type { OctopRole } from "../api/modules/auth";
import { isFeatureAgent } from "./agentKind";

export interface SharedExpertAccess {
  is_shared?: boolean;
  is_owner?: boolean;
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
 * True when this user may manage the expert behind this row: its owner, or a
 * system administrator.
 *
 * That is the server's whole rule for writing an agent — ``assert_agent_owner``
 * and ``agent_capability_refusal`` in ``api/common/agent.py`` both read
 * "``user.is_admin`` or the row's owner, and nobody else" — and a row cannot say
 * which half of it the caller is. ``is_owner`` answers *did you create this*: an
 * administrator reading a list that holds other people's experts gets ``false``
 * on every row that is not theirs, so a card gating on ownership alone hides the
 * controls the server would have accepted, and an expert's row arrives as an ID
 * and a conversation button. The role is the missing half; ownership still
 * carries it for everybody else.
 *
 * A share-only viewer stays read-only, which is the same statement from the other
 * side: ``is_owner`` is ``false`` for them and their role is no administrator's,
 * so a shared expert keeps offering the conversation and nothing that writes.
 * ``admin`` here is the system administrator alone — ``enterprise_admin`` and
 * ``unit_admin`` are scoped server-side and do not reach this rule
 * (``infra/users/identity.py``).
 *
 * The role may still be ``null`` (the current user is in flight); read as "not an
 * administrator", which only means a card is drawn without its controls until the
 * answer lands.
 */
export function canManageExpert(
  agent: SharedExpertAccess,
  role: OctopRole | null,
): boolean {
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
 * The caller's own features — :func:`ownedExperts`'s mirror for the other kind.
 *
 * A surface that manages the things a feature owns (an automation schedule, say)
 * asks for these; a surface that picks an expert asks for the experts. Same
 * ownership rule on both sides: a share-only viewer gets neither.
 */
export function ownedFeatures<T extends SharedExpertAccess>(agents: T[]): T[] {
  return agents.filter(
    (agent) => isOwnedExpert(agent) && isFeatureAgent(agent),
  );
}
