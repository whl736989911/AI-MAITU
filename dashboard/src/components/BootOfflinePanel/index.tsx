import { Button, Result } from "antd";
import { useTranslation } from "react-i18next";

interface BootOfflinePanelProps {
  onRetry: () => void;
}

export default function BootOfflinePanel({ onRetry }: BootOfflinePanelProps) {
  const { t } = useTranslation();

  return (
    <div
      role="alert"
      style={{
        height: "100dvh",
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        background: "var(--fn-bg-layout, #f7f8fa)",
        padding: 24,
        boxSizing: "border-box",
      }}
    >
      <Result
        status="warning"
        title={t("errors.offlineTitle")}
        subTitle={t("errors.offlineSubtitle")}
        extra={
          <Button type="primary" onClick={onRetry}>
            {t("errors.retry")}
          </Button>
        }
      />
    </div>
  );
}
