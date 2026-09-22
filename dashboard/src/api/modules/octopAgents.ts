import { request, requestUpload } from "../request";

/** Runtime state of one agent, as ``GET /agents/{id}/status`` answers. */
export interface AgentRuntimeStatus {
  /** ``running`` / ``stopped`` / ``failed`` / ``created`` / ``unknown``. */
  state: string;
  /** Why it is not running, when the last start or run failed. */
  last_error: string | null;
}

export const octopAgentsApi = {
  markRead: (agentId: string) =>
    request<void>(`/agents/${encodeURIComponent(agentId)}/read`, {
      method: "POST",
    }),

  /**
   * The row's own record of whether the harness holds this agent.
   *
   * Panels that only work against a running agent (the subagent manager disables
   * every install otherwise) need the state as the server records it, not as a
   * caller assumes it. Owner-gated, which an administrator passes for an
   * app-owned agent.
   */
  getAgentStatus: (agentId: string) =>
    request<AgentRuntimeStatus>(
      `/agents/${encodeURIComponent(agentId)}/status`,
    ),

  uploadAvatar: (agentId: string, file: File) => {
    const body = new FormData();
    body.append("file", file);
    return requestUpload<{ icon_url: string }>(
      `/agents/${encodeURIComponent(agentId)}/avatar`,
      body,
    );
  },

  deleteAvatar: (agentId: string) =>
    request<void>(`/agents/${encodeURIComponent(agentId)}/avatar`, {
      method: "DELETE",
    }),
};
