import { useCallback, useEffect, useState } from "react";
import { Alert, Button, Spin, Tag } from "antd";
import { message } from "@/utils/antdMessage";
import { Copy, RefreshCw, Shield } from "lucide-react";
import { useTranslation } from "react-i18next";
import {
  nativeSandboxApi,
  type BubblewrapStatusResult,
} from "../../../api/modules/nativeSandbox";
import { apiErrorMessage } from "../../../utils/apiError";
import { copyText } from "../../../utils/copyText";
import { TabPanelHeader } from "./TabPanelHeader";
import tabStyles from "./tabContent.module.less";

type StatusTone = "success" | "processing" | "warning" | "error" | "default";

const STATUS_TONE: Record<BubblewrapStatusResult["status"], StatusTone> = {
  ready: "success",
  installed: "success",
  skipped: "warning",
  degraded: "error",
};

export default function BubblewrapSettings() {
  const { t } = useTranslation();
  const [result, setResult] = useState<BubblewrapStatusResult | null>(null);
  const [loading, setLoading] = useState(true);
  const [installing, setInstalling] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    setLoading(true);
    setResult(null);
    setError(null);
    try {
      setResult(await nativeSandboxApi.getBubblewrapStatus());
    } catch (err) {
      setError(apiErrorMessage(err, t("advancedSettings.bwrap.loadFailed"), t));
    } finally {
      setLoading(false);
    }
  }, [t]);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  const ensure = async () => {
    setInstalling(true);
    setResult(null);
    setError(null);
    try {
      setResult(await nativeSandboxApi.ensureBubblewrap());
    } catch (err) {
      setError(
        apiErrorMessage(err, t("advancedSettings.bwrap.actionFailed"), t),
      );
    } finally {
      setInstalling(false);
    }
  };

  const copyCommand = async (command: string) => {
    if (await copyText(command)) {
      message.success(t("advancedSettings.bwrap.copied"));
    } else {
      message.error(t("advancedSettings.bwrap.copyFailed"));
    }
  };

  const diagnosis = result?.diagnosis;
  const diagnosisLabel = diagnosis
    ? t(`advancedSettings.bwrap.diagnosis.${diagnosis}`)
    : result?.status === "ready" || result?.status === "installed"
    ? t("advancedSettings.bwrap.diagnosis.ready")
    : null;

  return (
    <div>
      <TabPanelHeader
        icon={<Shield size={20} />}
        title={t("advancedSettings.bwrap.title")}
        description={t("advancedSettings.bwrap.description")}
        actions={
          <Button
            icon={<RefreshCw size={16} />}
            onClick={() => void refresh()}
            loading={loading}
          >
            {t("advancedSettings.bwrap.refresh")}
          </Button>
        }
      />
      <section aria-label={t("advancedSettings.bwrap.panelTitle")}>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 10,
            marginBottom: 12,
          }}
        >
          <strong>{t("advancedSettings.bwrap.panelTitle")}</strong>
          {result ? (
            <Tag color={STATUS_TONE[result.status]}>
              {t(`advancedSettings.bwrap.status.${result.status}`)}
            </Tag>
          ) : null}
        </div>
        {loading && !result ? <Spin /> : null}
        {error ? (
          <Alert
            type="error"
            showIcon
            message={error}
            style={{ marginBottom: 16 }}
          />
        ) : null}
        {result && !loading ? (
          <>
            {diagnosisLabel ? (
              <Alert
                type={
                  result.status === "ready" || result.status === "installed"
                    ? "success"
                    : "warning"
                }
                showIcon
                message={diagnosisLabel}
                description={result.detail || undefined}
                style={{ marginBottom: 16 }}
              />
            ) : null}
            {result.reason ? (
              <p className={tabStyles.sectionDesc}>
                {t("advancedSettings.bwrap.reason", { reason: result.reason })}
              </p>
            ) : null}
            {result.commands.length > 0 ? (
              <div style={{ marginBottom: 16 }}>
                <p>{t("advancedSettings.bwrap.commandsDescription")}</p>
                <ol style={{ paddingInlineStart: 24 }}>
                  {result.commands.map((command, index) => (
                    <li
                      key={`${index}-${command}`}
                      style={{ marginBottom: 12 }}
                    >
                      <pre
                        style={{
                          whiteSpace: "pre-wrap",
                          overflowWrap: "anywhere",
                          margin: "0 0 6px",
                          padding: 12,
                          borderRadius: 6,
                          background: "var(--fn-bg-secondary)",
                        }}
                      >
                        <code>{command}</code>
                      </pre>
                      <Button
                        size="small"
                        icon={<Copy size={14} />}
                        onClick={() => void copyCommand(command)}
                      >
                        {t("advancedSettings.bwrap.copyCommand")}
                      </Button>
                    </li>
                  ))}
                </ol>
              </div>
            ) : null}
          </>
        ) : null}
        <Button
          type="primary"
          onClick={() => void ensure()}
          loading={installing}
          disabled={loading || installing}
        >
          {t("advancedSettings.bwrap.installDiagnose")}
        </Button>
      </section>
    </div>
  );
}
