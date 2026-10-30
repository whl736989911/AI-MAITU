import { request } from "../request";
import type { LocalizedText } from "../../utils/localizedText";

export interface ChatWelcomeResponse {
  welcome_message?: LocalizedText;
  quick_prompts?: Array<{
    title: LocalizedText;
    description: LocalizedText;
    prompt: LocalizedText;
    color?: string;
    icon_name?: string | null;
  }>;
  task_examples?: { zh?: string[]; en?: string[] } | null;
  hitl_enabled?: boolean;
}

export const agentChatApi = {
  welcome: (agentId: string) =>
    request<ChatWelcomeResponse>(
      `/agents/${encodeURIComponent(agentId)}/chat/welcome`,
    ),
};
