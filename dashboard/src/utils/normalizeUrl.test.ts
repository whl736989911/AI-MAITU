import { describe, expect, it } from "vitest";
import { normalizeUrl } from "./normalizeUrl";

describe("normalizeUrl", () => {
  it("preserves absolute HTTP URLs regardless of scheme case", () => {
    expect(normalizeUrl("HTTPS://example.com/path")).toBe(
      "HTTPS://example.com/path",
    );
  });

  it("normalizes protocol-relative URLs without losing their host", () => {
    expect(normalizeUrl("//example.com/path?q=1")).toBe(
      "https://example.com/path?q=1",
    );
  });
});
