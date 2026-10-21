import { useEffect, useState } from "react";
import { Spin, Alert, Button, Typography, Space } from "antd";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";

import { authApi } from "../../../api/modules/auth";
import { setAuthToken } from "../../../api/request";
import { refreshServerLabels } from "../../../i18n";
import { applyUserLocale } from "../../../utils/locale";
import {
  wizardApi,
  wizardSession,
  resolveSetupProbeToken,
} from "../wizardClient";
import type { ProviderDraft } from "../wizardClient";

const { Text } = Typography;

interface Props {
  adminCreds: { username: string; password: string } | null;
  providerDraft: ProviderDraft | null;
}

export default function FinishStep({ adminCreds, providerDraft }: Props) {
  const { t } = useTranslation();
  const navigate = useNavigate();
  const [running, setRunning] = useState(true);
  const [attempt, setAttempt] = useState(0);
  const [errors, setErrors] = useState<{ login?: string; provider?: string }>(
    {},
  );

  useEffect(() => {
    let cancelled = false;
    setRunning(true);
    setErrors({});
    (async () => {
      const wizardToken = await resolveSetupProbeToken();
      if (!wizardToken) {
        if (!cancelled) {
          setErrors({ provider: t("wizard.sessionExpired") });
          setRunning(false);
        }
        return;
      }

      try {
        await wizardApi.finish({ provider_draft: providerDraft }, wizardToken);
      } catch (e) {
        if (!cancelled) {
          setErrors({
            provider: e instanceof Error ? e.message : String(e),
          });
          setRunning(false);
        }
        return;
      }

      try {
        const setupJwt = wizardSession.loadSetupJwt();
        if (adminCreds) {
          const r = await authApi.login(
            adminCreds.username,
            adminCreds.password,
          );
          setAuthToken(r.access_token);
          await applyUserLocale(r.user.locale);
          void refreshServerLabels(r.user.locale);
        } else if (setupJwt) {
          setAuthToken(setupJwt);
        } else {
          wizardSession.clearAll();
          if (!cancelled) {
            setRunning(false);
            navigate("/login", { replace: true });
          }
          return;
        }
      } catch (e) {
        if (!cancelled) {
          setErrors({
            login: e instanceof Error ? e.message : String(e),
          });
          setRunning(false);
        }
        return;
      }

      wizardSession.clearAll();
      if (!cancelled) {
        setRunning(false);
        setTimeout(() => {
          if (!cancelled) navigate("/chat", { replace: true });
        }, 400);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [adminCreds, providerDraft, navigate, t, attempt]);

  if (running) {
    return (
      <Space direction="vertical" align="center" style={{ width: "100%" }}>
        <Spin />
        <Text type="secondary">{t("wizard.finish.running")}</Text>
      </Space>
    );
  }

  return (
    <Space direction="vertical" style={{ width: "100%" }} size="middle">
      {errors.login && (
        <Alert type="error" showIcon message={t("wizard.finish.loginFailed")} />
      )}
      {errors.provider && (
        <Alert
          type="error"
          showIcon
          message={t("wizard.finish.failed")}
          description={errors.provider}
        />
      )}
      {errors.provider && (
        <Button
          type="primary"
          block
          onClick={() => setAttempt((value) => value + 1)}
        >
          {t("common.retry")}
        </Button>
      )}
      {errors.login && (
        <Button
          block
          onClick={() => {
            wizardSession.clearAll();
            navigate("/login", { replace: true });
          }}
        >
          {t("wizard.finish.fallbackToLogin")}
        </Button>
      )}
    </Space>
  );
}
