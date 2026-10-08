import { describe, expect, it } from "vitest";
import { parseHarnessChunk } from "./parseHarnessChunk";

describe("parseHarnessChunk usage", () => {
  it("keeps the stable call id and cache-aware usage fields", () => {
    const chunk = parseHarnessChunk(
      'data: {"type":"usage","call_id":"call-1","model":"deepseek-v4","usage":{"input_tokens":100,"uncached_input_tokens":30,"cache_read_tokens":70,"cache_write_tokens":0,"output_tokens":9,"reasoning_tokens":4,"total_tokens":109}}',
    );

    expect(chunk).toEqual({
      type: "usage",
      call_id: "call-1",
      model: "deepseek-v4",
      node: undefined,
      usage: {
        input_tokens: 100,
        uncached_input_tokens: 30,
        cache_read_tokens: 70,
        cache_write_tokens: 0,
        output_tokens: 9,
        reasoning_tokens: 4,
        total_tokens: 109,
      },
    });
  });
});

describe("parseHarnessChunk LangGraph v2 HITL interrupts", () => {
  it("unwraps a question request from the Interrupt value envelope", () => {
    const chunk = parseHarnessChunk(
      'data: {"type":"hitl_required","request":{"id":"interrupt-1","value":{"action_requests":[{"name":"ask_user_question","args":"{\\"questions\\":[{\\"question\\":\\"Which option?\\"}]}"}],"review_configs":[]}}}',
    );

    expect(chunk).toEqual({
      type: "hitl_required",
      request: {
        action_requests: [
          {
            name: "ask_user_question",
            args: { questions: [{ question: "Which option?" }] },
          },
        ],
        review_configs: [],
        pending_id: undefined,
      },
    });
  });
});
