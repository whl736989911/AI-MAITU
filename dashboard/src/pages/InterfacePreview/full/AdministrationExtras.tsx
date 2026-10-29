import { useState } from "react";
import { Alert, Button, Card, Input, Modal, Select, Space, Table } from "antd";
import { useTranslation } from "react-i18next";
import type { DemoField, DemoRow } from "./contract";
import {
  DemoBoundary,
  DemoCollection,
  DemoExternalAction,
  DemoSettings,
} from "./DemoKit";
import { useDemoState } from "./DemoKit";

const P = "interfacePreview.full.admin";
type T = (key: string) => string;
function f(
  t: T,
  names: string[],
  types: Record<string, DemoField["kind"]> = {},
): DemoField[] {
  return names.map((name) => ({
    name,
    label: t(`${P}.field.${name}`),
    kind:
      types[name] ||
      (name === "enabled" ||
      name === "scheduled" ||
      name === "includeConfig" ||
      name === "includeWorkspaces" ||
      name === "includePlugins" ||
      name === "includeKnowledge" ||
      name === "includeChats" ||
      name === "staging" ||
      name === "autoRenew" ||
      name === "networkAccess" ||
      name === "scanOnUpload" ||
      name === "allowRead" ||
      name === "allowWrite"
        ? "switch"
        : name === "retention" ||
          name === "timeout" ||
          name === "maxAttempts" ||
          name === "topK"
        ? "number"
        : [
            "rules",
            "allowedPaths",
            "deniedPaths",
            "maskPatterns",
            "notes",
            "blockedPatterns",
          ].includes(name)
        ? "textarea"
        : "text"),
    ...(name === "schedule"
      ? {
          options: ["daily", "weekly", "every12hours", "custom"].map(
            (value) => ({ value, label: t(`${P}.option.${value}`) }),
          ),
        }
      : name === "channel"
      ? {
          options: ["stable", "beta", "manual"].map((value) => ({
            value,
            label: t(`${P}.option.${value}`),
          })),
        }
      : name === "theme"
      ? {
          options: ["system", "light", "dark"].map((value) => ({
            value,
            label: t(`${P}.option.${value}`),
          })),
        }
      : name === "captchaProvider"
      ? {
          options: ["slider", "turnstile", "geetest", "tencent"].map(
            (value) => ({ value, label: t(`${P}.option.${value}`) }),
          ),
        }
      : name === "mode" || name === "policy"
      ? {
          options: ["strict", "balanced", "permissive"].map((value) => ({
            value,
            label: t(`${P}.option.${value}`),
          })),
        }
      : {}),
  }));
}
const sampleEnv: DemoRow[] = [
  {
    id: "env-1",
    name: "LOG_LEVEL",
    description: "示例日志级别",
    enabled: true,
    values: { value: "info", scope: "service" },
  },
  {
    id: "env-2",
    name: "MAX_UPLOAD_MB",
    description: "示例上传上限",
    enabled: true,
    values: { value: "100", scope: "control" },
  },
];
const external = (t: T, key: string) => (
  <DemoExternalAction
    label={t(`${P}.external.${key}`)}
    detail={t(`${P}.external.${key}Detail`)}
  />
);

export function AdvancedContent({ tab }: { tab: string }) {
  const { t: raw } = useTranslation();
  const t: T = (key) => {
    const advancedDetails = [
      "envVarsDesc",
      "observabilityLangfuse",
      "observabilityDesc",
      "secretKeyHelp",
      "noRealSecret",
      "httpsDesc",
      "bwrapDesc",
      "licenseStatus",
      "licenseDemoStatus",
    ];
    if (
      key.startsWith(`${P}.advanced.`) &&
      advancedDetails.includes(key.slice(`${P}.advanced.`.length))
    )
      return raw(key.replace(`${P}.advanced.`, `${P}.advancedDetails.`));
    if (key === `${P}.external.testObservability`)
      return raw(`${P}.externalObservability.test`);
    if (key === `${P}.external.testObservabilityDetail`)
      return raw(`${P}.externalObservability.testDetail`);
    return raw(key);
  };
  const title = t(`${P}.advanced.${tab}`);
  const [restoreFile, setRestoreFile] = useState<File | null>(null);
  const [restoreConfirm, setRestoreConfirm] = useState(false);
  const [restoreOutcome, setRestoreOutcome] = useState<
    "idle" | "valid" | "invalid"
  >("idle");
  const [httpsScenario, setHttpsScenario] = useState("notConfigured");
  const [licenseScenario, setLicenseScenario] = useState("activeSample");
  const [updateScenario, setUpdateScenario] = useState("availableSample");
  if (tab === "env-vars")
    return (
      <DemoCollection
        storageKey="admin.advanced.envVars"
        title={title}
        description={t(`${P}.advanced.envVarsDesc`)}
        initialRows={sampleEnv}
        fields={f(t, ["name", "value", "scope", "description", "enabled"])}
        canToggle
      />
    );
  if (tab === "observability")
    return (
      <Card title={t(`${P}.advanced.observabilityLangfuse`)}>
        <DemoSettings
          storageKey="admin.advanced.observability"
          title={title}
          description={t(`${P}.advanced.observabilityDesc`)}
          fields={[
            ...f(t, ["enabled", "host", "publicKey"]),
            {
              name: "secretKey",
              label: t(`${P}.field.secretKey`),
              kind: "password",
              help: t(`${P}.advanced.secretKeyHelp`),
              initialValue: "",
            },
          ]}
          initialValues={{
            enabled: false,
            host: "https://cloud.langfuse.com",
            publicKey: "pk-lf-example",
            secretKey: "",
          }}
        />
        <DemoBoundary>{t(`${P}.advanced.noRealSecret`)}</DemoBoundary>
        {external(t, "testObservability")}
      </Card>
    );
  if (tab === "backup")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.advanced.backup"
          fields={f(t, [
            "enabled",
            "schedule",
            "retention",
            "includeConfig",
            "includeWorkspaces",
            "includePlugins",
            "includeKnowledge",
            "includeChats",
          ])}
          initialValues={{
            enabled: true,
            schedule: "daily",
            retention: 7,
            includeConfig: true,
            includeWorkspaces: true,
            includePlugins: true,
            includeKnowledge: true,
            includeChats: false,
          }}
        />
        <Space wrap>
          {external(t, "backup")}
          {external(t, "restore")}
        </Space>
        <Space direction="vertical" style={{ width: "100%", marginTop: 12 }}>
          <Input
            type="file"
            accept=".json,application/json"
            onChange={(event) => {
              setRestoreFile(event.target.files?.[0] || null);
              setRestoreOutcome("idle");
            }}
          />
          <Button
            disabled={!restoreFile}
            onClick={() => setRestoreConfirm(true)}
          >
            {t(`${P}.advanced.restorePreview`)}
          </Button>
          {restoreOutcome === "valid" && (
            <Alert
              type="info"
              showIcon
              message={t(`${P}.advanced.restoreFileValid`)}
            />
          )}
          {restoreOutcome === "invalid" && (
            <Alert
              type="error"
              showIcon
              message={t(`${P}.advanced.restoreFileInvalid`)}
            />
          )}
        </Space>
        <Modal
          title={t(`${P}.advanced.restoreConfirmTitle`)}
          open={restoreConfirm}
          onCancel={() => setRestoreConfirm(false)}
          onOk={async () => {
            if (!restoreFile) return;
            try {
              JSON.parse(await restoreFile.text());
              setRestoreOutcome("valid");
            } catch {
              setRestoreOutcome("invalid");
            }
            setRestoreConfirm(false);
          }}
        >
          <Alert
            type="warning"
            showIcon
            message={t(`${P}.advanced.restorePreviewOnly`)}
          />
        </Modal>
      </Card>
    );
  if (tab === "https")
    return (
      <Card title={title}>
        <Select
          aria-label={t(`${P}.advanced.httpsScenarioLabel`)}
          value={httpsScenario}
          onChange={setHttpsScenario}
          options={[
            "notConfigured",
            "certificateReady",
            "expiring",
            "invalid",
            "permission",
          ].map((value) => ({
            value,
            label: t(`${P}.advanced.httpsScenario.${value}`),
          }))}
          style={{ width: 280, marginBottom: 12 }}
        />
        <Alert
          type={
            httpsScenario === "invalid"
              ? "error"
              : httpsScenario === "permission"
              ? "warning"
              : "info"
          }
          showIcon
          message={t(`${P}.advanced.httpsState.${httpsScenario}`)}
        />
        <DemoSettings
          storageKey="admin.advanced.https"
          description={t(`${P}.advanced.httpsDesc`)}
          fields={f(t, ["enabled", "domain", "contactEmail", "staging"])}
          initialValues={{
            enabled: false,
            domain: "app.example.test",
            contactEmail: "admin@example.test",
            staging: true,
          }}
        />
        <Space wrap>
          {external(t, "tls")}
          {external(t, "restart")}
        </Space>
      </Card>
    );
  if (tab === "captcha")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.advanced.captcha"
          fields={f(t, ["enabled", "captchaProvider", "siteKey", "strongMode"])}
          initialValues={{
            enabled: true,
            captchaProvider: "slider",
            siteKey: "example-site-key",
            strongMode: false,
          }}
        />
      </Card>
    );
  if (tab === "branding")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.advanced.branding"
          fields={f(t, [
            "brandName",
            "logoUrl",
            "loginSubtitle",
            "theme",
            "accentColor",
          ])}
          initialValues={{
            brandName: "Demo Workspace",
            logoUrl: "https://assets.example.test/logo.svg",
            loginSubtitle: "A sample workspace",
            theme: "system",
            accentColor: "#1677ff",
          }}
        />
      </Card>
    );
  if (tab === "bwrap")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.advanced.bwrap"
          description={t(`${P}.advanced.bwrapDesc`)}
          fields={f(t, [
            "enabled",
            "mode",
            "allowedPaths",
            "deniedPaths",
            "networkAccess",
            "timeout",
          ])}
          initialValues={{
            enabled: true,
            mode: "strict",
            allowedPaths: "/workspace/output/**",
            deniedPaths: "/etc/**\n~/.ssh/**",
            networkAccess: false,
            timeout: 60,
          }}
        />
      </Card>
    );
  if (tab === "license")
    return (
      <Card title={title}>
        <Select
          aria-label={t(`${P}.advanced.licenseScenarioLabel`)}
          value={licenseScenario}
          onChange={setLicenseScenario}
          options={[
            "activeSample",
            "expiredSample",
            "unlicensed",
            "renewalUnavailable",
          ].map((value) => ({
            value,
            label: t(`${P}.advanced.licenseScenario.${value}`),
          }))}
          style={{ width: 280, marginBottom: 12 }}
        />
        <Alert
          type={
            licenseScenario === "expiredSample" ||
            licenseScenario === "unlicensed"
              ? "warning"
              : "info"
          }
          showIcon
          message={t(`${P}.advanced.licenseState.${licenseScenario}`)}
        />
        <Alert
          type="info"
          showIcon
          message={t(`${P}.advanced.licenseStatus`)}
          description={t(`${P}.advanced.licenseDemoStatus`)}
        />
        <DemoSettings
          storageKey="admin.advanced.license"
          fields={f(t, [
            "licenseId",
            "licenseEdition",
            "licenseExpires",
            "autoRenew",
          ])}
          initialValues={{
            licenseId: "DEMO-LICENSE-0001",
            licenseEdition: "Team preview",
            licenseExpires: "2027-10-26",
            autoRenew: false,
          }}
        />
        {external(t, "license")}
      </Card>
    );
  if (tab === "updates")
    return (
      <Card title={title}>
        <Select
          aria-label={t(`${P}.advanced.updateScenarioLabel`)}
          value={updateScenario}
          onChange={setUpdateScenario}
          options={[
            "availableSample",
            "current",
            "downloadingSample",
            "failed",
            "permission",
          ].map((value) => ({
            value,
            label: t(`${P}.advanced.updateScenario.${value}`),
          }))}
          style={{ width: 280, marginBottom: 12 }}
        />
        <Alert
          type={
            updateScenario === "failed"
              ? "error"
              : updateScenario === "permission"
              ? "warning"
              : "info"
          }
          showIcon
          message={t(`${P}.advanced.updateState.${updateScenario}`)}
        />
        <DemoSettings
          storageKey="admin.advanced.updates"
          fields={f(t, [
            "channel",
            "currentVersion",
            "targetVersion",
            "enabled",
          ])}
          initialValues={{
            channel: "stable",
            currentVersion: "2.6.0",
            targetVersion: "2.7.0-example",
            enabled: false,
          }}
        />
        {external(t, "upgrade")}
      </Card>
    );
  return (
    <Card title={title}>
      <DemoSettings
        storageKey={`admin.advanced.${tab}`}
        fields={f(t, ["enabled", "retention", "notes"])}
        initialValues={{
          enabled: true,
          retention: 30,
          notes: "Example diagnostics configuration",
        }}
      />
    </Card>
  );
}

const auditRows = [
  {
    key: "1",
    time: "2026-10-26 09:41",
    actor: "林晓",
    event: "策略已保存（示例）",
    target: "安全配置",
  },
  {
    key: "2",
    time: "2026-10-25 16:08",
    actor: "周宁",
    event: "登录成功（示例）",
    target: "OIDC",
  },
  {
    key: "3",
    time: "2026-10-24 14:20",
    actor: "陈可",
    event: "下载文档（示例）",
    target: "员工手册",
  },
];
export function SecurityContent({ tab }: { tab: string }) {
  const { t: raw } = useTranslation();
  const t: T = (key) => {
    const securityDetails = [
      "hitlDesc",
      "filesystemDesc",
      "toolGuardRules",
      "skillScanDesc",
    ];
    if (
      key.startsWith(`${P}.security.`) &&
      securityDetails.includes(key.slice(`${P}.security.`.length))
    )
      return raw(key.replace(`${P}.security.`, `${P}.securityDetails.`));
    return raw(key);
  };
  const [query, setQuery] = useState("");
  const [hitlTools, setHitlTools] = useDemoState<string[]>(
    "admin.security.hitl-tools",
    ["shell"],
  );
  const title = t(`${P}.security.${tab}`);
  if (tab === "audit")
    return (
      <Card title={title}>
        <Space style={{ width: "100%" }}>
          <Input.Search
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t(`${P}.audit.search`)}
            allowClear
          />
        </Space>
        <Table
          pagination={{ pageSize: 5 }}
          dataSource={auditRows.filter((row) =>
            Object.values(row).some((value) =>
              String(value).toLowerCase().includes(query.toLowerCase()),
            ),
          )}
          columns={["time", "actor", "event", "target"].map((name) => ({
            title: t(`${P}.audit.${name}`),
            dataIndex: name,
          }))}
        />
      </Card>
    );
  if (tab === "hitl")
    return (
      <Card title={title}>
        <Select
          aria-label={t(`${P}.field.toolCategories`)}
          mode="multiple"
          value={hitlTools}
          onChange={setHitlTools}
          options={["filesystem", "shell", "network", "all"].map((value) => ({
            value,
            label: t(`${P}.option.${value}`),
          }))}
          style={{ width: "100%", marginBottom: 16 }}
          placeholder={t(`${P}.field.toolCategories`)}
        />
        <DemoSettings
          storageKey="admin.security.hitl"
          description={t(`${P}.security.hitlDesc`)}
          fields={f(t, ["enabled", "mode", "timeout", "requireApprovalFor"])}
          initialValues={{
            enabled: true,
            mode: "strict",
            timeout: 300,
            requireApprovalFor: "write operations",
          }}
        />
      </Card>
    );
  if (tab === "filesystem")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.security.filesystem"
          description={t(`${P}.security.filesystemDesc`)}
          fields={f(t, [
            "policy",
            "allowedPaths",
            "deniedPaths",
            "allowRead",
            "allowWrite",
          ])}
          initialValues={{
            policy: "allowlist",
            allowedPaths: "/workspace/**\n/tmp/agent-output/**",
            deniedPaths: "/etc/**\n~/.ssh/**",
            allowRead: true,
            allowWrite: false,
          }}
        />
      </Card>
    );
  if (tab === "pii")
    return (
      <Card title={title}>
        <DemoSettings
          storageKey="admin.security.pii"
          fields={[
            ...f(t, ["enabled", "mode", "maskPatterns"]),
            {
              name: "maskEmail",
              label: t(`${P}.field.maskEmail`),
              kind: "switch",
              initialValue: true,
            },
            {
              name: "maskPhone",
              label: t(`${P}.field.maskPhone`),
              kind: "switch",
              initialValue: true,
            },
            {
              name: "maskId",
              label: t(`${P}.field.maskId`),
              kind: "switch",
              initialValue: true,
            },
          ]}
          initialValues={{
            enabled: true,
            mode: "balanced",
            maskPatterns: "email\nphone\nidentity number",
            maskEmail: true,
            maskPhone: true,
            maskId: true,
          }}
        />
      </Card>
    );
  if (tab === "tool_guard")
    return (
      <Card title={title}>
        <DemoCollection
          storageKey="admin.security.toolRules"
          title={t(`${P}.security.toolGuardRules`)}
          initialRows={[
            {
              id: "rule-1",
              name: "Block destructive shell",
              description: "Require approval before destructive commands",
              enabled: true,
              values: {
                tool: "shell",
                matcher: "rm -rf",
                action: "require approval",
              },
            },
            {
              id: "rule-2",
              name: "Restrict outbound network",
              description: "Deny unapproved destination hosts",
              enabled: true,
              values: {
                tool: "http_request",
                matcher: "*.example.invalid",
                action: "deny",
              },
            },
          ]}
          fields={f(t, [
            "name",
            "tool",
            "matcher",
            "action",
            "description",
            "enabled",
          ])}
          canToggle
        />
      </Card>
    );
  return (
    <Card title={title}>
      <DemoSettings
        storageKey="admin.security.skillScan"
        description={t(`${P}.security.skillScanDesc`)}
        fields={f(t, [
          "enabled",
          "scanOnUpload",
          "mode",
          "maxAttempts",
          "blockedPatterns",
        ])}
        initialValues={{
          enabled: true,
          scanOnUpload: true,
          mode: "strict",
          maxAttempts: 3,
          blockedPatterns: "credential theft\nremote code download",
        }}
      />
    </Card>
  );
}
