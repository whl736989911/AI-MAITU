import { describe, expect, it } from "vitest";
import { ensureBwrapToastKind } from "./agentBackendForm";

describe("ensureBwrap helpers", () => {
  it("maps status to toast kind", () => {
    expect(ensureBwrapToastKind("ready")).toBe("none");
    expect(ensureBwrapToastKind("installed")).toBe("success");
    expect(ensureBwrapToastKind("skipped")).toBe("none");
    expect(ensureBwrapToastKind("degraded")).toBe("warning");
  });
});
