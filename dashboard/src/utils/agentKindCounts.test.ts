/**
 * A flat agent list has to answer two different questions: what each owner
 * holds, and whether a kind exists in this deployment at all (which is what
 * decides whether a kind's branch is drawn anywhere).
 *
 * What a row *is* comes from its ``kind``, never from its id — the id pairs
 * below say why: an expert may be named ``feat-…`` and a feature's agent need
 * not be. A row with no ``kind`` at all is an expert, which is what every agent
 * was before the field existed.
 *
 * The lists below are the deployment's own (``/agents?scope=all``, as Admin →
 * Users reads it) because that is what the ``held`` half is about; the tallies
 * are the same two counts whatever list they are read from.
 */

import { describe, expect, it } from "vitest";
import type { OctopAgent } from "../context/AgentContext";
import { indexAgentsByKind } from "./agentKindCounts";

function agent(
  agentId: string,
  kind: string | null | undefined,
  userId: number | null,
): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    user_id: userId,
    name: agentId,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: "sparkles",
    icon_url: null,
    color: null,
    config: {},
    is_shared: false,
    is_owner: true,
  };
}

describe("indexAgentsByKind", () => {
  it("attributes nothing to anyone when there are no agents", () => {
    expect(indexAgentsByKind([])).toEqual({
      countsByUserId: new Map(),
      held: { experts: false, features: false },
    });
  });

  it("tallies each owner's two kinds apart", () => {
    const { countsByUserId } = indexAgentsByKind([
      agent("main", "agent", 7),
      agent("feat-weekly", "feature", 7),
      agent("reviewer", undefined, 7),
      agent("feat-daily", "feature", 7),
      agent("assistant", "agent", 9),
    ]);

    expect(countsByUserId.get(7)).toEqual({ experts: 2, features: 2 });
    expect(countsByUserId.get(9)).toEqual({ experts: 1, features: 0 });
  });

  it("counts a kind by its kind and not by what its id looks like", () => {
    const { countsByUserId, held } = indexAgentsByKind([
      // An expert a user named like a feature's agent is still an expert.
      agent("feat-lookalike", "agent", 4),
      // And a feature's agent is a feature without any prefix saying so.
      agent("weekly-report", "feature", 4),
    ]);

    expect(countsByUserId.get(4)).toEqual({ experts: 1, features: 1 });
    expect(held).toEqual({ experts: true, features: true });
  });

  it("treats a row without a kind as an expert", () => {
    const { countsByUserId, held } = indexAgentsByKind([
      agent("legacy", undefined, 3),
      agent("also-legacy", null, 3),
    ]);

    expect(countsByUserId.get(3)).toEqual({ experts: 2, features: 0 });
    expect(held).toEqual({ experts: true, features: false });
  });

  it("counts a kind that exists without an owner as existing", () => {
    const { countsByUserId, held } = indexAgentsByKind([
      // Author deleted: the row has no owner left to hang a tally on, but the
      // deployment still runs features, and a branch is about the deployment.
      agent("orphan", "feature", null),
    ]);

    expect(held).toEqual({ experts: false, features: true });
    expect(countsByUserId.has(0)).toBe(false);
    expect(countsByUserId.size).toBe(0);
  });

  it("leaves an owner with no agents out of the tallies", () => {
    const { countsByUserId } = indexAgentsByKind([agent("main", "agent", 5)]);

    expect(countsByUserId.get(5)).toEqual({ experts: 1, features: 0 });
    expect(countsByUserId.size).toBe(1);
  });
});
