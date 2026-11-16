import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { Spin } from "antd";
import {
  AUTH_IDENTITY_CHANGED_EVENT,
  clearAuthToken,
  getAuthToken,
  isSameAuthIdentity,
  serverNowMs,
} from "../api/request";
import { authApi, type OctopUser } from "../api/modules/auth";
import { applyUserLocale } from "../utils/locale";
import { isNetworkFetchError } from "../utils/networkError";
import { CurrentUserProvider } from "../hooks/useCurrentUser";
import BootOfflinePanel from "./BootOfflinePanel";

interface AuthGuardProps {
  children: React.ReactNode;
}

/**
 * Gate every protected route on (a) the initial admin existing and
 * (b) a valid JWT in localStorage. Octop always requires auth — there is
 * no "password protection disabled" mode like finnie had.
 *
 * Always wait for ``/api/setup/status`` (and ``/auth/me`` when a token
 * exists) before mounting children. Optimistic shell render with a stale
 * token would fire ``/api/agents`` / capabilities / update probes and get
 * 503'd by setup lockdown on first boot.
 *
 * When unauthenticated we must NOT render children: MainLayout / AgentProvider
 * would fire authenticated APIs, trip the 401 interceptor, and race the
 * navigate back to ``/login``.
 */
export default function AuthGuard({ children }: AuthGuardProps) {
  const navigate = useNavigate();
  const navigateRef = useRef(navigate);
  navigateRef.current = navigate;
  const [checking, setChecking] = useState(true);
  const [authed, setAuthed] = useState(false);
  const [user, setUser] = useState<OctopUser | null>(null);
  const [offline, setOffline] = useState(false);
  const [retryKey, setRetryKey] = useState(0);

  const authGeneration = useRef(0);

  useEffect(() => {
    const handleIdentityChanged = () => {
      authGeneration.current += 1;
      setUser(null);
      setAuthed(false);
      setOffline(false);
      setChecking(true);
      setRetryKey((key) => key + 1);
    };
    window.addEventListener(AUTH_IDENTITY_CHANGED_EVENT, handleIdentityChanged);
    return () =>
      window.removeEventListener(
        AUTH_IDENTITY_CHANGED_EVENT,
        handleIdentityChanged,
      );
  }, []);

  useEffect(() => {
    let cancelled = false;
    const generation = authGeneration.current;
    const isCurrent = () => !cancelled && generation === authGeneration.current;

    const check = async () => {
      try {
        const status = await authApi.getAuthStatus();
        if (!isCurrent()) return;

        // No admin yet → push to setup wizard; drop any leftover JWT from a
        // previous install so background prefetch cannot stampede lockdown.
        if (status.setup_required) {
          if (getAuthToken()) clearAuthToken();
          if (!cancelled) navigateRef.current("/setup", { replace: true });
          return;
        }

        // Setup done. Need a token.
        const token = getAuthToken();
        if (!token) {
          if (isCurrent()) {
            setAuthed(false);
            // Stay on the spinner until navigation away completes — do not
            // flip ``checking`` off or children would mount and 401→/login.
            navigateRef.current("/login", { replace: true });
          }
          return;
        }

        // Validate /auth/me. A current-token 401 is handled below; stale-token
        // failures are retried against the currently stored same-account token.
        try {
          const me = await authApi.me();
          if (!isCurrent() || !isSameAuthIdentity(token, getAuthToken()))
            return;
          await applyUserLocale(me.locale);
          if (isCurrent() && isSameAuthIdentity(token, getAuthToken())) {
            setUser(me);
            setAuthed(true);
            setChecking(false);
          }
        } catch (err) {
          if (!isCurrent()) return;
          const currentToken = getAuthToken();
          if (!isSameAuthIdentity(token, currentToken)) return;
          if (token !== currentToken) {
            setUser(null);
            setAuthed(false);
            setOffline(false);
            setChecking(true);
            setRetryKey((key) => key + 1);
            return;
          }
          if (isNetworkFetchError(err)) {
            setOffline(true);
            setChecking(false);
            return;
          }
          setAuthed(false);
          navigateRef.current("/login", { replace: true });
        }
      } catch {
        if (!isCurrent()) return;
        setOffline(true);
        setChecking(false);
      }
    };

    void check();
    return () => {
      cancelled = true;
    };
  }, [retryKey]);

  // A permission change made by an administrator must reach an already-open tab
  // when the account returns to it; the initial /auth/me is not enough.
  useEffect(() => {
    if (!authed) return;
    let cancelled = false;
    const generation = authGeneration.current;
    const refreshUser = () => {
      const token = getAuthToken();
      if (!token) return;
      void authApi
        .me()
        .then((me) => {
          if (
            !cancelled &&
            generation === authGeneration.current &&
            isSameAuthIdentity(token, getAuthToken())
          ) {
            setUser(me);
          }
        })
        .catch(() => undefined);
    };
    window.addEventListener("focus", refreshUser);
    return () => {
      cancelled = true;
      window.removeEventListener("focus", refreshUser);
    };
  }, [authed]);

  useEffect(() => {
    if (!authed || user?.access_expires_at == null) return;
    let cancelled = false;
    const generation = authGeneration.current;
    const token = getAuthToken();
    let timer: number;
    const schedule = () => {
      const delay = Math.max(0, user.access_expires_at! * 1000 - serverNowMs());
      timer = window.setTimeout(
        () => {
          if (delay > 2_147_000_000) {
            schedule();
            return;
          }
          void authApi
            .me()
            .then((me) => {
              if (
                !cancelled &&
                generation === authGeneration.current &&
                isSameAuthIdentity(token, getAuthToken())
              ) {
                setUser(me);
              }
            })
            .catch(() => undefined);
        },
        Math.min(delay, 2_147_000_000),
      );
    };
    schedule();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [authed, user?.access_expires_at]);

  if (offline) {
    return (
      <BootOfflinePanel
        onRetry={() => {
          setChecking(true);
          setAuthed(false);
          setOffline(false);
          setRetryKey((key) => key + 1);
        }}
      />
    );
  }

  if (checking || !authed) {
    return (
      <div
        style={{
          height: "100dvh",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          background: "var(--fn-bg-layout)",
        }}
      >
        <Spin size="large" />
      </div>
    );
  }

  return (
    <CurrentUserProvider user={user} setUser={setUser}>
      {children}
    </CurrentUserProvider>
  );
}
