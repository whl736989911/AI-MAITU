import { useCallback, useEffect, useMemo, useState } from "react";
import { Alert, Button, Input, Space, Tag, Typography, message } from "antd";
import { FileKey2, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import {
  licensingApi,
  type LicenseSettings,
  type LicenseSnapshot,
  type OfflineLicenseRequest,
} from "../../../api/modules/licensing";
import { useServerTimezone } from "../../../hooks/useServerTimezone";
import { apiErrorMessage } from "../../../utils/apiError";

const { Text, Title } = Typography;
const { TextArea } = Input;

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

async function readFile(file: File | undefined): Promise<string> {
  if (!file) return "";
  if (file.size > 512 * 1024) throw new Error("file is larger than 512 KiB");
  return file.text();
}

function formatDate(
  value: number | null | undefined,
  timeZone: string,
): string {
  if (value == null) return "—";
  return new Intl.DateTimeFormat(undefined, {
    timeZone,
    dateStyle: "medium",
    timeStyle: "short",
  }).format(new Date(value * 1000));
}

export default function LicenseSettingsPanel() {
  const { t } = useTranslation();
  const timeZone = useServerTimezone();
  const [settings, setSettings] = useState<LicenseSettings | null>(null);
  const [status, setStatus] = useState<LicenseSnapshot | null>(null);
  const [centerUrl, setCenterUrl] = useState("");
  const [trustedKeys, setTrustedKeys] = useState("");
  const [rootCa, setRootCa] = useState("");
  const [code, setCode] = useState("");
  const [offlineToken, setOfflineToken] = useState("");
  const [offlineRequest, setOfflineRequest] =
    useState<OfflineLicenseRequest | null>(null);
  const [keysName, setKeysName] = useState("");
  const [caName, setCaName] = useState("");
  const [loading, setLoading] = useState(true);
  const [saving, setSaving] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const [nextSettings, nextStatus] = await Promise.all([
        licensingApi.getSettings(),
        licensingApi.getDetails().catch(() => licensingApi.getStatus()),
      ]);
      setSettings(nextSettings);
      setStatus(nextStatus);
      setCenterUrl(nextSettings.center_url || "https://111.229.110.180:8091");
      setTrustedKeys(nextSettings.trusted_keys_json || "");
      setRootCa(nextSettings.ca_pem || "");
    } catch (err) {
      setError(apiErrorMessage(err, t("license.settings.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void load();
  }, [load]);

  const stateLabel = useMemo(() => {
    if (!status?.managed) return t("license.settings.unmanaged");
    return t(`license.state.${status.state ?? "UNACTIVATED"}`);
  }, [status, t]);

  const save = async () => {
    setSaving(true);
    setError(null);
    try {
      const next = await licensingApi.updateSettings({
        center_url: centerUrl.trim(),
        trusted_keys_json: trustedKeys.trim() ? trustedKeys : null,
        ca_pem: rootCa.trim() ? rootCa : null,
        allow_loopback: false,
      });
      setSettings(next);
      message.success(t("license.settings.saved"));
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, t("license.settings.saveFailed"), t));
    } finally {
      setSaving(false);
    }
  };

  const activate = async () => {
    if (!code.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await licensingApi.activate(code.trim());
      setCode("");
      message.success(t("license.settings.activated"));
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, t("license.actionFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  const refresh = async () => {
    setBusy(true);
    setError(null);
    try {
      await licensingApi.refresh();
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, t("license.actionFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  const importOffline = async () => {
    if (!offlineToken.trim()) return;
    setBusy(true);
    setError(null);
    try {
      await licensingApi.importOffline(offlineToken.trim());
      setOfflineToken("");
      message.success(t("license.settings.activated"));
      await load();
    } catch (err) {
      setError(apiErrorMessage(err, t("license.actionFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div>
      <Space align="start" style={{ marginBottom: 16 }}>
        <ShieldCheck size={22} />
        <div>
          <Title level={5} style={{ margin: 0 }}>
            {t("license.settings.title")}
          </Title>
          <Text type="secondary">{t("license.settings.description")}</Text>
        </div>
      </Space>

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={error}
        />
      )}
      {loading ? null : status ? (
        <Alert
          type={status.business_allowed ? "success" : "warning"}
          showIcon
          style={{ marginBottom: 16 }}
          message={
            <Space wrap>
              <span>{stateLabel}</span>
              {status.license_expires_at != null && (
                <span>
                  {t("license.expiresAt")}：
                  {formatDate(status.license_expires_at, timeZone)}
                </span>
              )}
              {status.last_sync_at != null && (
                <span>
                  {t("license.lastSync")}：
                  {formatDate(status.last_sync_at, timeZone)}
                </span>
              )}
              {status.license_id && <Tag>{status.license_id}</Tag>}
            </Space>
          }
          description={
            status.sync_error ? (
              <span>{status.sync_error}</span>
            ) : status.instance_id ? (
              <span>
                {t("license.instanceId")}: <code>{status.instance_id}</code>
              </span>
            ) : null
          }
        />
      ) : null}

      {settings && (
        <Text type="secondary" style={{ display: "block", marginBottom: 12 }}>
          {t("license.settings.source")}: <code>{settings.source}</code>
        </Text>
      )}

      <Space direction="vertical" size={14} style={{ width: "100%" }}>
        <div>
          <label htmlFor="license-center-url">
            <Text strong>{t("license.settings.centerUrl")}</Text>
          </label>
          <Input
            id="license-center-url"
            value={centerUrl}
            onChange={(event) => setCenterUrl(event.target.value)}
            placeholder="https://license.example.com"
            disabled={saving}
            style={{ marginTop: 6 }}
          />
          <Text type="secondary" className="license-settings-hint">
            {t("license.settings.centerUrlHint")}
          </Text>
        </div>

        <div>
          <label htmlFor="license-trusted-keys">
            <Text strong>{t("license.settings.trustedKeys")}</Text>
          </label>
          <Space style={{ marginTop: 6, marginBottom: 6 }}>
            <label htmlFor="license-settings-keys-file">
              <Button icon={<FileKey2 size={16} />} role="button">
                {keysName || t("license.settings.chooseTrustedKeys")}
              </Button>
            </label>
            <input
              id="license-settings-keys-file"
              type="file"
              accept=".json,application/json"
              hidden
              disabled={saving}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (!file) return;
                void readFile(file)
                  .then((text) => {
                    setTrustedKeys(text);
                    setKeysName(file.name);
                  })
                  .catch((err) => setError(String(err)));
              }}
            />
          </Space>
          <TextArea
            id="license-trusted-keys"
            value={trustedKeys}
            onChange={(event) => setTrustedKeys(event.target.value)}
            autoSize={{ minRows: 4, maxRows: 10 }}
            placeholder={t("license.settings.trustedKeysPlaceholder")}
            disabled={saving}
          />
          <Text type="secondary" className="license-settings-hint">
            {t("license.settings.trustedKeysHint")}
          </Text>
        </div>

        <div>
          <label htmlFor="license-root-ca">
            <Text strong>{t("license.settings.rootCa")}</Text>
          </label>
          <Space style={{ marginTop: 6, marginBottom: 6 }}>
            <label htmlFor="license-settings-ca-file">
              <Button icon={<ShieldCheck size={16} />} role="button">
                {caName || t("license.settings.chooseRootCa")}
              </Button>
            </label>
            <input
              id="license-settings-ca-file"
              type="file"
              accept=".pem,.crt,.cer,text/plain"
              hidden
              disabled={saving}
              onChange={(event) => {
                const file = event.currentTarget.files?.[0];
                if (!file) return;
                void readFile(file)
                  .then((text) => {
                    setRootCa(text);
                    setCaName(file.name);
                  })
                  .catch((err) => setError(String(err)));
              }}
            />
          </Space>
          <TextArea
            id="license-root-ca"
            value={rootCa}
            onChange={(event) => setRootCa(event.target.value)}
            autoSize={{ minRows: 3, maxRows: 8 }}
            placeholder={t("license.settings.rootCaPlaceholder")}
            disabled={saving}
          />
          <Text type="secondary" className="license-settings-hint">
            {t("license.settings.rootCaHint")}
          </Text>
        </div>

        <Button type="primary" onClick={() => void save()} loading={saving}>
          {t("license.settings.save")}
        </Button>

        <div>
          <Text strong>{t("license.activate")}</Text>
          <Space.Compact style={{ width: "100%", marginTop: 6 }}>
            <Input
              value={code}
              onChange={(event) => setCode(event.target.value)}
              placeholder={t("license.activationCodePlaceholder")}
              disabled={busy}
            />
            <Button onClick={() => void activate()} loading={busy}>
              {t("license.activate")}
            </Button>
          </Space.Compact>
        </div>

        <Space wrap>
          <Button onClick={() => void refresh()} loading={busy}>
            {t("license.syncOnline")}
          </Button>
          <Button
            onClick={() =>
              void licensingApi
                .getOfflineRequest()
                .then((request) => {
                  setOfflineRequest(request);
                  downloadOfflineRequest(request);
                })
                .catch((err) =>
                  setError(apiErrorMessage(err, t("license.actionFailed"), t)),
                )
            }
            loading={busy}
          >
            {t("license.exportOfflineRequest")}
          </Button>
        </Space>

        {offlineRequest && (
          <Text type="secondary">
            {t("license.exportedRequest", {
              fingerprint: offlineRequest.key_fingerprint,
            })}
          </Text>
        )}

        <div>
          <Text strong>{t("license.offlineImport")}</Text>
          <TextArea
            value={offlineToken}
            onChange={(event) => setOfflineToken(event.target.value)}
            autoSize={{ minRows: 2, maxRows: 6 }}
            placeholder={t("license.offlineImportPlaceholder")}
            disabled={busy}
            style={{ marginTop: 6 }}
          />
          <Button
            style={{ marginTop: 6 }}
            onClick={() => void importOffline()}
            disabled={!offlineToken.trim()}
            loading={busy}
          >
            {t("license.importOffline")}
          </Button>
        </div>
      </Space>
      <style>{`
        .license-settings-hint {
          display: block;
          margin-top: 6px;
          font-size: 12px;
          line-height: 1.5;
        }
      `}</style>
    </div>
  );
}
