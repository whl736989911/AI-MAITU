import { describe, expect, it } from "vitest";
import { render, screen, within } from "@testing-library/react";
import type { OctopUser } from "../../../api/modules/auth";
import type { FeatureRule } from "../../../api/modules/features";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import type { FeatureLearning } from "./useFeatureLearning";
import RulesPanel from "./RulesPanel";

/**
 * The panel groups the rules it is handed by the layer each one lives at. A layer
 * it cannot name used to be a rule it did not render at all: the card left the
 * screen with nothing said. So these cases pin that every rule the panel receives
 * is shown — the ones it can place under their layer, the ones it cannot under the
 * unfiled heading — and that a feature with no rules still reads as having none.
 */

const ADMIN: OctopUser = {
  id: 1,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
};

/** One stored rule, with only what a case is about spelled out. */
function rule(
  over: Partial<FeatureRule> & Pick<FeatureRule, "id" | "rule_text">,
): FeatureRule {
  return {
    feature_id: "quote-draft",
    status: "approved",
    scope: "global",
    owner_user_id: null,
    unit_key: null,
    unit_label: null,
    source_task_ids: [],
    proposed_by: "ai",
    approved_by: 1,
    created_at: 1_700_000_000,
    reviewed_at: 1_700_000_100,
    ...over,
  };
}

/** Everything ``RulesPanel`` reads off ``useFeatureLearning``, at rest. */
function learning(rules: FeatureRule[]): FeatureLearning {
  return {
    rules,
    rulesLoading: false,
    pendingRules: 0,
    refreshRules: async () => undefined,
    extracting: false,
    extractRules: async () => undefined,
    extractNotice: null,
    reviewingRuleId: null,
    reviewRule: async () => undefined,
    submittingRuleId: null,
    submitRule: async () => undefined,
    submitNotice: null,
    cases: [],
    casesLoading: false,
    refreshCases: async () => undefined,
    finalized: null,
    finalizing: false,
    finalizeConflict: null,
    finalize: async () => undefined,
    resetRun: () => undefined,
    promotedTaskId: null,
    promoting: false,
    promote: async () => undefined,
  };
}

function renderPanel(rules: FeatureRule[]) {
  return render(
    <CurrentUserProvider user={ADMIN} setUser={() => undefined}>
      <RulesPanel learning={learning(rules)} />
    </CurrentUserProvider>,
  );
}

describe("<RulesPanel /> rule grouping", () => {
  it("shows a rule whose layer is one this build cannot name", () => {
    const { container } = renderPanel([
      rule({
        id: "p1",
        rule_text: "客户名写全称",
        scope: "personal",
        owner_user_id: ADMIN.id,
      }),
      rule({ id: "g1", rule_text: "对外邮件附署名" }),
      rule({ id: "t1", rule_text: "审批人写姓名", scope: "team" }),
    ]);

    const unfiled = screen.getByTestId("rules-unfiled-group");
    // The heading says what the panel cannot tell, and the count says how many.
    expect(
      within(unfiled).getByText("features.ruleScopeUnknownGroup"),
    ).toBeInTheDocument();
    expect(within(unfiled).getByText("1")).toBeInTheDocument();
    // The card is there, tagged as a layer nobody can name.
    expect(within(unfiled).getByText("审批人写姓名")).toBeInTheDocument();
    expect(
      within(unfiled).getByText("features.ruleScopeUnknown"),
    ).toBeInTheDocument();
    // The rules whose layer it does know keep their own headings.
    expect(screen.getByText("客户名写全称")).toBeInTheDocument();
    expect(screen.getByText("对外邮件附署名")).toBeInTheDocument();
    expect(container.querySelectorAll("article")).toHaveLength(3);
    expect(container.querySelectorAll("section")).toHaveLength(3);
  });

  it("shows a rule that arrives with no layer at all", () => {
    const { scope: _noLayer, ...withoutScope } = rule({
      id: "x1",
      rule_text: "没有层级的规则",
    });
    const { container } = renderPanel([
      rule({ id: "g1", rule_text: "对外邮件附署名" }),
      withoutScope,
    ]);

    expect(
      within(screen.getByTestId("rules-unfiled-group")).getByText(
        "没有层级的规则",
      ),
    ).toBeInTheDocument();
    expect(screen.getByText("对外邮件附署名")).toBeInTheDocument();
    expect(container.querySelectorAll("article")).toHaveLength(2);
  });

  it("keeps the empty state when the feature has no rules at all", () => {
    const { container } = renderPanel([]);

    expect(screen.getByText("features.rulesNone")).toBeInTheDocument();
    expect(screen.queryByTestId("rules-unfiled-group")).toBeNull();
    expect(container.querySelectorAll("article")).toHaveLength(0);
  });
});
