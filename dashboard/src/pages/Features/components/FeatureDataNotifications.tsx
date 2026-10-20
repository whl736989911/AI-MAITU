/**
 * The caller's in-app notifications, and the reminders behind them.
 *
 * A notification says a date is coming up. Marking it read is one action; the
 * reminder behind it is closed by "complete", which stops future thresholds from
 * firing. Both are per-caller, so this list is the caller's own view and never an
 * inbox for somebody else.
 */

import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Empty, Segmented, Spin, Tag } from "antd";
import { CheckOutlined } from "@ant-design/icons";
import { useTranslation } from "react-i18next";

import { message } from "@/utils/antdMessage";

import type { FeatureDataNotification } from "../../../api/modules/featureData";
import { featureDataApi } from "../../../api/modules/featureData";
import { apiErrorMessage } from "../../../utils/apiError";
import { formatServerDateTime } from "../../../utils/formatMessageTime";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import styles from "./FeatureDataPanel.module.less";

export interface FeatureDataNotificationsProps {
  agentId: string;
}

export default function FeatureDataNotifications({
  agentId,
}: FeatureDataNotificationsProps) {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [unreadOnly, setUnreadOnly] = useState(false);
  const [loading, setLoading] = useState(true);
  const [notifications, setNotifications] = useState<FeatureDataNotification[]>(
    [],
  );
  const [failure, setFailure] = useState<unknown>(null);
  const [busyId, setBusyId] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setFailure(null);
    try {
      setNotifications(await featureDataApi.notifications(agentId, unreadOnly));
    } catch (error) {
      setFailure(error);
    } finally {
      setLoading(false);
    }
  }, [agentId, unreadOnly]);

  useEffect(() => {
    void load();
  }, [load]);

  const handleRead = async (notification: FeatureDataNotification) => {
    setBusyId(notification.id);
    try {
      await featureDataApi.readNotification(agentId, notification.id);
      await load();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.markReadFailed"), t),
      );
    } finally {
      setBusyId(null);
    }
  };

  const handleComplete = async (notification: FeatureDataNotification) => {
    if (!notification.reminder_id) return;
    setBusyId(notification.id);
    try {
      await featureDataApi.completeReminder(agentId, notification.reminder_id);
      message.success(t("features.data.reminderCompleted"));
      await load();
    } catch (error) {
      message.error(
        apiErrorMessage(error, t("features.data.completeReminderFailed"), t),
      );
    } finally {
      setBusyId(null);
    }
  };

  return (
    <div className={styles.notifications}>
      <div className={styles.toolbar}>
        <Segmented
          value={unreadOnly ? "unread" : "all"}
          onChange={(value) => setUnreadOnly(value === "unread")}
          options={[
            { value: "all", label: t("features.data.all") },
            { value: "unread", label: t("features.data.unreadOnly") },
          ]}
        />
      </div>
      {loading ? (
        <div className={styles.loading}>
          <Spin />
        </div>
      ) : failure !== null ? (
        <Alert
          type="error"
          showIcon
          message={t("features.data.notificationsLoadFailed")}
          description={apiErrorMessage(
            failure,
            t("features.data.notificationsLoadFailed"),
            t,
          )}
        />
      ) : notifications.length === 0 ? (
        <Empty
          image={Empty.PRESENTED_IMAGE_SIMPLE}
          description={t("features.data.notificationsEmpty")}
        />
      ) : (
        <div className={styles.notificationList}>
          {notifications.map((notification) => (
            <div className={styles.notificationRow} key={notification.id}>
              <div className={styles.notificationMain}>
                <div className={styles.notificationHead}>
                  <span className={styles.notificationTitle}>
                    {notification.title}
                  </span>
                  {notification.read_at === null ? (
                    <Tag color="blue" bordered={false}>
                      {t("features.data.unread")}
                    </Tag>
                  ) : null}
                  <span className={styles.fileMeta}>
                    {formatServerDateTime(notification.created_at, timeZone)}
                  </span>
                </div>
                {notification.body?.trim() ? (
                  <p className={styles.notificationBody}>{notification.body}</p>
                ) : null}
              </div>
              <div className={styles.notificationActions}>
                {notification.read_at === null ? (
                  <Button
                    size="small"
                    icon={<CheckOutlined />}
                    loading={busyId === notification.id}
                    onClick={() => void handleRead(notification)}
                  >
                    {t("features.data.markRead")}
                  </Button>
                ) : null}
                {notification.reminder_id ? (
                  <Button
                    size="small"
                    type="text"
                    loading={busyId === notification.id}
                    onClick={() => void handleComplete(notification)}
                  >
                    {t("features.data.completeReminder")}
                  </Button>
                ) : null}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
