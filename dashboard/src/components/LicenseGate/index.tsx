import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Card, Input, Space, Spin, Typography } from "antd";
import { useTranslation } from "react-i18next";
import { useCurrentUser } from "../../hooks/useCurrentUser";
import { useServerTimezone } from "../../hooks/useServerTimezone";
import {
  licensingApi,
  type LicenseReminder,
  type LicenseSnapshot,
  type OfflineLicenseRequest,
} from "../../api/modules/licensing";
import { LICENSE_LOCKED_EVENT } from "../../api/request";
import { apiErrorMessage } from "../../utils/apiError";
import styles from "./index.module.less";

const { TextArea } = Input;
const STATUS_POLL_MS = 30_000;

function toToken(value: string): string {
  const trimmed = value.trim();
  try {
    const parsed: unknown = JSON.parse(trimmed);
    if (typeof parsed === "string") return parsed.trim();
    if (parsed && typeof parsed === "object") {
      const token = (parsed as Record<string, unknown>).token;
      if (typeof token === "string") return token.trim();
    }
  } catch {
    // A raw signed token is the expected import format.
  }
  return trimmed;
}

function downloadOfflineRequest(request: OfflineLicenseRequest): void {
  const blob = new Blob([JSON.stringify(request, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const link = document.createElement("a");
  link.href = url;
  link.download = "maitu-offline-request.json";
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 0);
}

export default function LicenseGate({
  children,
}: {
  children: React.ReactNode;
}) {
  const { t, i18n } = useTranslation();
  const user = useCurrentUser();
  const timezone = useServerTimezone();
  const isAdmin = user?.role === "admin" || user?.role === "enterprise_admin";
  const [snapshot, setSnapshot] = useState<LicenseSnapshot | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [offlineToken, setOfflineToken] = useState("");
  const [offlineRequest, setOfflineRequest] =
    useState<OfflineLicenseRequest | null>(null);
  const [busy, setBusy] = useState(false);
  const [lockedSignal, setLockedSignal] = useState(false);
  const [reminder, setReminder] = useState<LicenseReminder | null>(null);

  const refreshSnapshot = useCallback(async () => {
    try {
      const publicStatus = await licensingApi.getStatus();
      const next =
        publicStatus.managed && isAdmin
          ? await licensingApi.getDetails()
          : publicStatus;
      setSnapshot(next);
      setLoadError(null);
      setLoading(false);
      return next;
    } catch (error) {
      setLoadError(apiErrorMessage(error, t("license.statusFailed"), t));
      setLoading(false);
      return null;
    }
  }, [isAdmin, t]);

  const refreshReminder = useCallback(async () => {
    try {
      const next = await licensingApi.claimReminder();
      setReminder(next.show ? next : null);
    } catch {
      // Reminder delivery is best-effort; enforcement is server-side.
    }
  }, []);

  useEffect(() => {
    void refreshSnapshot();
    const interval = window.setInterval(
      () => void refreshSnapshot(),
      STATUS_POLL_MS,
    );
    const onFocus = () => {
      void refreshSnapshot();
      void refreshReminder();
    };
    const onLicenseLocked = () => {
      setLockedSignal(true);
      setSnapshot((current) =>
        current
          ? { ...current, state: "LOCKED", business_allowed: false }
          : current,
      );
      void refreshSnapshot();
    };
    window.addEventListener("focus", onFocus);
    window.addEventListener(LICENSE_LOCKED_EVENT, onLicenseLocked);
    return () => {
      window.clearInterval(interval);
      window.removeEventListener("focus", onFocus);
      window.removeEventListener(LICENSE_LOCKED_EVENT, onLicenseLocked);
    };
  }, [refreshSnapshot, refreshReminder]);

  useEffect(() => {
    if (!snapshot?.managed || !snapshot.business_allowed) return;
    void refreshReminder();
    const interval = window.setInterval(
      () => void refreshReminder(),
      60 * 60 * 1000,
    );
    return () => window.clearInterval(interval);
  }, [refreshReminder, snapshot?.managed, snapshot?.business_allowed]);

  const runAction = async (
    action: () => Promise<LicenseSnapshot>,
  ): Promise<void> => {
    setBusy(true);
    setActionError(null);
    try {
      const next = await action();
      setSnapshot(next);
      if (next.business_allowed) setLockedSignal(false);
      setCode("");
      setOfflineToken("");
      setOfflineRequest(null);
    } catch (error) {
      setActionError(apiErrorMessage(error, t("license.actionFailed"), t));
      void refreshSnapshot();
    } finally {
      setBusy(false);
    }
  };

  const exportOfflineRequest = async () => {
    setBusy(true);
    setActionError(null);
    try {
      const request = await licensingApi.getOfflineRequest();
      setOfflineRequest(request);
      downloadOfflineRequest(request);
    } catch (error) {
      setActionError(
        apiErrorMessage(error, t("license.requestExportFailed"), t),
      );
    } finally {
      setBusy(false);
    }
  };

  const importFile = async (file?: File) => {
    if (!file) return;
    try {
      setOfflineToken(toToken(await file.text()));
      setActionError(null);
    } catch (error) {
      setActionError(apiErrorMessage(error, t("license.fileReadFailed"), t));
    }
  };

  const expiration = snapshot?.license_expires_at;
  const formatDate = (seconds: number) =>
    new Intl.DateTimeFormat(i18n.language, {
      dateStyle: "long",
      timeStyle: "short",
      timeZone: timezone,
    }).format(new Date(seconds * 1000));

  const formatRemaining = (seconds: number | null | undefined) => {
    const remaining = Math.max(0, seconds ?? 0);
    const days = Math.floor(remaining / 86400);
    const hours = Math.floor((remaining % 86400) / 3600);
    const minutes = Math.floor((remaining % 3600) / 60);
    return t("license.remainingTime", { days, hours, minutes });
  };

  if (loading && !snapshot) {
    return (
      <div className={styles.loading}>
        <Spin size="large" />
      </div>
    );
  }

  if (!snapshot) {
    return (
      <div className={styles.gate}>
        <Card className={styles.card}>
          <Alert
            type="error"
            showIcon
            message={t("license.statusUnavailable")}
            description={loadError}
          />
          <Button type="primary" onClick={() => void refreshSnapshot()}>
            {t("license.retry")}
          </Button>
        </Card>
      </div>
    );
  }

  if (!snapshot.managed) return <>{children}</>;

  if (snapshot.business_allowed && !lockedSignal) {
    // The healthy state is not a pinned banner. Surface the bar only when the
    // user needs to act (expiry warning or a sync failure); full details live
    // in Settings → License center.
    const licenseDetails = (
      <div className={styles.statusDetails}>
        <div>
          <strong>{t("license.instanceId")}:</strong>{" "}
          <code>{snapshot.instance_id}</code>
        </div>
        {snapshot.license_id && (
          <div>
            <strong>{t("license.licenseId")}:</strong>{" "}
            <code>{snapshot.license_id}</code>
          </div>
        )}
        {snapshot.license_version != null && (
          <div>
            <strong>{t("license.version")}:</strong> {snapshot.license_version}
          </div>
        )}
        {expiration != null && (
          <div>
            <strong>{t("license.expiresAt")}:</strong> {formatDate(expiration)}
          </div>
        )}
        {snapshot.credential_type && (
          <div>
            <strong>{t("license.credentialType")}:</strong>{" "}
            {t(`license.credential.${snapshot.credential_type}`)}
          </div>
        )}
        {snapshot.last_sync_at != null && (
          <div>
            <strong>{t("license.lastSync")}:</strong>{" "}
            {formatDate(snapshot.last_sync_at)}
          </div>
        )}
        {snapshot.sync_error && (
          <div className={styles.error}>{snapshot.sync_error}</div>
        )}
        {isAdmin && (
          <Space wrap>
            <Button
              size="small"
              onClick={() => void runAction(() => licensingApi.refresh())}
              loading={busy}
            >
              {t("license.syncOnline")}
            </Button>
            <Button
              size="small"
              onClick={() => void exportOfflineRequest()}
              loading={busy}
            >
              {t("license.exportOfflineRequest")}
            </Button>
          </Space>
        )}
        {isAdmin && (
          <div className={styles.offlineControls}>
            {offlineRequest && (
              <p className={styles.helpText}>
                {t("license.exportedRequest", {
                  fingerprint: offlineRequest.key_fingerprint,
                })}
              </p>
            )}
            <label className={styles.fieldLabel} htmlFor="license-offline-file">
              {t("license.chooseOfflineFile")}
            </label>
            <input
              id="license-offline-file"
              type="file"
              accept=".json,.txt,application/json,text/plain"
              disabled={busy}
              onChange={(event) =>
                void importFile(event.currentTarget.files?.[0])
              }
            />
            <label
              className={styles.fieldLabel}
              htmlFor="license-offline-import"
            >
              {t("license.offlineImport")}
            </label>
            <TextArea
              id="license-offline-import"
              autoSize={{ minRows: 2, maxRows: 6 }}
              value={offlineToken}
              onChange={(event) => setOfflineToken(event.target.value)}
              placeholder={t("license.offlineImportPlaceholder")}
              disabled={busy}
            />
            <Button
              onClick={() =>
                void runAction(() =>
                  licensingApi.importOffline(toToken(offlineToken)),
                )
              }
              disabled={!offlineToken.trim() || busy}
              loading={busy}
            >
              {t("license.importOffline")}
            </Button>
          </div>
        )}
        {actionError && <Alert type="error" showIcon message={actionError} />}
        {loadError && <Alert type="warning" showIcon message={loadError} />}
      </div>
    );
    const showStatusBar =
      Boolean(snapshot.sync_error) || Boolean(snapshot.expiring_soon);
    return (
      <>
        {showStatusBar && (
          <div className={styles.statusDisclosure}>
            <details>
              <summary>
                {t("license.title")}:{" "}
                {t(`license.state.${snapshot.state ?? "ACTIVE"}`)}
              </summary>
              {licenseDetails}
            </details>
          </div>
        )}
        {reminder && (
          <div className={styles.reminder}>
            <Alert
              type="warning"
              showIcon
              closable
              message={t("license.reminderTitle")}
              description={
                <span>
                  {isAdmin
                    ? t("license.reminderAdmin", {
                        date: reminder.expires_at
                          ? formatDate(reminder.expires_at)
                          : reminder.date,
                        remaining: formatRemaining(reminder.remaining_seconds),
                      })
                    : t("license.reminderUser", {
                        date: reminder.expires_at
                          ? formatDate(reminder.expires_at)
                          : reminder.date,
                        remaining: formatRemaining(reminder.remaining_seconds),
                      })}
                </span>
              }
              onClose={() => setReminder(null)}
            />
          </div>
        )}
        {children}
      </>
    );
  }

  const reasonKey = `license.reason.${snapshot.reason ?? "unactivated"}`;
  const reasonText = t(reasonKey);
  const statusText = t(`license.state.${snapshot.state ?? "UNACTIVATED"}`);

  return (
    <main className={styles.gate}>
      <Card className={styles.card}>
        <Typography.Title level={2}>
          {t("license.lockedTitle")}
        </Typography.Title>
        <Typography.Paragraph>
          {t("license.lockedDescription")}
        </Typography.Paragraph>
        <Alert
          type="warning"
          showIcon
          message={t("license.statusSummary", {
            state: statusText,
            reason: reasonText,
          })}
          description={
            <div className={styles.details}>
              <div>
                <strong>{t("license.instanceId")}:</strong>{" "}
                <code>{snapshot.instance_id}</code>
              </div>
              {snapshot.license_id && (
                <div>
                  <strong>{t("license.licenseId")}:</strong>{" "}
                  <code>{snapshot.license_id}</code>
                </div>
              )}
              {snapshot.license_version != null && (
                <div>
                  <strong>{t("license.version")}:</strong>{" "}
                  {snapshot.license_version}
                </div>
              )}
              {expiration != null && (
                <div>
                  <strong>{t("license.expiresAt")}:</strong>{" "}
                  {formatDate(expiration)}
                </div>
              )}
              {snapshot.credential_type && (
                <div>
                  <strong>{t("license.credentialType")}:</strong>{" "}
                  {t(`license.credential.${snapshot.credential_type}`)}
                </div>
              )}
              {snapshot.last_sync_at != null && (
                <div>
                  <strong>{t("license.lastSync")}:</strong>{" "}
                  {formatDate(snapshot.last_sync_at)}
                </div>
              )}
              {snapshot.sync_error && (
                <div className={styles.error}>{snapshot.sync_error}</div>
              )}
            </div>
          }
        />
        {loadError && <Alert type="error" showIcon message={loadError} />}
        {actionError && <Alert type="error" showIcon message={actionError} />}
        {isAdmin ? (
          <section className={styles.adminActions}>
            <Typography.Title level={4}>
              {t("license.adminActions")}
            </Typography.Title>
            <Space wrap>
              <Button onClick={() => void refreshSnapshot()} loading={loading}>
                {t("license.refreshStatus")}
              </Button>
              <Button
                onClick={() => void runAction(() => licensingApi.refresh())}
                loading={busy}
              >
                {t("license.syncOnline")}
              </Button>
              <Button
                onClick={() => void exportOfflineRequest()}
                loading={busy}
              >
                {t("license.exportOfflineRequest")}
              </Button>
            </Space>
            {offlineRequest && (
              <p className={styles.helpText}>
                {t("license.exportedRequest", {
                  fingerprint: offlineRequest.key_fingerprint,
                })}
              </p>
            )}
            <label
              className={styles.fieldLabel}
              htmlFor="license-activation-code"
            >
              {t("license.activationCode")}
            </label>
            <TextArea
              id="license-activation-code"
              autoSize={{ minRows: 3, maxRows: 8 }}
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder={t("license.activationCodePlaceholder")}
              disabled={busy}
            />
            <Button
              type="primary"
              onClick={() =>
                void runAction(() => licensingApi.activate(code.trim()))
              }
              disabled={!code.trim() || busy}
              loading={busy}
            >
              {t("license.activate")}
            </Button>
            <label className={styles.fieldLabel} htmlFor="license-offline-file">
              {t("license.chooseOfflineFile")}
            </label>
            <input
              id="license-offline-file"
              type="file"
              accept=".json,.txt,application/json,text/plain"
              disabled={busy}
              onChange={(event) =>
                void importFile(event.currentTarget.files?.[0])
              }
            />
            <label
              className={styles.fieldLabel}
              htmlFor="license-offline-import"
            >
              {t("license.offlineImport")}
            </label>
            <TextArea
              id="license-offline-import"
              autoSize={{ minRows: 3, maxRows: 8 }}
              value={offlineToken}
              onChange={(event) => setOfflineToken(event.target.value)}
              placeholder={t("license.offlineImportPlaceholder")}
              disabled={busy}
            />
            <Button
              type="primary"
              onClick={() =>
                void runAction(() =>
                  licensingApi.importOffline(toToken(offlineToken)),
                )
              }
              disabled={!offlineToken.trim() || busy}
              loading={busy}
            >
              {t("license.importOffline")}
            </Button>
          </section>
        ) : (
          <Alert
            type="info"
            showIcon
            message={t("license.contactAdmin")}
            description={t("license.contactAdminDescription")}
          />
        )}
        {loadError && (
          <Button onClick={() => void refreshSnapshot()} loading={loading}>
            {t("license.retry")}
          </Button>
        )}
      </Card>
    </main>
  );
}
