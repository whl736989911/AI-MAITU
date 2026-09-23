/**
 * Features, over the one endpoint that is theirs.
 *
 * A feature *is* an agent (``utils/agentKind.ts``): creating one creates the agent
 * that carries it, and everything after that — its capabilities, its workspace, its
 * runtime state, its profile, its delete — is the agents API the experts' own
 * surfaces already speak. So this module is the creation call and nothing else,
 * rather than a second client for an agent.
 */

import { request } from "../request";

/** What ``POST /features`` answers: the creation's receipt. */
export interface FeatureCreated {
  /** The new feature's agent id — ``feat-<feature_id>``, the server's to mint. */
  agent_id: string;
  /** Always ``feature``; the marker every surface reads. */
  kind: string;
  name: string;
  /** Runtime state as the server records it (``created`` until it is started). */
  state: string;
}

export interface FeatureCreateInput {
  /** The feature's id; its agent is created as ``feat-<feature_id>``. */
  feature_id: string;
  name: string;
  description?: string | null;
  icon_name?: string | null;
  color?: string | null;
  default_model?: string | null;
}

export const featuresApi = {
  /** Create a feature and its agent. Refusals are the manager's own, as they are. */
  create: (body: FeatureCreateInput) =>
    request<FeatureCreated>("/features", {
      method: "POST",
      body: JSON.stringify(body),
    }),
};
