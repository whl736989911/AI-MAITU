/**
 * What an agent row *is*, on this side of the wire — the client's mirror of the
 * server's ``agents.kind`` (``src/octop/infra/agents/kinds.py``).
 *
 * A **feature** is an agent (:data:`KIND_FEATURE`): one is created together with
 * every feature, its ``user_id`` is the person who defined that feature, and it is
 * the conversation entry point every caller of the feature talks to. Everything
 * else a person holds — their own experts, a copy made from a template — is an
 * ordinary agent (:data:`KIND_AGENT`).
 *
 * "Who owns this" and "what is this" are two answers from two columns, and this is
 * the second one. Nothing here looks at an id prefix: every surface that has to
 * treat a feature's agent differently from an expert's asks
 * :func:`isFeatureAgent`, so the question is answered in one place rather than
 * re-derived per fragment.
 *
 * A payload without the field is an expert. That is not a fallback for a broken
 * response: it is what every agent was before the column existed, and a row that
 * does not say it is a feature is one.
 */

export const KIND_AGENT = "agent";
/** A feature's own agent — created with the feature, owned by its author. */
export const KIND_FEATURE = "feature";

export type AgentKind = typeof KIND_AGENT | typeof KIND_FEATURE;

/** The part of a row the kind question is answered from. */
export interface AgentKindCarrier {
  kind?: string | null;
}

/** Whether this row is the agent a feature runs on. */
export function isFeatureAgent(agent: AgentKindCarrier): boolean {
  return agent.kind === KIND_FEATURE;
}
