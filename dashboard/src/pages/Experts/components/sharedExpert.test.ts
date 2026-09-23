import { describe, expect, it } from "vitest";
import {
  canManageExpert,
  chatSkillCatalogAgentId,
  isOwnedExpert,
  isSharedExpertViewer,
  ownedExperts,
  ownedFeatures,
} from "../../../utils/sharedExpert";

describe("isSharedExpertViewer", () => {
  it("identifies a shared expert viewed by someone other than its owner", () => {
    expect(isSharedExpertViewer({ is_shared: true, is_owner: false })).toBe(
      true,
    );
  });

  it("does not treat the owner or a private expert as a viewer", () => {
    expect(isSharedExpertViewer({ is_shared: true, is_owner: true })).toBe(
      false,
    );
    expect(isSharedExpertViewer({ is_shared: false, is_owner: false })).toBe(
      false,
    );
  });
});

describe("canManageExpert", () => {
  it("keeps an expert with its owner, shared or not", () => {
    expect(canManageExpert({ is_shared: false, is_owner: true }, "user")).toBe(
      true,
    );
    expect(canManageExpert({ is_shared: true, is_owner: true }, "user")).toBe(
      true,
    );
  });

  it("lets an administrator manage an expert its list holds but is not theirs", () => {
    // The row an administrator's own list is full of: somebody else's expert,
    // private and therefore not a share, which the server accepts every write
    // on (``assert_agent_owner``'s admin bypass) while ``is_owner`` says false.
    expect(
      canManageExpert({ is_shared: false, is_owner: false }, "admin"),
    ).toBe(true);
    expect(canManageExpert({ is_shared: true, is_owner: false }, "admin")).toBe(
      true,
    );
  });

  it("keeps what a plain user does not own read-only, shared or granted", () => {
    // Shared to everyone, and a private expert granted to them: the second is use
    // rather than maintenance (``can_write`` in ``infra/sharing``), and the row
    // reads the same way for both.
    expect(canManageExpert({ is_shared: true, is_owner: false }, "user")).toBe(
      false,
    );
    expect(canManageExpert({ is_shared: false, is_owner: false }, "user")).toBe(
      false,
    );
  });

  it("reads a role it does not have yet as no administrator", () => {
    // ``useUserRole`` answers null until ``/auth/me`` lands.
    expect(canManageExpert({ is_shared: false, is_owner: false }, null)).toBe(
      false,
    );
  });

  it("does not widen the bypass to the scoped administrator roles", () => {
    // Only the system administrator passes ``assert_agent_owner``; the other two
    // are bounded by the org tree and resolved server-side.
    expect(
      canManageExpert({ is_shared: false, is_owner: false }, "unit_admin"),
    ).toBe(false);
    expect(
      canManageExpert(
        { is_shared: false, is_owner: false },
        "enterprise_admin",
      ),
    ).toBe(false);
  });
});

describe("chatSkillCatalogAgentId", () => {
  it("loads skills for a ready shared expert viewed by a non-owner", () => {
    const shared = {
      agent_id: "shared",
      is_shared: true,
      is_owner: false,
    };
    expect(isSharedExpertViewer(shared)).toBe(true);
    expect(chatSkillCatalogAgentId(shared.agent_id, true, false)).toBe(
      "shared",
    );
  });

  it("waits until the expert is ready and agent loading has finished", () => {
    expect(chatSkillCatalogAgentId("shared", false, false)).toBeNull();
    expect(chatSkillCatalogAgentId("shared", true, true)).toBeNull();
  });
});

describe("ownedExperts", () => {
  it("keeps owned experts and drops shared viewers for manage pages", () => {
    const agents = [
      { agent_id: "own", is_shared: true, is_owner: true },
      { agent_id: "shared", is_shared: true, is_owner: false },
      { agent_id: "private", is_shared: false, is_owner: true },
    ];
    expect(ownedExperts(agents).map((a) => a.agent_id)).toEqual([
      "own",
      "private",
    ]);
    expect(isOwnedExpert(agents[1]!)).toBe(false);
    expect(isOwnedExpert(agents[0]!)).toBe(true);
  });

  it("drops a feature's agent even though its author owns it", () => {
    // A feature's author owns the row, so ownership alone would offer it as an
    // expert on every picker; the kind is what keeps it out.
    const agents = [
      { agent_id: "my-expert", is_owner: true },
      { agent_id: "feat-weekly", is_owner: true, kind: "feature" },
      { agent_id: "feat-shared", is_owner: false, kind: "feature" },
    ];
    expect(ownedExperts(agents).map((a) => a.agent_id)).toEqual(["my-expert"]);
  });
});

describe("ownedFeatures", () => {
  it("is the experts' list with the kinds swapped", () => {
    const agents = [
      { agent_id: "my-expert", is_shared: false, is_owner: true },
      {
        agent_id: "feat-mine",
        is_shared: false,
        is_owner: true,
        kind: "feature",
      },
      {
        agent_id: "feat-shared",
        is_shared: true,
        is_owner: false,
        kind: "feature",
      },
    ];
    expect(ownedFeatures(agents).map((a) => a.agent_id)).toEqual(["feat-mine"]);
    // A shared feature is still somebody else's to manage, exactly as a shared
    // expert is — one ownership rule, both kinds.
    expect(ownedExperts(agents).map((a) => a.agent_id)).toEqual(["my-expert"]);
  });
});
