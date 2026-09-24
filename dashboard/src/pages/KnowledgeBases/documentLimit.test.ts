import { describe, expect, it } from "vitest";

import { effectiveDocumentLimit } from "./documentLimit";

describe("effectiveDocumentLimit", () => {
  it("uses the default when no per-base limit is set", () => {
    expect(effectiveDocumentLimit(null, 100)).toBe(100);
    expect(effectiveDocumentLimit(undefined, 100)).toBe(100);
  });

  it("treats zero as unlimited", () => {
    expect(effectiveDocumentLimit(0, 100)).toBeNull();
  });

  it("preserves a positive per-base limit", () => {
    expect(effectiveDocumentLimit(150, 100)).toBe(150);
  });
});
