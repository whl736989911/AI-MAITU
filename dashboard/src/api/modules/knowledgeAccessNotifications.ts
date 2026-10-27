import { request } from "../request";

export type KnowledgeAccessNotificationKind =
  | "authorization_revoked"
  | "delegation_revoked";

export interface KnowledgeAccessNotification {
  id: string;
  kb_id: string;
  resource_id: string;
  resource_path: string;
  event_kind: KnowledgeAccessNotificationKind;
  access_retained: boolean;
  read_at: number | null;
  created_at: number;
}

export interface KnowledgeAccessNotificationsResponse {
  items: KnowledgeAccessNotification[];
  unread_count: number;
}

export const knowledgeAccessNotificationsApi = {
  list: (unreadOnly = false, limit = 50) =>
    request<KnowledgeAccessNotificationsResponse>(
      `/knowledge-access-notifications?unread_only=${unreadOnly}&limit=${limit}`,
    ),
  markRead: (id: string) =>
    request<void>(
      `/knowledge-access-notifications/${encodeURIComponent(id)}/read`,
      { method: "POST" },
    ),
};
