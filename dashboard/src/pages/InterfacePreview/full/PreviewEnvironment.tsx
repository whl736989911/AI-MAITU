import { useEffect, useState, type ReactNode } from "react";
import { App, Alert, Modal, Typography } from "antd";
import { MemoryRouter, useLocation } from "react-router-dom";
import { useTranslation } from "react-i18next";
import { CurrentUserProvider } from "../../../hooks/useCurrentUser";
import { VoiceOutputProvider } from "../../../context/VoiceOutputContext";
import type { OctopUser } from "../../../api/modules/auth";
import {
  PREVIEW_BLOCKED_EVENT,
  type PreviewBlockedOperation,
} from "./previewTransport";

export const PREVIEW_NAVIGATE_EVENT = "maitu:preview-navigate";

function RouterBridge() {
  const location = useLocation();
  useEffect(() => {
    if (location.pathname === "/") return;
    window.dispatchEvent(
      new CustomEvent(PREVIEW_NAVIGATE_EVENT, {
        detail: { path: location.pathname },
      }),
    );
  }, [location.key, location.pathname]);
  return null;
}

function PreviewBoundary() {
  const { t } = useTranslation();
  const [operation, setOperation] = useState<PreviewBlockedOperation | null>(
    null,
  );
  useEffect(() => {
    const handler = (event: Event) =>
      setOperation((event as CustomEvent<PreviewBlockedOperation>).detail);
    window.addEventListener(PREVIEW_BLOCKED_EVENT, handler);
    return () => window.removeEventListener(PREVIEW_BLOCKED_EVENT, handler);
  }, []);
  return (
    <Modal
      open={operation !== null}
      title={t("interfacePreview.full.common.externalOnly")}
      onCancel={() => setOperation(null)}
      onOk={() => setOperation(null)}
      okText={t("interfacePreview.full.common.understood")}
      cancelButtonProps={{ style: { display: "none" } }}
    >
      <Alert
        showIcon
        type="info"
        message={t("interfacePreview.full.common.noExternalExecution")}
      />
      {operation && (
        <Typography.Paragraph style={{ marginTop: 16 }}>
          <Typography.Text code>
            {operation.method} {operation.path}
          </Typography.Text>
        </Typography.Paragraph>
      )}
    </Modal>
  );
}

export default function PreviewEnvironment({
  children,
}: {
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [user, setUser] = useState<OctopUser | null>(() => ({
    id: 1,
    username: "preview-user",
    role: "admin",
    display_name: t("interfacePreview.sampleUser"),
    locale: "zh",
  }));
  return (
    <MemoryRouter>
      <CurrentUserProvider user={user} setUser={setUser}>
        <App>
          <VoiceOutputProvider>
            <RouterBridge />
            {children}
            <PreviewBoundary />
          </VoiceOutputProvider>
        </App>
      </CurrentUserProvider>
    </MemoryRouter>
  );
}
