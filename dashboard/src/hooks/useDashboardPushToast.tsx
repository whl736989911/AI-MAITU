import { App } from "antd";
import { Bell } from "lucide-react";
import { useEffect, useRef } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router-dom";
import { buildDashboardNotifyWsUrl } from "../api/modules/wsNotifications";
import { AUTH_IDENTITY_CHANGED_EVENT, getAuthToken } from "../api/request";
import { useAgent } from "../context/AgentContext";
import { ExpertIcon } from "../pages/Experts/components/iconForName";
import {
  emitSessionEvent,
  invalidateHistory,
} from "../pages/Chat/hooks/chatStore";
import {
  claimSystemPush,
  clearSystemPushClaims,
  parseDashboardPushFrame,
  pushDedupeKey,
  readSystemNotifyPermission,
  shouldShowSystemPush,
  systemNotificationTag,
  truncatePushText,
  type DashboardPushEvent,
} from "../utils/dashboardPushToast";
import { peekSystemNotifyRegistration } from "../sw-register";
import { publicUrl } from "../utils/publicUrl";
import styles from "./useDashboardPushToast.module.less";

const PING_INTERVAL_MS = 25_000;
const MAX_RETRY_MS = 15_000;

function PushToastIcon({
  iconUrl,
  iconName,
}: {
  iconUrl?: string | null;
  iconName?: string | null;
}) {
  return (
    <span className={styles.icon}>
      {iconUrl || iconName ? (
        <ExpertIcon iconUrl={iconUrl} iconName={iconName} size={16} />
      ) : (
        <Bell size={16} />
      )}
    </span>
  );
}

/** Listen for dashboard text pushes and show a bottom-right toast. */
export function useDashboardPushToast(): void {
  const { notification } = App.useApp();
  const navigate = useNavigate();
  const { t } = useTranslation();
  const { refresh, agents } = useAgent();
  const agentsRef = useRef(agents);
  agentsRef.current = agents;
  const notificationRef = useRef(notification);
  notificationRef.current = notification;
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const tRef = useRef(t);
  tRef.current = t;
  const refreshRef = useRef(refresh);
  refreshRef.current = refresh;

  useEffect(() => {
    const tabId = Math.random().toString(36).slice(2);
    let ws: WebSocket | null = null;
    let identityGeneration = 0;
    const pushNoticeKeys = new Set<string>();
    const browserNotifications = new Set<Notification>();

    const onNotifyOpen = (event: MessageEvent) => {
      const data = event.data as { type?: string; path?: string } | null;
      if (data?.type !== "octop-notify-open" || !data.path?.startsWith("/"))
        return;
      window.focus();
      navigateRef.current(data.path);
    };
    navigator.serviceWorker?.addEventListener("message", onNotifyOpen);
    let closed = false;
    let retry = 0;
    const storage = (): Storage | null => {
      try {
        const value = window.localStorage;
        value.setItem("octop:push-probe", "1");
        value.removeItem("octop:push-probe");
        return value;
      } catch {
        return null;
      }
    };

    const closePushNotifications = () => {
      for (const key of pushNoticeKeys) notificationRef.current.destroy(key);
      pushNoticeKeys.clear();
      for (const notice of browserNotifications) notice.close();
      browserNotifications.clear();
      const closeWorkerNotifications = (
        registration: ServiceWorkerRegistration,
      ) => {
        void registration
          .getNotifications()
          .then((notices) =>
            notices
              .filter((notice) => {
                const data = notice.data as {
                  pushOwner?: unknown;
                  pushGeneration?: unknown;
                } | null;
                return (
                  notice.tag.startsWith("dash-push:") &&
                  data?.pushOwner === tabId &&
                  typeof data.pushGeneration === "number" &&
                  data.pushGeneration < identityGeneration
                );
              })
              .forEach((notice) => notice.close()),
          )
          .catch(() => undefined);
      };
      const registration = peekSystemNotifyRegistration();
      if (registration) {
        closeWorkerNotifications(registration);
      } else if ("serviceWorker" in navigator) {
        void navigator.serviceWorker.ready
          .then(closeWorkerNotifications)
          .catch(() => undefined);
      }
    };

    const showSystemNotification = (
      parsed: DashboardPushEvent,
      title: string,
      generation: number,
    ) => {
      const path = `/chat/${parsed.agent_id}/${parsed.thread_id}`;
      const options: NotificationOptions & { renotify?: boolean } = {
        body: truncatePushText(parsed.text),
        tag: systemNotificationTag(parsed.thread_id),
        icon: new URL(publicUrl("brand/pwa-192.png"), window.location.href)
          .href,
        renotify: true,
        data: { path, pushOwner: tabId, pushGeneration: generation },
      };
      const showFallback = () => {
        if (
          generation !== identityGeneration ||
          typeof Notification === "undefined"
        ) {
          return;
        }
        const notice = new Notification(title, options);
        browserNotifications.add(notice);
        notice.onclose = () => browserNotifications.delete(notice);
        notice.onclick = () => {
          browserNotifications.delete(notice);
          window.focus();
          notice.close();
          navigateRef.current(path);
        };
      };
      const showWithWorker = (registration: ServiceWorkerRegistration) => {
        if (generation !== identityGeneration) return;
        void registration
          .showNotification(title, options)
          .then(() => {
            if (generation !== identityGeneration) {
              return registration
                .getNotifications({ tag: options.tag })
                .then((notices) =>
                  notices
                    .filter((notice) => {
                      const data = notice.data as {
                        pushOwner?: unknown;
                        pushGeneration?: unknown;
                      } | null;
                      return (
                        data?.pushOwner === tabId &&
                        data.pushGeneration === generation
                      );
                    })
                    .forEach((notice) => notice.close()),
                );
            }
          })
          .catch(showFallback);
      };

      if (generation !== identityGeneration) return;
      const registration = peekSystemNotifyRegistration();
      if (registration) {
        showWithWorker(registration);
      } else if ("serviceWorker" in navigator) {
        void navigator.serviceWorker.ready
          .then(showWithWorker)
          .catch(showFallback);
      } else {
        showFallback();
      }
    };

    let reconnectTimer: number | null = null;

    const clearReconnect = () => {
      if (reconnectTimer !== null) {
        window.clearTimeout(reconnectTimer);
        reconnectTimer = null;
      }
    };

    const scheduleReconnect = () => {
      if (closed) return;
      clearReconnect();
      const delay = Math.min(1000 * 2 ** retry, MAX_RETRY_MS);
      retry += 1;
      reconnectTimer = window.setTimeout(connect, delay);
    };

    const connect = () => {
      if (closed) return;
      const token = getAuthToken();
      if (!token) {
        scheduleReconnect();
        return;
      }
      const socketGeneration = identityGeneration;
      let socket: WebSocket;
      try {
        socket = new WebSocket(buildDashboardNotifyWsUrl());
      } catch {
        scheduleReconnect();
        return;
      }
      if (ws && ws !== socket) {
        const prev = ws;
        ws = null;
        try {
          prev.close();
        } catch {
          // ignore
        }
      }
      ws = socket;
      socket.onopen = () => {
        if (ws !== socket || socketGeneration !== identityGeneration) return;
        retry = 0;
      };
      socket.onmessage = (event) => {
        if (ws !== socket || socketGeneration !== identityGeneration) return;
        let raw: unknown;
        try {
          raw = JSON.parse(String(event.data));
        } catch {
          return;
        }
        if (
          raw &&
          typeof raw === "object" &&
          (raw as { type?: string }).type === "pong"
        ) {
          return;
        }
        const parsed = parseDashboardPushFrame(raw);
        if (!parsed) return;
        void refreshRef.current({ silent: true });
        // A proactive run wrote to this thread outside the chat socket. Stale it
        // here (not in the chat page listener) so the cached page is dropped
        // even when the push lands while another route is open.
        invalidateHistory(parsed.thread_id);
        // Refresh the sidebar and the open thread's history without a reload.
        emitSessionEvent({
          kind: "sessionsChanged",
          sessionId: parsed.thread_id,
          agentId: parsed.agent_id,
        });
        const agent = agentsRef.current.find(
          (a) => a.agent_id === parsed.agent_id,
        );
        const accent = agent?.color?.trim() || "#6366f1";
        const title = parsed.agent_name
          ? tRef.current("chat.pushToast.title", { name: parsed.agent_name })
          : tRef.current("chat.pushToast.titleFallback");
        const noticeKey = `dash-push-${parsed.thread_id}-${Date.now()}`;
        pushNoticeKeys.add(noticeKey);
        notificationRef.current.open({
          key: noticeKey,
          placement: "bottomRight",
          className: styles.notice,
          style: {
            ["--push-accent" as string]: accent,
          },
          message: (
            <span className={styles.header}>
              <PushToastIcon
                iconUrl={agent?.icon_url}
                iconName={agent?.icon_name}
              />
              <span className={styles.title}>{title}</span>
            </span>
          ),
          description: (
            <span className={styles.body}>{truncatePushText(parsed.text)}</span>
          ),
          duration: null,
          closable: true,
          onClick: () => {
            pushNoticeKeys.delete(noticeKey);
            notificationRef.current.destroy(noticeKey);
            navigateRef.current(`/chat/${parsed.agent_id}/${parsed.thread_id}`);
          },
          onClose: () => pushNoticeKeys.delete(noticeKey),
        });
        const permission = readSystemNotifyPermission(
          window.isSecureContext,
          typeof Notification === "undefined" ? undefined : Notification,
        );
        const pushKey = pushDedupeKey(parsed);
        if (
          shouldShowSystemPush(
            permission,
            document.visibilityState === "visible",
          ) &&
          claimSystemPush(storage(), pushKey, tabId)
        ) {
          showSystemNotification(parsed, title, socketGeneration);
        }
      };
      socket.onclose = () => {
        if (ws !== socket) return;
        ws = null;
        if (!closed) scheduleReconnect();
      };
    };

    const onAuthIdentityChanged = () => {
      identityGeneration += 1;
      retry = 0;
      clearReconnect();
      const previous = ws;
      ws = null;
      if (previous) {
        try {
          previous.close();
        } catch {
          // ignore
        }
      }
      closePushNotifications();
      clearSystemPushClaims(storage());
      connect();
    };
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, onAuthIdentityChanged);
    connect();
    const pingTimer = window.setInterval(() => {
      if (ws && ws.readyState === WebSocket.OPEN) {
        try {
          ws.send(JSON.stringify({ type: "ping" }));
        } catch {
          // ignore
        }
      }
    }, PING_INTERVAL_MS);

    return () => {
      closed = true;
      identityGeneration += 1;
      window.removeEventListener(
        AUTH_IDENTITY_CHANGED_EVENT,
        onAuthIdentityChanged,
      );
      closePushNotifications();
      navigator.serviceWorker?.removeEventListener("message", onNotifyOpen);
      clearReconnect();
      window.clearInterval(pingTimer);
      const previous = ws;
      ws = null;
      if (previous) {
        try {
          previous.close();
        } catch {
          // ignore
        }
      }
    };
  }, []);
}
