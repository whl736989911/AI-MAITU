import { useCallback, useEffect, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Segmented, Steps, Typography } from "antd";
import {
  Lock,
  UserCog,
  Cpu,
  CheckCircle,
  Wand2,
  Database,
  ShieldCheck,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import { ensureLocaleBundle } from "../../i18n";
import { storeUiLocale, type UiLocale } from "../../utils/locale";

import { authApi } from "../../api/modules/auth";
import { setAuthToken } from "../../api/request";
import { preferencesApi } from "../../api/modules/preferences";
import BootOfflinePanel from "../../components/BootOfflinePanel";
import { useTheme } from "../../context/ThemeContext";
import DatabaseStep from "./steps/DatabaseStep";
import PasswordStep from "./steps/PasswordStep";
import LicenseStep from "./steps/LicenseStep";
import AdminStep from "./steps/AdminStep";
import ModelStep from "./steps/ModelStep";
import FinishStep from "./steps/FinishStep";
import type { ProviderDraft } from "./wizardClient";
import { BRAND, wordmark } from "../../brand.generated";
import {
  wizardApi,
  wizardSession,
  resolveSetupProbeToken,
  STEP_DATABASE,
  STEP_PASSWORD,
  STEP_LICENSE,
  STEP_ADMIN,
  STEP_MODEL,
  STEP_FINISH,
} from "./wizardClient";
import styles from "./setup.module.less";

const { Text } = Typography;

export default function SetupPage() {
  const { t, i18n } = useTranslation();
  const { isDark } = useTheme();
  const navigate = useNavigate();
  const [checking, setChecking] = useState(true);
  const [offline, setOffline] = useState(false);
  const [statusRetryKey, setStatusRetryKey] = useState(0);
  const [passwordRequired, setPasswordRequired] = useState(true);
  const [passwordNextStep, setPasswordNextStep] = useState(STEP_DATABASE);
  const [current, setCurrentRaw] = useState<number>(STEP_PASSWORD);
  const [adminCreds, setAdminCreds] = useState<{
    username: string;
    password: string;
  } | null>(null);
  const [providerDraft, setProviderDraft] = useState<ProviderDraft | null>(
    null,
  );

  const goToStep = useCallback((step: number) => {
    setCurrentRaw(step);
    wizardSession.saveStep(step);
  }, []);

  const ensureWizardToken = useCallback(async () => {
    const token = wizardSession.loadToken();
    if (token) {
      try {
        const { valid } = await wizardApi.validateToken(token);
        if (valid) return token;
      } catch {
        /* fall through */
      }
    }
    const r = await wizardApi.begin();
    wizardSession.saveToken(r.wizard_token);
    return r.wizard_token;
  }, []);

  useEffect(() => {
    setOffline(false);
    setChecking(true);
    let cancelled = false;
    authApi
      .getAuthStatus()
      .then(async (status) => {
        if (cancelled) return;
        if (!status.setup_required) {
          wizardSession.clearAll();
          navigate("/login", { replace: true });
          return;
        }

        setPasswordRequired(status.wizard_password_required);
        setPasswordNextStep(STEP_DATABASE);

        const savedStep = wizardSession.loadStep();
        const draft = wizardSession.loadDraft();
        if (draft.provider) {
          setProviderDraft(draft.provider);
        }
        if (status.admin_created) {
          const nextStep = savedStep === STEP_FINISH ? STEP_FINISH : STEP_MODEL;
          setPasswordNextStep(nextStep);
          if (!(await resolveSetupProbeToken())) {
            if (status.wizard_password_required) {
              setCurrentRaw(STEP_PASSWORD);
              setChecking(false);
              return;
            }
            await ensureWizardToken();
          }
          if (cancelled) return;
          const setupJwt = wizardSession.loadSetupJwt();
          if (setupJwt) setAuthToken(setupJwt);
          goToStep(nextStep);
          setChecking(false);
          return;
        }
        const token = wizardSession.loadToken();
        if (token) {
          try {
            const { valid } = await wizardApi.validateToken(token);
            if (cancelled) return;
            if (valid) {
              if (
                savedStep !== null &&
                savedStep >= STEP_DATABASE &&
                savedStep <= STEP_MODEL
              ) {
                if (savedStep === STEP_DATABASE && status.database_bound) {
                  goToStep(STEP_LICENSE);
                } else {
                  goToStep(savedStep);
                }
              } else if (
                savedStep === STEP_PASSWORD &&
                status.wizard_password_required
              ) {
                goToStep(STEP_PASSWORD);
              } else {
                goToStep(status.database_bound ? STEP_LICENSE : STEP_DATABASE);
              }
              setChecking(false);
              return;
            }
          } catch {
            /* fall through */
          }
        }
        const setupJwt = wizardSession.loadSetupJwt();
        if (setupJwt) {
          setAuthToken(setupJwt);
          if (savedStep === STEP_FINISH) {
            goToStep(STEP_FINISH);
          } else {
            goToStep(STEP_MODEL);
          }
          if (!cancelled) setChecking(false);
          return;
        }

        // No valid wizard token yet.
        if (status.wizard_password_required) {
          goToStep(STEP_PASSWORD);
        } else {
          try {
            await ensureWizardToken();
          } catch {
            /* continue into database step; begin can be retried */
          }
          if (!cancelled) goToStep(STEP_DATABASE);
        }
        if (!cancelled) setChecking(false);
      })
      .catch(() => {
        if (cancelled) return;
        setOffline(true);
        setChecking(false);
      });
    return () => {
      cancelled = true;
    };
  }, [navigate, goToStep, ensureWizardToken, statusRetryKey]);

  const handlePasswordVerified = () => {
    goToStep(passwordNextStep);
  };

  const handleDatabaseContinue = async () => {
    try {
      await ensureWizardToken();
    } catch {
      /* token may already exist after password step */
    }
    goToStep(STEP_LICENSE);
  };

  const handleBackFromAdmin = () => {
    setAdminCreds(null);
    setProviderDraft(null);
    wizardSession.saveDraft({});
    goToStep(STEP_LICENSE);
  };

  const handleBackFromDatabase = () => {
    if (!passwordRequired) return;
    wizardSession.clearToken();
    goToStep(STEP_PASSWORD);
  };

  if (offline) {
    return (
      <BootOfflinePanel
        onRetry={() => {
          setChecking(true);
          setOffline(false);
          setStatusRetryKey((key) => key + 1);
        }}
      />
    );
  }

  if (checking) {
    return (
      <div
        style={{
          height: "100dvh",
          boxSizing: "border-box",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          padding:
            "env(safe-area-inset-top, 0px) env(safe-area-inset-right, 0px) env(safe-area-inset-bottom, 0px) env(safe-area-inset-left, 0px)",
          background: "var(--fn-bg-layout)",
        }}
      >
        <Text type="secondary">Checking setup status…</Text>
      </div>
    );
  }

  const isModelStep = current === STEP_MODEL;
  const isWideStep = isModelStep || current === STEP_LICENSE;
  const currentLang = i18n.language?.startsWith("zh") ? "zh" : "en";

  const handleLanguageChange = (lang: string) => {
    const locale: UiLocale = lang.startsWith("zh") ? "zh" : "en";
    storeUiLocale(locale);
    void ensureLocaleBundle(locale).then(() => i18n.changeLanguage(locale));
    const setupJwt = wizardSession.loadSetupJwt();
    if (setupJwt) {
      void preferencesApi.setLocale(locale).catch(() => {
        /* best-effort */
      });
    }
  };

  const stepItems = passwordRequired
    ? [
        { title: t("wizard.steps.password"), icon: <Lock size={22} /> },
        { title: t("wizard.steps.database"), icon: <Database size={22} /> },
        { title: t("wizard.steps.license"), icon: <ShieldCheck size={22} /> },
        { title: t("wizard.steps.admin"), icon: <UserCog size={22} /> },
        { title: t("wizard.steps.model"), icon: <Cpu size={22} /> },
        { title: t("common.done"), icon: <CheckCircle size={22} /> },
      ]
    : [
        { title: t("wizard.steps.database"), icon: <Database size={22} /> },
        { title: t("wizard.steps.license"), icon: <ShieldCheck size={22} /> },
        { title: t("wizard.steps.admin"), icon: <UserCog size={22} /> },
        { title: t("wizard.steps.model"), icon: <Cpu size={22} /> },
        { title: t("common.done"), icon: <CheckCircle size={22} /> },
      ];

  const stepIndex = (() => {
    if (passwordRequired) return current;
    // Password step omitted visually: map DATABASE..FINISH → 0..4
    if (current <= STEP_DATABASE) return 0;
    if (current === STEP_LICENSE) return 1;
    if (current === STEP_ADMIN) return 2;
    if (current === STEP_MODEL) return 3;
    return 4;
  })();

  return (
    <div className={styles.wizardShell}>
      <div
        className={`${styles.wizardCard} ${
          isWideStep ? styles.wizardCardWide : styles.wizardCardNarrow
        }`}
      >
        <div className={styles.wizardHeader}>
          <div className={styles.wizardHeaderTop}>
            <div className={styles.wizardHeaderBrand}>
              <img
                src={wordmark(isDark)}
                alt={BRAND.name.en}
                className={styles.wizardHeaderLogo}
              />
              <div className={styles.wizardHeaderBrandText}>
                <Text type="secondary" className={styles.wizardHeaderSubtitle}>
                  <Wand2 size={11} /> {t("wizard.title")}
                </Text>
              </div>
            </div>
            <Segmented
              className={styles.wizardHeaderLang}
              size="small"
              value={currentLang}
              options={[
                { label: t("account.langZh"), value: "zh" },
                { label: t("account.langEn"), value: "en" },
              ]}
              onChange={handleLanguageChange}
            />
          </div>
          <Steps
            className={styles.wizardHeaderSteps}
            current={stepIndex}
            labelPlacement="vertical"
            responsive={false}
            items={stepItems}
          />
        </div>

        <div
          className={`${styles.wizardBody} ${
            isModelStep ? styles.wizardBodyFlush : ""
          }`}
        >
          {current === STEP_PASSWORD && passwordRequired && (
            <PasswordStep onVerified={handlePasswordVerified} />
          )}
          {current === STEP_DATABASE && (
            <DatabaseStep
              onContinue={() => void handleDatabaseContinue()}
              onBack={passwordRequired ? handleBackFromDatabase : undefined}
            />
          )}
          {current === STEP_LICENSE && (
            <LicenseStep
              onContinue={() => goToStep(STEP_ADMIN)}
              onSkip={() => goToStep(STEP_ADMIN)}
              onBack={() => goToStep(STEP_DATABASE)}
            />
          )}
          {current === STEP_ADMIN && (
            <AdminStep
              createdCreds={adminCreds}
              onBack={handleBackFromAdmin}
              onCreated={(creds) => {
                setAdminCreds(creds);
                wizardSession.saveDraft({ adminUsername: creds.username });
                goToStep(STEP_MODEL);
              }}
            />
          )}
          {current === STEP_MODEL && (
            <ModelStep
              onBack={() => goToStep(STEP_ADMIN)}
              onSkip={() => {
                setProviderDraft(null);
                wizardSession.saveDraft({});
                goToStep(STEP_FINISH);
              }}
              onContinue={(draft) => {
                setProviderDraft(draft);
                goToStep(STEP_FINISH);
              }}
            />
          )}
          {current === STEP_FINISH && (
            <FinishStep adminCreds={adminCreds} providerDraft={providerDraft} />
          )}
        </div>
      </div>
    </div>
  );
}
