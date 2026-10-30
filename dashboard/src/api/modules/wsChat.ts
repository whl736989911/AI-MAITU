import { getWsUrl } from "../config";
import { getAuthToken } from "../request";

export function buildDashboardChatWsUrl(agentId: string): string {
  const token = getAuthToken();
  const base = getWsUrl(`/agents/${encodeURIComponent(agentId)}/chat/ws`);
  const params = new URLSearchParams();
  if (token) params.set("token", token);
  return params.toString() ? `${base}?${params.toString()}` : base;
}
