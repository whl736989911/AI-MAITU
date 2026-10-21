import { request } from "../request";
import type { MemoryScope } from "./memoryDashboard";

export interface MemoryReviewConflict {
  id: string;
  fact: string;
  document: "USER.md" | "MEMORY.md";
  evidence: string;
  relation: "contradiction" | "duplicate";
}

export interface MemoryReviewResult {
  status: "reviewed" | "unavailable";
  reason?: string;
  message?: string;
  receipt?: string;
  scope: MemoryScope;
  manual_authoritative?: boolean;
  coverage: { facts_reviewed: number; facts_total: number; complete?: boolean };
  conflicts?: MemoryReviewConflict[];
}

const base = (agentId: string) => `/agents/${agentId}/memory/review`;
const memoryReviewApi = {
  review(agentId: string, scope: MemoryScope) {
    return request<MemoryReviewResult>(base(agentId), {
      method: "POST",
      body: JSON.stringify({ scope }),
    });
  },
  apply(
    agentId: string,
    scope: MemoryScope,
    receipt: string,
    factIds: string[],
  ) {
    return request<{
      status: "applied";
      action: string;
      deprecated_fact_ids: string[];
    }>(`${base(agentId)}/apply`, {
      method: "POST",
      body: JSON.stringify({ scope, receipt, fact_ids: factIds }),
    });
  },
};

export default memoryReviewApi;
