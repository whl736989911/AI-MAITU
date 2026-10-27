import { useCallback, useEffect, useRef, useState } from "react";
import {
  Alert,
  Badge,
  Button,
  Drawer,
  Empty,
  List,
  Spin,
  Typography,
} from "antd";
import { Bell } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  knowledgeAccessNotificationsApi,
  type KnowledgeAccessNotification,
} from "../api/modules/knowledgeAccessNotifications";
import { apiErrorMessage } from "../utils/apiError";
import { useServerTimezone } from "../hooks/useServerTimezone";

function notificationDate(value: number, timezone: string, locale: string) {
  const timestamp = value > 1_000_000_000_000 ? value : value * 1000;
  try {
    return new Intl.DateTimeFormat(locale, {
      dateStyle: "medium",
      timeStyle: "short",
      timeZone: timezone,
    }).format(new Date(timestamp));
  } catch {
    return new Date(timestamp).toLocaleString();
  }
}

export default function KnowledgeAccessNotifications() {
  const { t, i18n } = useTranslation();
  const timezone = useServerTimezone();
  const [open, setOpen] = useState(false);
  const [items, setItems] = useState<KnowledgeAccessNotification[]>([]);
  const [unreadCount, setUnreadCount] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [markingId, setMarkingId] = useState<string | null>(null);
  const requestId = useRef(0);

  const refresh = useCallback(async () => {
    if (typeof navigator !== "undefined" && !navigator.onLine) return;
    const id = ++requestId.current;
    setLoading(true);
    try {
      const response = await knowledgeAccessNotificationsApi.list(false, 50);
      if (requestId.current !== id) return;
      setItems(response.items);
      setUnreadCount(response.unread_count);
      setError(null);
    } catch (cause) {
      if (requestId.current === id) setError(cause);
    } finally {
      if (requestId.current === id) setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refresh();
    const interval = window.setInterval(() => void refresh(), 30_000);
    const onFocus = () => void refresh();
    const onOnline = () => void refresh();
    window.addEventListener("focus", onFocus);
    window.addEventListener("online", onOnline);
    return () => {
      requestId.current += 1;
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener("online", onOnline);
    };
  }, [refresh]);

  const markRead = async (item: KnowledgeAccessNotification) => {
    if (item.read_at !== null || markingId) return;
    setMarkingId(item.id);
    try {
      await knowledgeAccessNotificationsApi.markRead(item.id);
      setItems((current) =>
        current.map((entry) =>
          entry.id === item.id
            ? { ...entry, read_at: Math.floor(Date.now() / 1000) }
            : entry,
        ),
      );
      setUnreadCount((count) => Math.max(0, count - 1));
      setError(null);
    } catch (cause) {
      setError(cause);
    } finally {
      setMarkingId(null);
    }
  };

  return (
    <>
      <Button
        type="text"
        aria-label={t("knowledgeAccessNotifications.title")}
        onClick={() => {
          setOpen(true);
          void refresh();
        }}
        icon={
          <Badge count={unreadCount} overflowCount={99} size="small">
            <Bell size={19} />
          </Badge>
        }
        style={{
          position: "fixed",
          top: 68,
          right: 18,
          zIndex: 999,
          color: "var(--fn-text-primary)",
          background: "var(--fn-bg-container)",
          boxShadow: "var(--fn-shadow-sm)",
        }}
      />
      <Drawer
        title={t("knowledgeAccessNotifications.title")}
        open={open}
        onClose={() => setOpen(false)}
        width={480}
      >
        {error ? (
          <Alert
            type="error"
            showIcon
            message={apiErrorMessage(
              error,
              t("knowledgeAccessNotifications.loadFailed"),
              t,
            )}
            style={{ marginBottom: 16 }}
          />
        ) : null}
        {loading && !items.length ? (
          <div
            style={{ display: "flex", justifyContent: "center", padding: 24 }}
          >
            <Spin />
          </div>
        ) : items.length ? (
          <List
            dataSource={items}
            renderItem={(item) => (
              <List.Item
                key={item.id}
                actions={
                  item.read_at === null
                    ? [
                        <Button
                          key="read"
                          type="link"
                          loading={markingId === item.id}
                          disabled={Boolean(markingId)}
                          onClick={() => void markRead(item)}
                        >
                          {t("knowledgeAccessNotifications.markRead")}
                        </Button>,
                      ]
                    : undefined
                }
                style={{ opacity: item.read_at === null ? 1 : 0.72 }}
              >
                <List.Item.Meta
                  title={t(
                    `knowledgeAccessNotifications.events.${item.event_kind}`,
                  )}
                  description={
                    <>
                      <Typography.Paragraph style={{ marginBottom: 4 }}>
                        {t("knowledgeAccessNotifications.resource", {
                          path: item.resource_path,
                        })}
                      </Typography.Paragraph>
                      <Typography.Text type="secondary">
                        {item.access_retained
                          ? t("knowledgeAccessNotifications.accessRetained")
                          : t("knowledgeAccessNotifications.accessRemoved")}
                      </Typography.Text>
                      <br />
                      <Typography.Text type="secondary">
                        {notificationDate(
                          item.created_at,
                          timezone,
                          i18n.language,
                        )}
                      </Typography.Text>
                    </>
                  }
                />
              </List.Item>
            )}
          />
        ) : loading ? null : (
          <Empty description={t("knowledgeAccessNotifications.empty")} />
        )}
      </Drawer>
    </>
  );
}
