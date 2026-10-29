import React from "react";
import ReactDOM from "react-dom/client";
import { ConfigProvider } from "antd";
import zhCN from "antd/locale/zh_CN";
import { createInstance } from "i18next";
import { I18nextProvider, initReactI18next } from "react-i18next";
import zh from "./locales/zh.json";
import en from "./locales/en.json";
import InterfacePreview from "./pages/InterfacePreview";
import "./pages/InterfacePreview/preview.css";
import "katex/dist/katex.min.css";
import PreviewEnvironment from "./pages/InterfacePreview/full/PreviewEnvironment";
import { installPreviewTransport } from "./pages/InterfacePreview/full/previewTransport";

async function mountPreview() {
  const i18n = createInstance();
  await i18n.use(initReactI18next).init({
    resources: { zh: { translation: zh }, en: { translation: en } },
    lng: "zh",
    fallbackLng: "en",
    supportedLngs: ["zh", "en"],
    interpolation: { escapeValue: false },
  });
  installPreviewTransport(() => i18n.language);
  const root = document.getElementById("interface-preview-root");
  if (!root) throw new Error("Missing interface preview root");
  ReactDOM.createRoot(root).render(
    <React.StrictMode>
      <I18nextProvider i18n={i18n}>
        <ConfigProvider
          locale={zhCN}
          theme={{
            token: {
              colorPrimary: "#e5484d",
              borderRadius: 12,
              fontFamily: '"Segoe UI", "Microsoft YaHei", sans-serif',
            },
          }}
        >
          <PreviewEnvironment>
            <InterfacePreview />
          </PreviewEnvironment>
        </ConfigProvider>
      </I18nextProvider>
    </React.StrictMode>,
  );
}

void mountPreview();
