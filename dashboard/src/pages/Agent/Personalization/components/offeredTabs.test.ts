import { describe, expect, it } from "vitest";
import {
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

  it("offers a caller memory but not author-owned configuration", () => {
    expect(offeredTabs("feature", FEATURE_PERSONALIZATION_TABS, false)).toEqual(
      ["memory"],
    );
  });

  it("does not offer a caller a capability the author writes", () => {
    for (const tab of FEATURE_PERSONALIZATION_TABS) {
      if (tab === "memory") continue;
      expect(offeredTabs("feature", [tab], false)).toEqual([]);
    }
  });
});
