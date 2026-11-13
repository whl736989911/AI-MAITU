import { useState, useEffect, useRef, type ReactNode } from "react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { Input, Button, Modal } from "antd";
import { message } from "@/utils/antdMessage";

import { KeyRound, Lock, User } from "lucide-react";
import { useTranslation } from "react-i18next";
import { clearAuthToken, setAuthToken } from "../../api";
import { authApi, type OauthProviderStatus } from "../../api/modules/auth";
import { publicUrl } from "../../utils/publicUrl";

import { apiErrorMessage, parseApiError } from "../../utils/apiError";
import { refreshServerLabels } from "../../i18n";
import { applyUserLocale, applyGuestLocale } from "../../utils/locale";
import { useTheme } from "../../context/ThemeContext";
import { useBranding } from "../../context/BrandingContext";
import {
  isSsoPopup,
  isSsoPopupMessage,
  notifySsoOpener,
  openSsoPopup,
} from "../../utils/ssoPopup";
import feishuIcon from "../../assets/channels/feishu.svg";
import dingtalkIcon from "../../assets/channels/dingtalk.svg";
import wecomIcon from "../../assets/channels/wecom.svg";
import googleIcon from "../../assets/providers/google.svg";
import CaptchaField, { type CaptchaFieldHandle } from "./CaptchaField";
import type { PublicCaptchaConfig } from "./captchaAdapters";
import styles from "./index.module.less";

function providerLabel(
  provider: OauthProviderStatus,
  t: (key: string, opts?: Record<string, string>) => string,
): string {
  const name = provider.display_name.trim();
  if (name) return name;
  return t(`login.providerKind.${provider.kind}`, {
    defaultValue: provider.kind,
  });
}

function providerIcon(provider: OauthProviderStatus): ReactNode {
  if (provider.kind === "feishu") {
    return (
      <img src={feishuIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "dingtalk") {
    return (
      <img src={dingtalkIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  if (provider.kind === "wecom") {
    return (
      <img src={wecomIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  const name = provider.display_name.trim().toLowerCase();
  if (
    provider.kind === "oidc" &&
    (name === "google" || name.includes("google"))
  ) {
    return (
      <img src={googleIcon} alt="" width={18} height={18} draggable={false} />
    );
  }
  return <KeyRound size={18} />;
}

export default function LoginPage() {
  const { t, i18n } = useTranslation();
  const { isDark } = useTheme();
  const brand = useBranding();
  const navigate = useNavigate();
  const [searchParams] = useSearchParams();
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [loading, setLoading] = useState(false);
  const [providers, setProviders] = useState<OauthProviderStatus[]>([]);
  const [ssoLoadingKind, setSsoLoadingKind] = useState<string | null>(null);
  const [captchaReady, setCaptchaReady] = useState(false);
  const [captchaResetKey, setCaptchaResetKey] = useState(0);
  const [captcha, setCaptcha] = useState<PublicCaptchaConfig>({
    provider: "slider",
  });
  const captchaRef = useRef<CaptchaFieldHandle>(null);
  const [accessExpiredNotice, setAccessExpiredNotice] = useState<string | null>(
    null,
  );

  useEffect(() => {
    void applyGuestLocale();
  }, []);

  useEffect(() => {
    let cancelled = false;
    authApi
      .getAuthStatus()
      .then((status) => {
        if (cancelled) return;
        if (status.setup_required) {
          clearAuthToken();
          navigate("/setup", { replace: true });
          return;
        }
        // Only probe OIDC / captcha after setup is done — otherwise lockdown 503s.
        authApi
          .getOauthStatus()
          .then((next) => {
            if (!cancelled) {
              setProviders(next.providers.filter((item) => item.enabled));
            }
          })
          .catch(() => {});
        authApi
          .getCaptcha()
          .then((next) => {
            if (!cancelled) setCaptcha(next);
          })
          .catch(() => {
            if (!cancelled) setCaptcha({ provider: "slider" });
          });
      })
      .catch(() => {
        // Backend unreachable — let the user attempt login and show a real
        // error from the request itself; redirecting blindly to /setup
        // would mask the actual problem.
      });
    return () => {
      cancelled = true;
    };
  }, [navigate]);

  useEffect(() => {
    if (searchParams.get("oidc_error") !== "access_expired") return;
    const params = new URLSearchParams(window.location.hash.slice(1));
    const notice = params.get("access_notice") || "";
    if (notifySsoOpener({ ok: false, error: "access_expired", notice })) return;
    setAccessExpiredNotice(
      notice ||
        t("login.accessExpiredDefault", "Your account access has expired."),
    );
    navigate("/login", { replace: true });
  }, [navigate, searchParams, t]);

  useEffect(() => {
    const code = searchParams.get("oidc_error");
    if (!code || code === "access_expired") return;
    if (notifySsoOpener({ ok: false, error: code })) return;
    message.error(
      t(`login.oidcError.${code}`, {
        defaultValue: t("login.oidcError.generic"),
      }),
    );
    navigate("/login", { replace: true });
  }, [navigate, searchParams, t]);

  useEffect(() => {
    const onMessage = (event: MessageEvent) => {
      if (!isSsoPopupMessage(event, window.location.origin) || !event.data.ok) {
        if (
          isSsoPopupMessage(event, window.location.origin) &&
          !event.data.ok
        ) {
          const code = event.data.error || "generic";
          if (code === "access_expired") {
            setAccessExpiredNotice(
              event.data.notice ||
                t(
                  "login.accessExpiredDefault",
                  "Your account access has expired.",
                ),
            );
          } else {
            message.error(
              t(`login.oidcError.${code}`, {
                defaultValue: t("login.oidcError.generic"),
              }),
            );
          }
        }
        return;
      }
      window.location.replace(publicUrl(event.data.redirect || "/chat"));
    };
    window.addEventListener("message", onMessage);
    return () => window.removeEventListener("message", onMessage);
  }, [t]);

  const resetCaptcha = () => {
    setCaptchaReady(false);
    setCaptchaResetKey((k) => k + 1);
  };

  const onSso = async (kind: string) => {
    setSsoLoadingKind(kind);
    let popup: Window | null = null;
    if (kind !== "oidc") {
      popup = openSsoPopup();
    }
    try {
      const { authorization_url } = await authApi.startOauth(kind, "/chat");
      if (kind === "oidc") {
        window.location.href = authorization_url;
        return;
      }
      if (popup && !popup.closed) {
        popup.location.href = authorization_url;
        const timer = window.setInterval(() => {
          if (!popup || popup.closed) {
            window.clearInterval(timer);
            setSsoLoadingKind((current) => (current === kind ? null : current));
          }
        }, 400);
      } else {
        popup?.close();
        message.error(t("account.ssoPopupBlocked"));
        setSsoLoadingKind(null);
      }
    } catch (err) {
      popup?.close();
      message.error(apiErrorMessage(err, t("login.oidcStartFailed"), t));
      setSsoLoadingKind(null);
    }
  };

  const handleLogin = async () => {
    if (!username || !password || !captchaReady) return;
    setLoading(true);
    try {
      const token = await captchaRef.current?.getToken();
      const res = await authApi.login(username, password, token);
      setAuthToken(res.access_token);
      await applyUserLocale(res.user.locale);
      void refreshServerLabels(res.user.locale);
      navigate("/chat", { replace: true });
    } catch (err) {
      const parsed = parseApiError(err);
      if (parsed?.code === "ACCESS_EXPIRED") {
        const notice = parsed.details?.notice;
        setAccessExpiredNotice(
          typeof notice === "string" && notice
            ? notice
            : t("login.accessExpiredDefault"),
        );
      } else {
        message.error(apiErrorMessage(err, t("login.failed"), t));
      }
      resetCaptcha();
    } finally {
      setLoading(false);
    }
  };

  if (isSsoPopup() && searchParams.get("oidc_error")) {
    return null;
  }

  return (
    <>
      <Modal
        open={accessExpiredNotice !== null}
        title={t("login.accessExpiredTitle", "Account access expired")}
        onOk={() => setAccessExpiredNotice(null)}
        onCancel={() => setAccessExpiredNotice(null)}
        cancelButtonProps={{ style: { display: "none" } }}
        okText={t("common.ok", "OK")}
      >
        <div style={{ whiteSpace: "pre-wrap", overflowWrap: "anywhere" }}>
          {accessExpiredNotice}
        </div>
      </Modal>
      <div className={styles.loginShell}>
        <div className={styles.loginCard}>
          <img
            className={styles.brandLogo}
            src={
              isDark ? brand.logos.wordmark_dark : brand.logos.wordmark_light
            }
            alt={brand.name.en}
          />

          <h2 className={styles.loginTitle}>{t("login.title")}</h2>

          <Input
            prefix={
              <User size={16} style={{ color: "var(--fn-text-quaternary)" }} />
            }
            placeholder={t("login.username")}
            size="large"
            value={username}
            onChange={(e) => setUsername(e.target.value)}
            autoFocus
            style={{ borderRadius: 10 }}
          />

          <Input.Password
            prefix={
              <Lock size={16} style={{ color: "var(--fn-text-quaternary)" }} />
            }
            placeholder={t("login.password")}
            size="large"
            value={password}
            onChange={(e) => setPassword(e.target.value)}
            onPressEnter={handleLogin}
            style={{ borderRadius: 10 }}
          />

          <CaptchaField
            ref={captchaRef}
            config={captcha}
            resetKey={captchaResetKey}
            slideHint={t("login.slideHint")}
            slideVerifiedLabel={t("login.slideVerified")}
            unsupportedLabel={t("login.unsupportedCaptcha")}
            onReadyChange={setCaptchaReady}
          />

          <Button
            type="primary"
            size="large"
            block
            loading={loading}
            onClick={handleLogin}
            disabled={!username || !password || !captchaReady}
            style={{ borderRadius: 10, height: 44, fontWeight: 500 }}
          >
            {t("login.submit")}
          </Button>

          <p
            data-testid="login-forgot-password"
            style={{
              margin: 0,
              fontSize: 13,
              lineHeight: 1.6,
              color: "var(--fn-text-tertiary)",
              textAlign: "center",
              width: "100%",
            }}
          >
            {t("login.forgotPasswordHint", {
              brand: brand.name[i18n.language.startsWith("zh") ? "zh" : "en"],
            })}
          </p>

          {providers.length > 0 && (
            <>
              <div
                style={{
                  width: "100%",
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  color: "var(--fn-text-tertiary)",
                  fontSize: 13,
                }}
              >
                <span
                  style={{
                    flex: 1,
                    height: 1,
                    background: "var(--fn-border-primary)",
                  }}
                />
                {t("login.or")}
                <span
                  style={{
                    flex: 1,
                    height: 1,
                    background: "var(--fn-border-primary)",
                  }}
                />
              </div>
              {providers.map((provider) => (
                <Button
                  key={provider.kind}
                  size="large"
                  block
                  icon={providerIcon(provider)}
                  loading={ssoLoadingKind === provider.kind}
                  onClick={() => void onSso(provider.kind)}
                  style={{ borderRadius: 10, height: 44, fontWeight: 500 }}
                >
                  {t("login.oidcWith", { name: providerLabel(provider, t) })}
                </Button>
              ))}
            </>
          )}
        </div>
      </div>
    </>
  );
}
