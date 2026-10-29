import { useState } from "react";
import {
  Alert,
  Button,
  Card,
  Divider,
  Select,
  Space,
  Tabs,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import type { DemoField } from "./contract";
import { DemoExternalAction, DemoSettings } from "./DemoKit";
import styles from "./AdministrationPages.module.less";

const P = "interfacePreview.full.admin";
export function PublicAccessPage({
  path,
  tab,
  titleKey,
  onBack,
  navigate,
}: {
  path: string;
  tab?: string;
  titleKey?: string;
  onBack: () => void;
  navigate: (path: string, tab?: string, item?: string) => void;
}) {
  const { t } = useTranslation();
  const tx = (key: string) => {
    if (key.startsWith("public.scenario."))
      return t(
        `${P}.publicFlow.scenario.${key.slice("public.scenario.".length)}`,
      );
    const flowKeys = [
      "setupAdminStep",
      "setupDatabaseStep",
      "setupModelStep",
      "setupAdmin",
      "setupDatabase",
      "setupModel",
      "oidcPending",
      "noOidcExchange",
      "callbackCodeLabel",
      "callbackStateLabel",
      "scenarioLabel",
      "validationBranch",
      "unavailableBranch",
      "loginFactors",
      "loginFactorBoundary",
      "captchaState",
      "captchaLoading",
      "captchaFailed",
      "captchaReset",
      "ssoState",
      "ssoProvidersSample",
      "ssoDiscovering",
      "ssoPopupBlocked",
      "ssoFailed",
      "ssoStart",
      "ssoBoundary",
      "oidcState",
      "setupFinishStep",
      "setupFinish",
      "setupFinishBoundary",
    ];
    const flowPrefixes = ["captcha.", "sso.", "oidcState.", "oidcMessage."];
    if (
      key.startsWith("public.") &&
      (flowKeys.includes(key.slice("public.".length)) ||
        flowPrefixes.some((prefix) =>
          key.slice("public.".length).startsWith(prefix),
        ))
    )
      return t(`${P}.publicFlow.${key.slice("public.".length)}`);
    return t(`${P}.${key}`);
  };
  const [submitted, setSubmitted] = useState(false);
  const [scenario, setScenario] = useState("ready");
  const [captchaScenario, setCaptchaScenario] = useState("ready");
  const [ssoScenario, setSsoScenario] = useState("idle");
  const [oidcScenario, setOidcScenario] = useState("previewReady");
  const scenarioKeys = ["ready", "validation", "unavailable"] as const;
  const scenarioControl = (
    <Select
      aria-label={tx("public.scenarioLabel")}
      value={scenario}
      onChange={(value) => {
        setScenario(value);
        setSubmitted(false);
      }}
      options={scenarioKeys.map((value) => ({
        value,
        label: tx(`public.scenario.${value}`),
      }))}
      style={{ width: 230, marginBottom: 12 }}
    />
  );
  const fields = (names: string[]): DemoField[] =>
    names.map((name) => ({
      name,
      label: tx(`field.${name}`),
      kind: ["password", "passwordConfirm"].includes(name)
        ? "password"
        : ["remember", "enabled", "useExistingDatabase"].includes(name)
        ? "switch"
        : ["databaseDriver", "modelProvider"].includes(name)
        ? "select"
        : name === "notes"
        ? "textarea"
        : "text",
      ...(name === "databaseDriver"
        ? {
            options: ["sqlite", "postgresql"].map((value) => ({
              value,
              label: tx(`option.${value}`),
            })),
          }
        : name === "modelProvider"
        ? {
            options: ["compatible", "local"].map((value) => ({
              value,
              label: tx(`option.${value}`),
            })),
          }
        : {}),
    }));
  const successNotice = submitted ? (
    <Alert type="info" showIcon message={tx("public.previewSubmitted")} />
  ) : null;
  const localForm = (key: string, heading: string, names: string[]) => (
    <Card title={tx(heading)}>
      {scenarioControl}
      {scenario === "validation" && (
        <Alert
          type="warning"
          showIcon
          message={tx("public.validationBranch")}
        />
      )}
      {scenario === "unavailable" && (
        <Alert type="error" showIcon message={tx("public.unavailableBranch")} />
      )}
      {successNotice}
      <DemoSettings
        storageKey={`admin.public.${path}.${key}`}
        fields={fields(names)}
        initialValues={{
          email: "person@example.test",
          username: "demo-user",
          displayName: "示例用户",
          remember: false,
          enabled: false,
          databaseDriver: "sqlite",
          databaseName: "workspace-demo.db",
          adminUsername: "admin-demo",
          modelProvider: "compatible",
          modelName: "chat-example",
          inviteCode: "INVITE-DEMO-2026",
          verificationCode: "123456",
        }}
      />
      <Button onClick={() => setSubmitted(scenario === "ready")}>
        {tx("action.previewForm")}
      </Button>
    </Card>
  );
  const loginSupport = (
    <Card title={tx("public.loginFactors")}>
      <Typography.Paragraph>
        {tx("public.loginFactorBoundary")}
      </Typography.Paragraph>
      <Typography.Text>{tx("public.captchaState")}</Typography.Text>
      <Select
        aria-label={tx("public.captchaState")}
        value={captchaScenario}
        onChange={setCaptchaScenario}
        options={["ready", "loading", "failed", "reset"].map((value) => ({
          value,
          label: tx(`public.captcha.${value}`),
        }))}
        style={{ display: "block", width: 260, margin: "8px 0 16px" }}
      />
      {captchaScenario === "loading" && (
        <Alert type="info" showIcon message={tx("public.captchaLoading")} />
      )}
      {captchaScenario === "failed" && (
        <Alert
          type="error"
          showIcon
          message={tx("public.captchaFailed")}
          action={
            <Button size="small" onClick={() => setCaptchaScenario("ready")}>
              {tx("action.retry")}
            </Button>
          }
        />
      )}
      {captchaScenario === "reset" && (
        <Alert type="warning" showIcon message={tx("public.captchaReset")} />
      )}
      <Divider />
      <Typography.Text>{tx("public.ssoState")}</Typography.Text>
      <Select
        aria-label={tx("public.ssoState")}
        value={ssoScenario}
        onChange={setSsoScenario}
        options={[
          "idle",
          "discovering",
          "providers",
          "popupBlocked",
          "failed",
        ].map((value) => ({ value, label: tx(`public.sso.${value}`) }))}
        style={{ display: "block", width: 260, margin: "8px 0 16px" }}
      />
      {ssoScenario === "providers" && (
        <Alert type="info" showIcon message={tx("public.ssoProvidersSample")} />
      )}
      {ssoScenario === "discovering" && (
        <Alert type="info" showIcon message={tx("public.ssoDiscovering")} />
      )}
      {ssoScenario === "popupBlocked" && (
        <Alert type="warning" showIcon message={tx("public.ssoPopupBlocked")} />
      )}
      {ssoScenario === "failed" && (
        <Alert type="error" showIcon message={tx("public.ssoFailed")} />
      )}
      <DemoExternalAction
        label={tx("public.ssoStart")}
        detail={tx("public.ssoBoundary")}
      />
    </Card>
  );
  const content =
    path === "/login" ? (
      <>
        {localForm("login", "public.login", [
          "username",
          "password",
          "verificationCode",
          "remember",
        ])}
        {loginSupport}
      </>
    ) : path === "/setup" ? (
      <Tabs
        activeKey={tab || "admin"}
        onChange={(next) => navigate(path, next)}
        items={[
          {
            key: "admin",
            label: tx("public.setupAdminStep"),
            children: localForm("setup-admin", "public.setupAdmin", [
              "adminUsername",
              "displayName",
              "password",
              "passwordConfirm",
            ]),
          },
          {
            key: "database",
            label: tx("public.setupDatabaseStep"),
            children: localForm("setup-database", "public.setupDatabase", [
              "databaseDriver",
              "databaseName",
              "useExistingDatabase",
            ]),
          },
          {
            key: "model",
            label: tx("public.setupModelStep"),
            children: localForm("setup-model", "public.setupModel", [
              "modelProvider",
              "endpoint",
              "modelName",
            ]),
          },
          {
            key: "finish",
            label: tx("public.setupFinishStep"),
            children: (
              <Card title={tx("public.setupFinish")}>
                {scenarioControl}
                {scenario === "validation" && (
                  <Alert
                    type="warning"
                    showIcon
                    message={tx("public.validationBranch")}
                  />
                )}
                {scenario === "unavailable" && (
                  <Alert
                    type="error"
                    showIcon
                    message={tx("public.unavailableBranch")}
                  />
                )}
                <Alert
                  style={{ marginBottom: 12 }}
                  type="info"
                  showIcon
                  message={tx("public.setupFinishBoundary")}
                />
                {successNotice}
                <Button
                  disabled={scenario !== "ready"}
                  onClick={() => setSubmitted(true)}
                >
                  {tx("action.previewForm")}
                </Button>
              </Card>
            ),
          },
        ]}
      />
    ) : path === "/invite" ? (
      localForm("invite", "public.invite", [
        "inviteCode",
        "username",
        "displayName",
        "password",
        "passwordConfirm",
      ])
    ) : (
      <Card title={tx("public.oidc")}>
        <Select
          aria-label={tx("public.oidcState")}
          value={oidcScenario}
          onChange={(value) => {
            setOidcScenario(value);
            setSubmitted(false);
          }}
          options={[
            "missingCode",
            "invalidState",
            "unsafeRedirect",
            "loading",
            "failure",
            "popupBlocked",
            "cleanup",
            "previewReady",
          ].map((value) => ({ value, label: tx(`public.oidcState.${value}`) }))}
          style={{ width: 300, marginBottom: 12 }}
        />
        <Alert
          type={
            [
              "missingCode",
              "invalidState",
              "unsafeRedirect",
              "failure",
            ].includes(oidcScenario)
              ? "error"
              : oidcScenario === "popupBlocked"
              ? "warning"
              : "info"
          }
          showIcon
          message={tx(`public.oidcMessage.${oidcScenario}`)}
          description={tx("public.noOidcExchange")}
        />
        {successNotice}
        <Divider />
        <Typography.Paragraph>
          {tx("public.callbackCodeLabel")}:{" "}
          {oidcScenario === "missingCode" ? "—" : "demo-code-example"}
        </Typography.Paragraph>
        <Typography.Paragraph>
          {tx("public.callbackStateLabel")}:{" "}
          {oidcScenario === "invalidState"
            ? "mismatch-preview-only"
            : "state-preview-only"}
        </Typography.Paragraph>
        <Button
          disabled={oidcScenario !== "previewReady"}
          onClick={() => setSubmitted(true)}
        >
          {tx("action.previewForm")}
        </Button>
        <Button onClick={onBack}>{tx("action.backToLogin")}</Button>
      </Card>
    );
  return (
    <PageShell title={t(titleKey || "login.title")}>
      <Card className={styles.publicCard}>
        <Alert type="info" showIcon message={tx("public.localOnly")} />
      </Card>
      <Space direction="vertical" style={{ width: "100%" }}>
        {content}
        <Button onClick={onBack}>{tx("action.backToLogin")}</Button>
      </Space>
    </PageShell>
  );
}
