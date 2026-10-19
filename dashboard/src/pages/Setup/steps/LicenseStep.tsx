import { useState } from "react";
import { Alert, Button, Input, Space, Typography } from "antd";
import { FileKey2, ShieldCheck } from "lucide-react";
import { useTranslation } from "react-i18next";

import { apiErrorMessage } from "../../../utils/apiError";
import {
  DEFAULT_LICENSE_CENTER_URL,
  resolveSetupProbeToken,
  wizardApi,
} from "../wizardClient";
import setupStyles from "../setup.module.less";

const { Text } = Typography;

interface Props {
  onContinue: () => void;
  onSkip: () => void;
  onBack?: () => void;
}

async function readTextFile(file: File | undefined): Promise<string> {
  if (!file) return "";
  if (file.size > 512 * 1024) {
    throw new Error("file is larger than 512 KiB");
  }
  return file.text();
}

export default function LicenseStep({ onContinue, onSkip, onBack }: Props) {
  const { t } = useTranslation();
  const [centerUrl, setCenterUrl] = useState(DEFAULT_LICENSE_CENTER_URL);
  const [trustedKeys, setTrustedKeys] = useState("");
  const [rootCa, setRootCa] = useState("");
  const [trustedKeysName, setTrustedKeysName] = useState("");
  const [rootCaName, setRootCaName] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const loadFile = async (
    file: File | undefined,
    setText: (value: string) => void,
    setName: (value: string) => void,
  ) => {
    try {
      setText(await readTextFile(file));
      setName(file?.name ?? "");
      setError(null);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    }
  };

  const save = async (skip: boolean) => {
    setBusy(true);
    setError(null);
    try {
      const token = await resolveSetupProbeToken();
      if (!token) throw new Error(t("wizard.sessionExpired"));
      await wizardApi.saveLicenseConfig(
        {
          center_url: centerUrl.trim(),
          trusted_keys_json: trustedKeys.trim() || null,
          ca_pem: rootCa.trim() || null,
          allow_loopback: false,
          skip,
        },
        token,
      );
      if (skip) onSkip();
      else onContinue();
    } catch (err) {
      setError(apiErrorMessage(err, t("wizard.license.saveFailed"), t));
    } finally {
      setBusy(false);
    }
  };

  return (
    <>
      <div style={{ marginBottom: 20 }}>
        <div
          style={{
            fontSize: 16,
            fontWeight: 600,
            color: "var(--fn-text-primary)",
            marginBottom: 4,
          }}
        >
          {t("wizard.license.title")}
        </div>
        <Text type="secondary" style={{ fontSize: 13 }}>
          {t("wizard.license.hint")}
        </Text>
      </div>

      {error && (
        <Alert
          type="error"
          showIcon
          style={{ marginBottom: 16 }}
          message={error}
        />
      )}

      <div className={setupStyles.fieldGroup}>
        <label className={setupStyles.fieldLabel} htmlFor="license-center-url">
          {t("wizard.license.centerUrl")}
        </label>
        <Input
          id="license-center-url"
          value={centerUrl}
          onChange={(event) => setCenterUrl(event.target.value)}
          placeholder={DEFAULT_LICENSE_CENTER_URL}
          disabled={busy}
        />
        <Text type="secondary" className={setupStyles.fieldHint}>
          {t("wizard.license.centerUrlHint")}
        </Text>
      </div>

      <div className={setupStyles.fieldGroup}>
        <label
          className={setupStyles.fieldLabel}
          htmlFor="license-trusted-keys"
        >
          {t("wizard.license.trustedKeys")}
        </label>
        <Space direction="vertical" style={{ width: "100%" }}>
          <label className={setupStyles.fileButton} htmlFor="license-keys-file">
            <FileKey2 size={16} />
            {trustedKeysName || t("wizard.license.chooseTrustedKeys")}
          </label>
          <input
            id="license-keys-file"
            type="file"
            accept=".json,application/json"
            hidden
            disabled={busy}
            onChange={(event) =>
              void loadFile(
                event.currentTarget.files?.[0],
                setTrustedKeys,
                setTrustedKeysName,
              )
            }
          />
          <Input.TextArea
            id="license-trusted-keys"
            value={trustedKeys}
            onChange={(event) => setTrustedKeys(event.target.value)}
            autoSize={{ minRows: 4, maxRows: 10 }}
            placeholder={t("wizard.license.trustedKeysPlaceholder")}
            disabled={busy}
          />
        </Space>
        <Text type="secondary" className={setupStyles.fieldHint}>
          {t("wizard.license.trustedKeysHint")}
        </Text>
      </div>

      <div className={setupStyles.fieldGroup}>
        <label className={setupStyles.fieldLabel} htmlFor="license-root-ca">
          {t("wizard.license.rootCa")}
        </label>
        <Space direction="vertical" style={{ width: "100%" }}>
          <label className={setupStyles.fileButton} htmlFor="license-ca-file">
            <ShieldCheck size={16} />
            {rootCaName || t("wizard.license.chooseRootCa")}
          </label>
          <input
            id="license-ca-file"
            type="file"
            accept=".pem,.crt,.cer,text/plain"
            hidden
            disabled={busy}
            onChange={(event) =>
              void loadFile(
                event.currentTarget.files?.[0],
                setRootCa,
                setRootCaName,
              )
            }
          />
          <Input.TextArea
            id="license-root-ca"
            value={rootCa}
            onChange={(event) => setRootCa(event.target.value)}
            autoSize={{ minRows: 3, maxRows: 8 }}
            placeholder={t("wizard.license.rootCaPlaceholder")}
            disabled={busy}
          />
        </Space>
        <Text type="secondary" className={setupStyles.fieldHint}>
          {t("wizard.license.rootCaHint")}
        </Text>
      </div>

      <Space style={{ width: "100%", justifyContent: "space-between" }}>
        {onBack ? (
          <Button onClick={onBack}>{t("wizard.back")}</Button>
        ) : (
          <span />
        )}
        <Space>
          <Button onClick={() => void save(true)} loading={busy}>
            {t("wizard.license.skip")}
          </Button>
          <Button
            type="primary"
            onClick={() => void save(false)}
            loading={busy}
          >
            {t("wizard.license.save")}
          </Button>
        </Space>
      </Space>
    </>
  );
}
