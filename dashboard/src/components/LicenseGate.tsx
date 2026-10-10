import { useCallback, useEffect, useState } from "react";
import { Button, Card, Input, Space, Tag, Typography, message } from "antd";
import { useTranslation } from "react-i18next";
import { ShieldAlert, RefreshCw, KeyRound } from "lucide-react";
import { authApi } from "../api/modules/auth";
import {
  licenseApi,
  type LicenseReason,
  type LicenseStatus,
} from "../api/modules/license";
import { getAuthToken, INSTANCE_LICENSE_LOCKED_EVENT } from "../api/request";
import { formatServerDateTime } from "../utils/formatMessageTime";
import styles from "./LicenseGate.module.less";

const REASON_KEYS: Record<LicenseReason, string> = {
  unactivated: "license.reason.unactivated",
  expired: "license.reason.expired",
  revoked: "license.reason.revoked",
  customer_disabled: "license.reason.customerDisabled",
  cache_expired: "license.reason.cacheExpired",
  invalid_credential: "license.reason.invalidCredential",
  clock_anomaly: "license.reason.clockAnomaly",
  configuration_required: "license.reason.configurationRequired",
  network_error: "license.reason.networkError",
};

function LicenseLockedPage({
  status,
  onStatus,
}: {
  status: LicenseStatus;
  onStatus: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const timeZone = status.timezone || "UTC";
  const [userRole, setUserRole] = useState<string | null>(null);
  const [checkingUser, setCheckingUser] = useState(false);
  const [activationCode, setActivationCode] = useState("");
  const [working, setWorking] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const reasonKey = status.reason ? REASON_KEYS[status.reason] : null;

  useEffect(() => {
    if (!getAuthToken()) {
      setUserRole(null);
      return;
    }
    let live = true;
    setCheckingUser(true);
    void authApi
      .me()
      .then(
        (user) => {
          if (live) setUserRole(user.role);
        },
        () => {
          if (live) setUserRole(null);
        },
      )
      .finally(() => {
        if (live) setCheckingUser(false);
      });
    return () => {
      live = false;
    };
  }, []);

  const canManage = userRole === "admin" || userRole === "enterprise_admin";
  const activate = async () => {
    if (!activationCode.trim()) return;
    setWorking(true);
    try {
      await licenseApi.activate(activationCode.trim());
      setActivationCode("");
      await onStatus();
    } catch {
      message.error(t("license.activationFailed"));
    } finally {
      setWorking(false);
    }
  };
  const refresh = async () => {
    setRefreshing(true);
    try {
      await licenseApi.refresh();
      await onStatus();
    } catch {
      message.error(t("license.refreshFailed"));
      await onStatus();
    } finally {
      setRefreshing(false);
    }
  };

  const expiry = status.license_expires_at
    ? formatServerDateTime(status.license_expires_at, timeZone)
    : null;

  return (
    <main className={styles.page}>
      <Card className={styles.card} bordered={false}>
        <div className={styles.mark}>
          <ShieldAlert size={30} />
        </div>
        <Tag color="error" className={styles.stateTag}>
          {t("license.locked")}
        </Tag>
        <Typography.Title level={2} className={styles.title}>
          {t("license.title")}
        </Typography.Title>
        <Typography.Paragraph className={styles.description}>
          {reasonKey ? t(reasonKey) : t("license.defaultReason")}
        </Typography.Paragraph>
        <div className={styles.details}>
          <div className={styles.detailRow}>
            <span>{t("license.expires")}</span>
            <strong>{expiry ?? t("license.notAvailable")}</strong>
          </div>
          <div className={styles.detailRow}>
            <span>{t("license.displayTimezone")}</span>
            <strong>{timeZone}</strong>
          </div>
          {status.contact && (
            <div className={styles.contact}>
              <span>{t("license.contact")}</span>
              <Typography.Text copyable={{ text: status.contact }}>
                {status.contact}
              </Typography.Text>
            </div>
          )}
        </div>

        {canManage ? (
          <div className={styles.actions}>
            <Typography.Text strong>{t("license.adminTools")}</Typography.Text>
            {status.reason === "unactivated" && (
              <Space.Compact className={styles.activation}>
                <Input
                  value={activationCode}
                  onChange={(event) => setActivationCode(event.target.value)}
                  onPressEnter={() => void activate()}
                  placeholder={t("license.activationCode")}
                  aria-label={t("license.activationCode")}
                  autoComplete="off"
                />
                <Button
                  type="primary"
                  icon={<KeyRound size={15} />}
                  loading={working}
                  disabled={!activationCode.trim()}
                  onClick={() => void activate()}
                >
                  {t("license.activate")}
                </Button>
              </Space.Compact>
            )}
            <Button
              icon={<RefreshCw size={15} />}
              loading={refreshing}
              onClick={() => void refresh()}
            >
              {t("license.checkAgain")}
            </Button>
          </div>
        ) : (
          <div className={styles.actions}>
            {checkingUser ? (
              <Typography.Text type="secondary">
                {t("license.checkingPermissions")}
              </Typography.Text>
            ) : getAuthToken() ? (
              <Typography.Text type="secondary">
                {t("license.adminRequired")}
              </Typography.Text>
            ) : (
              <Button type="primary" href="/login">
                {t("license.signIn")}
              </Button>
            )}
          </div>
        )}
        <div className={styles.footer}>
          <Typography.Text type="secondary">
            {t("license.accessRestoredByServer")}
          </Typography.Text>
        </div>
      </Card>
    </main>
  );
}

export default function LicenseGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<LicenseStatus | null>(null);
  const [failed, setFailed] = useState(false);

  const loadStatus = useCallback(async () => {
    try {
      const next = await licenseApi.status();
      setStatus(next);
      setFailed(false);
    } catch {
      setFailed(true);
    }
  }, []);

  useEffect(() => {
    void loadStatus();
    const onLocked = () => {
      setStatus((current) =>
        current ? { ...current, state: "LOCKED", allowed: false } : current,
      );
      void loadStatus();
    };
    window.addEventListener(INSTANCE_LICENSE_LOCKED_EVENT, onLocked);
    return () =>
      window.removeEventListener(INSTANCE_LICENSE_LOCKED_EVENT, onLocked);
  }, [loadStatus]);

  useEffect(() => {
    if (!status) return;
    const timer = window.setInterval(
      () => void loadStatus(),
      status.allowed ? 30_000 : 5_000,
    );
    return () => window.clearInterval(timer);
  }, [loadStatus, status?.allowed]);

  if (status?.allowed) return children;
  if (status)
    return <LicenseLockedPage status={status} onStatus={loadStatus} />;
  return (
    <main className={styles.page}>
      <Card className={styles.card} bordered={false}>
        <div className={styles.mark}>
          <ShieldAlert size={30} />
        </div>
        <Typography.Title level={3}>
          {t(failed ? "license.unavailable" : "license.loading")}
        </Typography.Title>
        <Typography.Paragraph type="secondary">
          {t(
            failed
              ? "license.unavailableDescription"
              : "license.loadingDescription",
          )}
        </Typography.Paragraph>
        <Button
          icon={<RefreshCw size={15} />}
          onClick={() => void loadStatus()}
        >
          {t("license.retry")}
        </Button>
      </Card>
    </main>
  );
}
