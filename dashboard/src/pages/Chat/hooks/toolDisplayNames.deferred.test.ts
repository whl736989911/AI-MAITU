import { describe, expect, it } from "vitest";

import {
  deferredCallTargetName,
  resolveDeferredToolLabel,
} from "./toolDisplayNames";

const translate = (name?: string) => `label:${name ?? ""}`;

describe("deferredCallTargetName", () => {
  it("reads the target from structured arguments", () => {
    expect(deferredCallTargetName({ name: " read_file " })).toBe("read_file");
  });

  it("reads the target from a partially streamed JSON string", () => {
    expect(deferredCallTargetName('{"name": "write_file", "args": {')).toBe(
      "write_file",
    );
  });

  it("ignores missing or malformed arguments", () => {
    expect(deferredCallTargetName(undefined)).toBeNull();
    expect(deferredCallTargetName({})).toBeNull();
    expect(deferredCallTargetName("")).toBeNull();
  });
});

describe("resolveDeferredToolLabel", () => {
  it("labels the forwarded tool instead of the wrapper", () => {
    expect(
      resolveDeferredToolLabel("call_deferred_tool", { name: "read_file" }, translate),
    ).toBe("label:read_file");
  });

  it("keeps the wrapper label until the target is known", () => {
    expect(resolveDeferredToolLabel("call_deferred_tool", {}, translate)).toBeNull();
    expect(resolveDeferredToolLabel("call_deferred_tool", "{}", translate)).toBeNull();
  });

  it("does not rewrite ordinary tools", () => {
    expect(resolveDeferredToolLabel("read_file", { name: "write_file" }, translate)).toBeNull();
  });

  it("ignores a nested deferred call", () => {
    expect(
      resolveDeferredToolLabel(
        "call_deferred_tool",
        { name: "call_deferred_tool" },
        translate,
      ),
    ).toBeNull();
  });
});
