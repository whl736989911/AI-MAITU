import { describe, expect, it } from "vitest";
import {
  CAPABILITY_POLICY,
  FEATURE_PERSONALIZATION_TABS,
  PERSONALIZATION_TABS,
  offeredTabs,
} from "./PersonalizationPanels";

describe("offeredTabs", () => {
  it("offers an expert's owner every tab it has", () => {
    // The expert scope has no policy of its own: the viewer writes everything,
    // which is what the page has always shown.
    expect(offeredTabs("expert", PERSONALIZATION_TABS, true)).toEqual([
      ...PERSONALIZATION_TABS,
    ]);
  });

  it("keeps every tab for the author of a feature", () => {
    expect(offeredTabs("feature", FEATURE_PERSONALIZATION_TABS, true)).toEqual([
      ...FEATURE_PERSONALIZATION_TABS,
    ]);
  });

  it("offers a caller only what nobody has to write", () => {
    // A feature is configured by whoever defines it. For a caller the writing
    // tabs are not offered at all — a control whose only outcome is a refusal is
    // a dead end, not a choice — and what is left is its memory, which no one
    // writes and which therefore stays readable.
    expect(offeredTabs("feature", FEATURE_PERSONALIZATION_TABS, false)).toEqual(
      ["memory"],
    );
    expect(CAPABILITY_POLICY.feature.memory?.writer).toBe("nobody");
  });

  it("does not offer a caller a capability the author writes", () => {
    for (const tab of FEATURE_PERSONALIZATION_TABS) {
      if (tab === "memory") continue;
      expect(offeredTabs("feature", [tab], false)).toEqual([]);
    }
  });
});
