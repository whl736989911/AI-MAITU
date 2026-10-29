import { useMemo, useState } from "react";
import {
  Alert,
  Button,
  Card,
  Col,
  Descriptions,
  Drawer,
  Input,
  Progress,
  Row,
  Select,
  Space,
  Statistic,
  Table,
  Tabs,
  Tag,
  Tree,
  Typography,
  type TreeDataNode,
} from "antd";
import { useTranslation } from "react-i18next";
import PageShell from "../../../layouts/PageShell";
import {
  PermissionCheckboxPicker,
  type PermissionCatalogItem,
} from "../../../components/PermissionPicker";
import {
  useDemoState,
  DemoBoundary,
  DemoCollection,
  DemoExternalAction,
  DemoSettings,
} from "./DemoKit";
import styles from "./AdministrationPages.module.less";
import { AdvancedContent, SecurityContent } from "./AdministrationExtras";
import { PublicAccessPage } from "./PublicAccessPage";
import type {
  DemoField,
  DemoPageDefinition,
  DemoPageProps,
  DemoRow,
} from "./contract";

const P = "interfacePreview.full.admin";
const fields = (names: string[], t: (key: string) => string): DemoField[] =>
  names.map((name) => {
    const kind =
      name === "enabled" ||
      name === "scheduled" ||
      name === "autoRenew" ||
      name === "scanOnUpload" ||
      name === "allowRead" ||
      name === "allowWrite" ||
      name === "strongMode"
        ? "switch"
        : [
            "notes",
            "rules",
            "allowedPaths",
            "deniedPaths",
            "maskPatterns",
            "permissions",
          ].includes(name)
        ? "textarea"
        : [
            "max_iters",
            "max_input_length",
            "timeout",
            "retention",
            "maxUpload",
            "contextWindow",
            "topK",
            "members",
          ].includes(name)
        ? "number"
        : [
            "role",
            "mode",
            "schedule",
            "channel",
            "theme",
            "captchaProvider",
            "policy",
            "unit",
            "type",
            "visibility",
            "permission",
            "auth",
            "quality",
            "state",
          ].includes(name)
        ? "select"
        : "text";
    const options =
      name === "role"
        ? ["admin", "editor", "member", "viewer"]
        : name === "unit"
        ? ["产品中心", "研发部", "运营部"]
        : name === "mode"
        ? ["strict", "balanced", "permissive"]
        : name === "schedule"
        ? ["daily", "weekly", "every12hours", "custom"]
        : name === "channel"
        ? ["stable", "beta", "manual"]
        : name === "theme"
        ? ["system", "light", "dark"]
        : name === "captchaProvider"
        ? ["slider", "turnstile", "geetest", "tencent"]
        : name === "policy"
        ? ["allowlist", "denylist", "sandbox"]
        : name === "type"
        ? ["local", "s3", "azure", "minio"]
        : name === "visibility"
        ? ["private", "unit", "public"]
        : name === "permission"
        ? ["read", "write"]
        : name === "auth"
        ? ["local", "oidc"]
        : name === "quality"
        ? ["standard", "high"]
        : name === "state"
        ? ["pending", "approved", "rejected"]
        : [];
    return {
      name,
      label: t(`${P}.field.${name}`),
      kind,
      initialValue: kind === "switch" ? true : kind === "number" ? 0 : "",
      ...(options.length
        ? {
            options: options.map((value) => ({
              value,
              label:
                name === "unit"
                  ? t(
                      `${P}.unitNames.${
                        value === "产品中心"
                          ? "product"
                          : value === "研发部"
                          ? "engineering"
                          : "operations"
                      }`,
                    )
                  : t(`${P}.option.${value}`),
            })),
          }
        : {}),
    };
  });
const users: DemoRow[] = [
  {
    id: "u-101",
    name: "林晓",
    description: "平台管理员",
    enabled: true,
    values: {
      email: "lin.xiao@example.test",
      role: "admin",
      unit: "产品中心",
      auth: "local",
      permissions: "用户管理\\n安全策略\\n授权配置",
    },
  },
  {
    id: "u-102",
    name: "周宁",
    description: "知识库维护者",
    enabled: true,
    values: {
      email: "zhou.ning@example.test",
      role: "editor",
      unit: "研发部",
      auth: "oidc",
      permissions: "知识库读写\\n共享申请",
    },
  },
  {
    id: "u-103",
    name: "陈可",
    description: "只读成员",
    enabled: false,
    values: {
      email: "chen.ke@example.test",
      role: "viewer",
      unit: "运营部",
      auth: "local",
      permissions: "仅查看",
    },
  },
];
const demoPermissionCatalog: PermissionCatalogItem[] = [
  {
    key: "personalization",
    category: "settings",
    label: "Personalization",
    page: "personalization",
    page_label: "Personalization",
    can_grant: true,
  },
  {
    key: "models",
    category: "settings",
    label: "Models",
    page: "models",
    page_label: "Models",
    can_grant: true,
  },
  {
    key: "remote-browser",
    category: "control",
    label: "Remote browser",
    page: "browser",
    page_label: "Workbench",
    can_grant: true,
  },
  {
    key: "users",
    category: "admin",
    label: "Users",
    page: "users",
    page_label: "Users",
    can_grant: false,
  },
];
const units: DemoRow[] = [
  {
    id: "u-product",
    name: "产品中心",
    description: "产品与设计协作",
    enabled: true,
    values: { parent: "—", members: 12, roles: "管理员、编辑者" },
  },
  {
    id: "u-engineering",
    name: "研发部",
    description: "工程研发团队",
    enabled: true,
    values: { parent: "u-product", members: 28, roles: "编辑者、成员" },
  },
  {
    id: "u-operations",
    name: "运营部",
    description: "内容与运营",
    enabled: true,
    values: { parent: "—", members: 9, roles: "成员" },
  },
];
const providers: DemoRow[] = [
  {
    id: "p-openai",
    name: "兼容接口示例",
    description: "兼容接口演示配置",
    enabled: true,
    values: {
      endpoint: "https://api.example.test/v1",
      models: "chat-example, embed-example",
      contextWindow: 32000,
      ttsModel: "speech-demo",
      sttModel: "transcription-demo",
      voiceId: "voice-demo",
      imageSize: "1024×1024",
      quality: "standard",
      indexName: "kb-demo",
      topK: 5,
      language: "zh-CN",
    },
  },
  {
    id: "p-local",
    name: "本地推理示例",
    description: "本机模型服务示例端点",
    enabled: false,
    values: {
      endpoint: "http://localhost:11434",
      models: "local-chat-demo",
      contextWindow: 16000,
      ttsModel: "local-speech-demo",
      sttModel: "local-transcription-demo",
      voiceId: "voice-local",
      imageSize: "512×512",
      quality: "standard",
      indexName: "local-index-demo",
      topK: 3,
      language: "zh-CN",
    },
  },
];
const storage: DemoRow[] = [
  {
    id: "s-default",
    name: "默认文件存储",
    description: "平台上传文件",
    enabled: true,
    values: {
      type: "local",
      location: "./data/files",
      used: "18.4 GB / 100 GB",
    },
  },
  {
    id: "s-archive",
    name: "归档对象存储",
    description: "历史文档归档",
    enabled: false,
    values: {
      type: "s3",
      location: "s3://demo-archive",
      used: "6.8 GB / 500 GB",
    },
  },
];
const plugins: DemoRow[] = [
  {
    id: "p-docx",
    name: "Office 文档解析",
    description: "解析 docx 与 xlsx 文件",
    enabled: true,
    values: { version: "1.4.2", source: "内置插件", tools: 3 },
  },
  {
    id: "p-web",
    name: "网页检索连接器",
    description: "为代理提供网页检索工具",
    enabled: false,
    values: { version: "0.9.1", source: "自定义", tools: 2 },
  },
];
const sharingRows: DemoRow[] = [
  {
    id: "kb-handbook",
    name: "员工手册知识库",
    description: "知识库 · 林晓",
    enabled: true,
    values: {
      visibility: "public",
      permission: "read",
      grants: "研发部、运营部",
      status: "已生效",
    },
  },
  {
    id: "agent-support",
    name: "客户支持助手",
    description: "智能体 · 周宁",
    enabled: true,
    values: {
      visibility: "unit",
      permission: "write",
      grants: "运营部",
      status: "待审批",
    },
  },
];
interface UsageRecord {
  day: string;
  expert: string;
  feature: string;
  model: string;
  input: number;
  output: number;
  cost: number;
}
const usageRecords: UsageRecord[] = [
  {
    day: "2026-10-26",
    expert: "知识助手",
    feature: "知识问答",
    model: "chat-example",
    input: 1248320,
    output: 426110,
    cost: 6.82,
  },
  {
    day: "2026-10-26",
    expert: "客户支持助手",
    feature: "工单摘要",
    model: "chat-example",
    input: 404210,
    output: 112840,
    cost: 2.31,
  },
  {
    day: "2026-10-25",
    expert: "客户支持助手",
    feature: "知识问答",
    model: "chat-example",
    input: 902410,
    output: 268402,
    cost: 5.14,
  },
  {
    day: "2026-10-25",
    expert: "周报助手",
    feature: "周报生成",
    model: "reasoner-demo",
    input: 348920,
    output: 178310,
    cost: 3.26,
  },
  {
    day: "2026-10-24",
    expert: "知识助手",
    feature: "文档提取",
    model: "chat-example",
    input: 512200,
    output: 186700,
    cost: 2.94,
  },
  {
    day: "2026-10-24",
    expert: "周报助手",
    feature: "周报生成",
    model: "reasoner-demo",
    input: 241000,
    output: 91500,
    cost: 1.88,
  },
  {
    day: "2026-10-23",
    expert: "客户支持助手",
    feature: "工单摘要",
    model: "chat-example",
    input: 396000,
    output: 144000,
    cost: 2.52,
  },
];
function groupUsage(
  by: keyof Pick<UsageRecord, "day" | "expert" | "feature" | "model">,
) {
  const grouped = new Map<
    string,
    { key: string; input: number; output: number; cost: number; count: number }
  >();
  for (const row of usageRecords) {
    const key = row[by];
    const aggregate = grouped.get(key) || {
      key,
      input: 0,
      output: 0,
      cost: 0,
      count: 0,
    };
    aggregate.input += row.input;
    aggregate.output += row.output;
    aggregate.cost += row.cost;
    aggregate.count += 1;
    grouped.set(key, aggregate);
  }
  return [...grouped.values()].sort(
    (left, right) => right.input + right.output - left.input - left.output,
  );
}
const TABS: Record<string, string[]> = {
  "/admin/users": ["local", "feishu", "wecom", "dingtalk", "oidc"],
  "/admin/models": ["chat", "generation", "voice", "search"],
  "/admin/backend": ["my", "types"],
  "/admin/plugins": ["installed", "market"],
  "/admin/security": [
    "hitl",
    "filesystem",
    "pii",
    "tool_guard",
    "skill_scan",
    "audit",
  ],
  "/admin/advanced": [
    "env-vars",
    "observability",
    "backup",
    "https",
    "captcha",
    "branding",
    "bwrap",
    "license",
    "updates",
  ],
  "/sharing": ["queue", "settings"],
};
export const ADMINISTRATION_PAGES: readonly DemoPageDefinition[] = [
  { path: "/token-usage", titleKey: "pageShell.tokenUsage.title" },
  {
    path: "/admin/users",
    titleKey: "pageShell.adminUsers.title",
    tabs: TABS["/admin/users"],
  },
  { path: "/admin/org-units", titleKey: "pageShell.adminOrgUnits.title" },
  {
    path: "/admin/models",
    titleKey: "pageShell.models.title",
    tabs: TABS["/admin/models"],
  },
  {
    path: "/admin/backend",
    titleKey: "pageShell.adminStorage.title",
    tabs: TABS["/admin/backend"],
  },
  {
    path: "/admin/plugins",
    titleKey: "pageShell.adminPlugins.title",
    tabs: TABS["/admin/plugins"],
  },
  {
    path: "/admin/security",
    titleKey: "pageShell.security.title",
    tabs: TABS["/admin/security"],
  },
  {
    path: "/admin/advanced",
    titleKey: "pageShell.adminAdvanced.title",
    tabs: TABS["/admin/advanced"],
  },
  {
    path: "/sharing",
    titleKey: "pageShell.sharing.title",
    tabs: TABS["/sharing"],
  },
  { path: "/agent-config", titleKey: "agentConfig.title" },
  { path: "/pwa-debug", titleKey: "interfacePreview.full.admin.pwa.title" },
  { path: "/login", titleKey: "login.title" },
  { path: "/setup", titleKey: "interfacePreview.full.admin.public.setup" },
  { path: "/invite", titleKey: "invite.title" },
  { path: "/login/oidc/complete", titleKey: "login.title" },
];

function unitSubtreeIds(rows: DemoRow[], rootId: string): Set<string> {
  const ids = new Set([rootId]);
  let expanded = true;
  while (expanded) {
    expanded = false;
    for (const row of rows) {
      if (ids.has(String(row.values?.parent || "")) && !ids.has(row.id)) {
        ids.add(row.id);
        expanded = true;
      }
    }
  }
  return ids;
}

function OrgUnitEditor({ t }: { t: (key: string) => string }) {
  const tx = (key: string) => {
    const [scope, name] = key.split(".", 2);
    const target =
      scope === "units"
        ? `department.${name}`
        : scope === "action" &&
          ["addDepartment", "edit", "delete", "save", "cancel"].includes(name)
        ? `actions.${name}`
        : key;
    return t(`${P}.${target}`);
  };
  const [rows, setRows] = useDemoState<DemoRow[]>("admin.orgUnits.tree", units);
  const [selected, setSelected] = useState("u-product");
  const [editing, setEditing] = useState<string | null>(null);
  const [name, setName] = useState("");
  const [parent, setParent] = useState("—");
  const [description, setDescription] = useState("");
  const [roles, setRoles] = useState("");
  const [grantsOpen, setGrantsOpen] = useState(false);
  const [grantSelection, setGrantSelection] = useState<string[]>([]);
  const [grantsByUnit, setGrantsByUnit] = useDemoState<
    Record<string, string[]>
  >("admin.orgUnit-grants", {});
  const invalidParents = editing
    ? unitSubtreeIds(rows, editing)
    : new Set<string>();
  const children = (parentId: string): TreeDataNode[] =>
    rows
      .filter((row) => String(row.values?.parent || "—") === parentId)
      .map((row) => ({
        key: row.id,
        title: row.name,
        children: children(row.id),
      }));
  const start = (row?: DemoRow) => {
    setEditing(row?.id || "new");
    setName(row?.name || "");
    setParent(String(row?.values?.parent || "—"));
    setDescription(row?.description || "");
    setRoles(String(row?.values?.roles || "editor, member"));
  };
  const save = () => {
    if (!name.trim()) return;
    const id =
      editing === "new"
        ? `unit-${Date.now()}`
        : editing || `unit-${Date.now()}`;
    const row: DemoRow = {
      id,
      name: name.trim(),
      description,
      enabled: true,
      values: {
        parent,
        members:
          editing === "new"
            ? 0
            : Number(rows.find((item) => item.id === id)?.values?.members || 0),
        roles,
      },
    };
    setRows((current) =>
      editing === "new"
        ? [...current, row]
        : current.map((item) => (item.id === id ? row : item)),
    );
    setSelected(id);
    setEditing(null);
  };
  const current = rows.find((row) => row.id === selected);
  return (
    <Row gutter={[16, 16]}>
      <Col xs={24} md={8}>
        <Card
          title={tx("units.tree")}
          extra={
            <Button onClick={() => start()}>
              {tx("action.addDepartment")}
            </Button>
          }
        >
          <Tree
            selectedKeys={[selected]}
            treeData={children("—")}
            onSelect={(keys) => {
              if (keys[0]) setSelected(String(keys[0]));
            }}
          />
        </Card>
      </Col>
      <Col xs={24} md={16}>
        <Card
          title={tx("units.details")}
          extra={
            current ? (
              <Space>
                <Button onClick={() => start(current)}>
                  {tx("action.edit")}
                </Button>
                <Button
                  danger
                  onClick={() => {
                    const removed = unitSubtreeIds(rows, current.id);
                    setRows((value) =>
                      value.filter((row) => !removed.has(row.id)),
                    );
                    setSelected(
                      rows.find((row) => !removed.has(row.id))?.id || "",
                    );
                  }}
                >
                  {tx("action.delete")}
                </Button>
              </Space>
            ) : null
          }
        >
          {editing ? (
            <Space direction="vertical" className={styles.editor}>
              <Input
                value={name}
                placeholder={tx("field.name")}
                onChange={(event) => setName(event.target.value)}
              />
              <Select
                value={parent}
                onChange={setParent}
                options={[
                  { value: "—", label: tx("units.noParent") },
                  ...rows
                    .filter((row) => !invalidParents.has(row.id))
                    .map((row) => ({ value: row.id, label: row.name })),
                ]}
              />
              <Input
                value={description}
                placeholder={tx("field.description")}
                onChange={(event) => setDescription(event.target.value)}
              />
              <Input.TextArea
                rows={3}
                value={roles}
                placeholder={tx("units.authorization")}
                onChange={(event) => setRoles(event.target.value)}
              />
              <Space>
                <Button type="primary" onClick={save}>
                  {tx("action.save")}
                </Button>
                <Button onClick={() => setEditing(null)}>
                  {tx("action.cancel")}
                </Button>
              </Space>
            </Space>
          ) : current ? (
            <>
              <Typography.Title level={4}>{current.name}</Typography.Title>
              <Typography.Paragraph>{current.description}</Typography.Paragraph>
              <Descriptions
                bordered
                size="small"
                items={[
                  {
                    key: "parent",
                    label: tx("field.parent"),
                    children:
                      rows.find((row) => row.id === current.values?.parent)
                        ?.name || tx("units.noParent"),
                  },
                  {
                    key: "members",
                    label: tx("field.members"),
                    children: String(current.values?.members || 0),
                  },
                  {
                    key: "roles",
                    label: tx("units.authorization"),
                    children: String(current.values?.roles || ""),
                  },
                ]}
              />
              <Typography.Title level={5}>
                {tx("units.membersList")}
              </Typography.Title>
              <Table
                size="small"
                pagination={false}
                rowKey="id"
                dataSource={users.filter(
                  (user) => user.values?.unit === current.name,
                )}
                columns={[
                  { title: t(`${P}.field.name`), dataIndex: "name" },
                  {
                    title: t(`${P}.field.email`),
                    render: (_value, row: DemoRow) =>
                      String(row.values?.email || ""),
                  },
                  {
                    title: t(`${P}.field.role`),
                    render: (_value, row: DemoRow) =>
                      String(row.values?.role || ""),
                  },
                ]}
              />
              <Button
                style={{ marginTop: 16 }}
                onClick={() => {
                  setGrantSelection(
                    grantsByUnit[current.id] ?? ["personalization", "models"],
                  );
                  setGrantsOpen(true);
                }}
              >
                {tx("units.authorization")}
              </Button>
              <Drawer
                open={grantsOpen}
                title={tx("units.authorization")}
                onClose={() => setGrantsOpen(false)}
                width={480}
              >
                <PermissionCheckboxPicker
                  catalog={demoPermissionCatalog}
                  value={grantSelection}
                  onChange={setGrantSelection}
                />
                <Button
                  type="primary"
                  style={{ marginTop: 16 }}
                  onClick={() => {
                    setGrantsByUnit((existing) => ({
                      ...existing,
                      [current.id]: grantSelection,
                    }));
                    setGrantsOpen(false);
                  }}
                >
                  {tx("action.save")}
                </Button>
              </Drawer>
            </>
          ) : null}
        </Card>
      </Col>
    </Row>
  );
}
function SharingQueue({ t }: { t: (key: string) => string }) {
  const tx = (key: string) =>
    t(`${P}.${key === "action.rejectLocally" ? "actions.rejectLocally" : key}`);
  const initialRows: DemoRow[] = [
    {
      id: "change-204",
      name: "客户支持助手 → 全组织",
      description: "申请人：周宁 · 2026-10-24",
      enabled: true,
      values: { scope: "组织范围", permission: "write", state: "pending" },
    },
  ];
  const [, setRows] = useDemoState<DemoRow[]>(
    "admin.sharingQueue",
    initialRows,
  );
  const mark = (row: DemoRow, state: string) =>
    setRows((rows) =>
      rows.map((item) =>
        item.id === row.id
          ? { ...item, enabled: false, values: { ...item.values, state } }
          : item,
      ),
    );
  return (
    <DemoCollection
      storageKey="admin.sharingQueue"
      title={tx("sharing.queue")}
      initialRows={initialRows}
      fields={fields(["name", "scope", "permission", "state"], t)}
      canCreate={false}
      rowActions={[
        {
          key: "approve",
          label: tx("action.approveLocally"),
          onClick: (row) => mark(row, "approved"),
        },
        {
          key: "reject",
          label: tx("action.rejectLocally"),
          onClick: (row) => mark(row, "rejected"),
        },
      ]}
    />
  );
}
function UsersAdminJourney({ t }: { t: (key: string) => string }) {
  const tx = (key: string) => t(`${P}.usersJourney.${key}`);
  const [scenario, setScenario] = useState("admin");
  const [inviteOpen, setInviteOpen] = useState(false);
  const [userDrawer, setUserDrawer] = useState<DemoRow | null>(null);
  const [email, setEmail] = useState("");
  const [selectedPermissions, setSelectedPermissions] = useState<string[]>([]);
  const [permissionsByUser, setPermissionsByUser] = useDemoState<
    Record<string, string[]>
  >("admin.user-permissions", {});
  const [inviteNotice, setInviteNotice] = useState("");
  const [invites, setInvites] = useDemoState<DemoRow[]>(
    "admin.user-invites",
    [],
  );
  const [inviteError, setInviteError] = useState(false);
  const admin = scenario === "admin";
  return (
    <div className={styles.section}>
      <Space wrap>
        <Select
          value={scenario}
          onChange={setScenario}
          options={["admin", "readOnly", "loading", "error"].map((value) => ({
            value,
            label: tx(`scenario.${value}`),
          }))}
          style={{ width: 210 }}
        />
        {admin && (
          <Button
            type="primary"
            onClick={() => {
              setEmail("");
              setInviteError(false);
              setInviteOpen(true);
            }}
          >
            {tx("invite")}
          </Button>
        )}
      </Space>
      {inviteNotice && <Alert type="success" showIcon message={inviteNotice} />}
      {scenario === "loading" && (
        <Alert type="info" showIcon message={tx("loading")} />
      )}
      {scenario === "error" && (
        <Alert
          type="error"
          showIcon
          message={tx("loadError")}
          action={
            <Button size="small" onClick={() => setScenario("admin")}>
              {tx("retry")}
            </Button>
          }
        />
      )}
      {scenario === "readOnly" && (
        <Alert type="warning" showIcon message={tx("readOnly")} />
      )}
      {scenario !== "loading" && scenario !== "error" && (
        <DemoCollection
          storageKey="admin.users"
          title={t(`${P}.users.title`)}
          description={t(`${P}.users.desc`)}
          initialRows={users}
          fields={fields(
            [
              "name",
              "email",
              "role",
              "unit",
              "auth",
              "permissions",
              "description",
              "enabled",
            ],
            t,
          )}
          canCreate={admin}
          canEdit={admin}
          canDelete={admin}
          canToggle={admin}
          rowActions={
            admin
              ? [
                  {
                    key: "permissions",
                    label: tx("userActions"),
                    onClick: (row) => {
                      setUserDrawer(row);
                      setSelectedPermissions(
                        permissionsByUser[row.id] ?? ["personalization"],
                      );
                    },
                  },
                ]
              : []
          }
        />
      )}
      <DemoBoundary>{t(`${P}.users.roleHelp`)}</DemoBoundary>
      <Drawer
        open={inviteOpen}
        title={tx("inviteTitle")}
        onClose={() => setInviteOpen(false)}
        width={420}
      >
        <Typography.Paragraph>{tx("inviteLocalOnly")}</Typography.Paragraph>
        <Input
          value={email}
          type="email"
          placeholder={tx("emailPlaceholder")}
          onChange={(event) => {
            setEmail(event.target.value);
            setInviteError(false);
          }}
        />
        {inviteError && (
          <Alert
            style={{ marginTop: 12 }}
            type="error"
            showIcon
            message={tx("invalidEmail")}
          />
        )}
        <Button
          type="primary"
          style={{ marginTop: 16 }}
          onClick={() => {
            if (!/^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(email)) {
              setInviteError(true);
              return;
            }
            const invite = {
              id: `invite-${Date.now()}`,
              name: email,
              description: tx("inviteCreated"),
              enabled: true,
              values: { code: `INVITE-${Date.now()}`, state: "pending" },
            };
            setInvites((current) => [...current, invite]);
            setInviteNotice(tx("inviteCreated"));
            setInviteOpen(false);
          }}
        >
          {tx("createInvite")}
        </Button>
      </Drawer>
      <Drawer
        open={userDrawer !== null}
        title={userDrawer?.name}
        onClose={() => setUserDrawer(null)}
        width={480}
      >
        <Descriptions
          column={1}
          size="small"
          items={[
            { key: "agents", label: tx("agentCount"), children: "2" },
            { key: "features", label: tx("featureCount"), children: "1" },
          ]}
        />
        <Typography.Title level={5}>{tx("permissionsTitle")}</Typography.Title>
        <PermissionCheckboxPicker
          catalog={demoPermissionCatalog}
          value={selectedPermissions}
          disabled={!admin}
          unitGrantedKeys={new Set(["models"])}
          deniedKeys={new Set(["users"])}
          onChange={setSelectedPermissions}
        />
        <Button
          type="primary"
          disabled={!admin || !userDrawer}
          style={{ marginTop: 16 }}
          onClick={() => {
            if (userDrawer)
              setPermissionsByUser((current) => ({
                ...current,
                [userDrawer.id]: selectedPermissions,
              }));
            setInviteNotice(tx("permissionsSaved"));
            setUserDrawer(null);
          }}
        >
          {tx("savePermissions")}
        </Button>
        <Space wrap style={{ marginTop: 12 }}>
          <DemoExternalAction
            label={tx("resetPassword")}
            detail={tx("resetPasswordBoundary")}
          />
          <DemoExternalAction
            label={tx("unlockLogin")}
            detail={tx("unlockBoundary")}
          />
        </Space>
      </Drawer>
      <DemoCollection
        storageKey="admin.user-invites"
        title={tx("inviteList")}
        initialRows={invites}
        fields={[
          { name: "name", label: t(`${P}.field.email`) },
          { name: "state", label: t(`${P}.field.state`) },
        ]}
        canCreate={false}
        canEdit={false}
        rowActions={
          admin
            ? [
                {
                  key: "revoke",
                  label: tx("revoke"),
                  onClick: (row) =>
                    setInvites((current) =>
                      current.map((invite) =>
                        invite.id === row.id
                          ? {
                              ...invite,
                              enabled: false,
                              values: { ...invite.values, state: "revoked" },
                            }
                          : invite,
                      ),
                    ),
                },
              ]
            : []
        }
      />
    </div>
  );
}

function collection(
  path: string,
  tab: string | undefined,
  t: (key: string) => string,
  setLocalTab: (value: string) => void,
  navigate: DemoPageProps["navigate"],
) {
  const tx = (key: string) => t(`${P}.${key}`);
  const handleTab = (key: string) => {
    setLocalTab(key);
    navigate(path, key);
  };
  const active =
    tab ||
    (
      {
        "/admin/users": "local",
        "/admin/models": "chat",
        "/admin/backend": "my",
        "/admin/plugins": "installed",
        "/sharing": "queue",
      } as Record<string, string>
    )[path];
  if (path === "/admin/users") {
    return (
      <Tabs
        activeKey={active}
        onChange={handleTab}
        items={[
          {
            key: "local",
            label: tx("tab.local"),
            children: <UsersAdminJourney t={t} />,
          },
          ...["feishu", "wecom", "dingtalk", "oidc"].map((key) => ({
            key,
            label: tx(`tab.${key}`),
            children: (
              <>
                <DemoSettings
                  storageKey={`admin.sso.${key}`}
                  title={tx(`sso.${key}`)}
                  description={tx("sso.desc")}
                  fields={fields(
                    ["clientId", "issuer", "redirectUri", "scopes", "enabled"],
                    t,
                  )}
                  initialValues={{
                    clientId: "demo-client-id",
                    issuer: "https://identity.example.test",
                    redirectUri: "https://app.example.test/login/callback",
                    scopes: "openid profile email",
                    enabled: false,
                  }}
                />
                <DemoExternalAction
                  label={tx("usersJourney.testSso")}
                  detail={tx("usersJourney.testSsoBoundary")}
                />
              </>
            ),
          })),
        ]}
      />
    );
  }
  if (path === "/admin/org-units") return <OrgUnitEditor t={t} />;
  if (path === "/admin/models")
    return <ModelsJourney t={t} tab={active} onTab={handleTab} />;
  if (path === "/admin/backend")
    return <StorageJourney t={t} tab={active} onTab={handleTab} />;
  if (path === "/admin/plugins")
    return <PluginsJourney t={t} tab={active} onTab={handleTab} />;
  if (path === "/sharing")
    return (
      <Tabs
        activeKey={active}
        onChange={handleTab}
        items={[
          {
            key: "queue",
            label: tx("tab.queue"),
            children: <SharingQueue t={t} />,
          },
          {
            key: "settings",
            label: tx("tab.settings"),
            children: <SharingResourcesJourney t={t} />,
          },
        ]}
      />
    );
  return null;
}
function ModelsJourney({
  t,
  tab,
  onTab,
}: {
  t: (key: string) => string;
  tab: string;
  onTab: (value: string) => void;
}) {
  const tx = (key: string) => t(`${P}.modelJourney.${key}`);
  const [scenario, setScenario] = useState("ready");
  const [selected, setSelected] = useState<DemoRow | null>(null);
  const fieldsFor = (key: string) =>
    fields(
      key === "voice"
        ? [
            "name",
            "endpoint",
            "ttsModel",
            "sttModel",
            "voiceId",
            "language",
            "enabled",
          ]
        : key === "search"
        ? ["name", "endpoint", "indexName", "topK", "enabled"]
        : ["name", "endpoint", "models", "contextWindow", "enabled"],
      t,
    );
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          aria-label={tx("scenarioLabel")}
          value={scenario}
          onChange={setScenario}
          options={["ready", "loading", "empty", "error", "readOnly"].map(
            (value) => ({ value, label: tx(`scenario.${value}`) }),
          )}
          style={{ width: 230 }}
        />
      </Space>
      {scenario === "loading" && (
        <Alert type="info" showIcon message={tx("loading")} />
      )}
      {scenario === "empty" && (
        <Alert type="info" showIcon message={tx("empty")} />
      )}
      {scenario === "error" && (
        <Alert
          type="error"
          showIcon
          message={tx("error")}
          action={
            <Button size="small" onClick={() => setScenario("ready")}>
              {tx("retry")}
            </Button>
          }
        />
      )}
      {scenario === "readOnly" && (
        <Alert type="warning" showIcon message={tx("readOnly")} />
      )}
      {scenario !== "loading" &&
        scenario !== "empty" &&
        scenario !== "error" && (
          <Tabs
            activeKey={tab}
            onChange={onTab}
            items={TABS["/admin/models"].map((key) => ({
              key,
              label: tx(`tab.${key}`),
              children: (
                <>
                  <DemoCollection
                    storageKey={`admin.models.${key}`}
                    title={t(`${P}.models.${key}`)}
                    initialRows={providers}
                    fields={fieldsFor(key)}
                    canCreate={scenario !== "readOnly"}
                    canEdit={scenario !== "readOnly"}
                    canDelete={scenario !== "readOnly"}
                    canToggle={scenario !== "readOnly"}
                    rowActions={
                      scenario !== "readOnly"
                        ? [
                            {
                              key: "configure",
                              label: tx("configure"),
                              onClick: setSelected,
                            },
                          ]
                        : []
                    }
                  />
                  <DemoBoundary>{t(`${P}.modelBoundary`)}</DemoBoundary>
                  <DemoExternalAction
                    label={t(`${P}.external.testModel`)}
                    detail={t(`${P}.external.testModelDetail`)}
                  />
                </>
              ),
            }))}
          />
        )}
      <Drawer
        open={selected !== null}
        title={selected?.name}
        onClose={() => setSelected(null)}
        width={520}
      >
        {scenario === "readOnly" ? (
          <Alert type="warning" showIcon message={tx("readOnly")} />
        ) : (
          <DemoSettings
            storageKey={`admin.models.provider.${selected?.id || "none"}`}
            title={tx("providerConfig")}
            fields={[
              { name: "name", label: t(`${P}.field.name`), required: true },
              { name: "endpoint", label: t(`${P}.field.endpoint`) },
              {
                name: "apiKey",
                label: t(`${P}.field.apiKey`),
                kind: "password",
                initialValue: "",
              },
              {
                name: "models",
                label: t(`${P}.field.models`),
                kind: "textarea",
              },
            ]}
            initialValues={{
              name: selected?.name || "",
              endpoint: "https://api.example.test/v1",
              apiKey: "",
              models: String(selected?.values?.models || "chat-example"),
            }}
          />
        )}
        <DemoExternalAction
          label={t(`${P}.external.testModel`)}
          detail={t(`${P}.external.testModelDetail`)}
        />
      </Drawer>
    </>
  );
}
function StorageJourney({
  t,
  tab,
  onTab,
}: {
  t: (key: string) => string;
  tab: string;
  onTab: (value: string) => void;
}) {
  const [drawerType, setDrawerType] = useState("");
  const [scenario, setScenario] = useState("admin");
  const admin = scenario === "admin";
  const tx = (key: string) => t(`${P}.${key}`);
  const typeNames = ["local", "s3", "azure", "minio"];
  const localTree: TreeDataNode[] = [
    {
      key: "root",
      title: "workspace-demo",
      children: [
        {
          key: "docs",
          title: "docs",
          children: [{ key: "readme", title: "README.md" }],
        },
        { key: "exports", title: "exports" },
      ],
    },
  ];
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          aria-label={tx("storage.permissionScenario")}
          value={scenario}
          onChange={setScenario}
          options={["admin", "readOnly", "loading", "error", "empty"].map(
            (value) => ({ value, label: tx(`storage.${value}`) }),
          )}
          style={{ width: 220 }}
        />
      </Space>
      {scenario === "readOnly" && (
        <Alert type="warning" showIcon message={tx("storage.readOnly")} />
      )}
      <Tabs
        activeKey={tab}
        onChange={onTab}
        items={[
          {
            key: "my",
            label: tx("tab.my"),
            children:
              scenario === "loading" ? (
                <Alert type="info" showIcon message={tx("storage.loading")} />
              ) : scenario === "error" ? (
                <Alert
                  type="error"
                  showIcon
                  message={tx("storage.error")}
                  action={
                    <Button size="small" onClick={() => setScenario("admin")}>
                      {tx("storage.retry")}
                    </Button>
                  }
                />
              ) : scenario === "empty" ? (
                <Alert type="info" showIcon message={tx("storage.empty")} />
              ) : (
                <DemoCollection
                  storageKey="admin.storage"
                  title={tx("storage.title")}
                  initialRows={storage}
                  fields={fields(
                    ["name", "type", "location", "used", "enabled"],
                    t,
                  )}
                  canCreate={admin}
                  canEdit={admin}
                  canDelete={admin}
                  canToggle={admin}
                  rowActions={
                    admin
                      ? [
                          {
                            key: "inspect",
                            label: tx("storage.browse"),
                            onClick: (row) =>
                              setDrawerType(
                                String(row.values?.type || "local"),
                              ),
                          },
                        ]
                      : []
                  }
                />
              ),
          },
          {
            key: "types",
            label: tx("tab.types"),
            children: (
              <Row gutter={[16, 16]}>
                {typeNames.map((type) => (
                  <Col xs={24} sm={12} md={8} lg={6} key={type}>
                    <Card
                      title={tx(`storageKind.${type}`)}
                      extra={
                        <Button
                          disabled={!admin}
                          onClick={() => setDrawerType(type)}
                        >
                          {tx("action.configure")}
                        </Button>
                      }
                    >
                      {tx(`storageKind.${type}Desc`)}
                    </Card>
                  </Col>
                ))}
              </Row>
            ),
          },
        ]}
      />
      <Drawer
        open={Boolean(drawerType)}
        title={drawerType ? tx(`storageKind.${drawerType}`) : ""}
        onClose={() => setDrawerType("")}
        width={520}
      >
        {admin ? (
          <DemoSettings
            storageKey={`admin.storage.config.${drawerType}`}
            title={tx("storage.configuration")}
            fields={[
              ...fields(["name", "endpoint", "bucket", "region", "enabled"], t),
              {
                name: "accessKey",
                label: t(`${P}.field.accessKey`),
                kind: "password",
                initialValue: "",
              },
              {
                name: "secretKey",
                label: t(`${P}.field.secretKey`),
                kind: "password",
                initialValue: "",
              },
            ]}
            initialValues={{
              name: `Preview ${drawerType}`,
              endpoint: "https://storage.example.test",
              bucket: "preview-bucket",
              region: "sample-region",
              accessKey: "",
              secretKey: "",
            }}
          />
        ) : (
          <Alert type="warning" showIcon message={tx("storage.readOnly")} />
        )}
        <Typography.Title level={5}>{tx("storage.browse")}</Typography.Title>
        <Tree treeData={localTree} defaultExpandAll selectable />
        <DemoBoundary>{tx("storage.treeBoundary")}</DemoBoundary>
        <DemoExternalAction
          label={tx("external.storageProbe")}
          detail={tx("external.storageProbeDetail")}
        />
      </Drawer>
    </>
  );
}
function PluginsJourney({
  t,
  tab,
  onTab,
}: {
  t: (key: string) => string;
  tab: string;
  onTab: (value: string) => void;
}) {
  const tx = (key: string) => t(`${P}.${key}`);
  const [selected, setSelected] = useState<DemoRow | null>(null);
  const [permission, setPermission] = useState("admin");
  const [staged, setStaged] = useDemoState<string[]>(
    "admin.plugins.staged-preview",
    [],
  );
  const admin = permission === "admin";
  const market: DemoRow[] = [
    {
      id: "market-ocr",
      name: tx("plugins.ocrName"),
      description: tx("plugins.ocrDescription"),
      enabled: false,
      values: { version: "2.1.0", publisher: tx("plugins.fixtureCatalog") },
    },
    {
      id: "market-calendar",
      name: tx("plugins.calendarName"),
      description: tx("plugins.calendarDescription"),
      enabled: false,
      values: { version: "1.2.0", publisher: tx("plugins.fixtureCatalog") },
    },
  ];
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          aria-label={tx("plugins.permissionScenario")}
          value={permission}
          onChange={setPermission}
          options={["admin", "readOnly"].map((value) => ({
            value,
            label: tx(`plugins.${value}`),
          }))}
          style={{ width: 200 }}
        />
      </Space>
      {permission === "readOnly" && (
        <Alert type="warning" showIcon message={tx("plugins.readOnly")} />
      )}
      <Tabs
        activeKey={tab}
        onChange={onTab}
        items={[
          {
            key: "installed",
            label: tx("tab.installed"),
            children: (
              <DemoCollection
                storageKey="admin.plugins"
                title={tx("plugins.title")}
                initialRows={plugins}
                fields={fields(
                  [
                    "name",
                    "version",
                    "source",
                    "tools",
                    "description",
                    "enabled",
                  ],
                  t,
                )}
                canCreate={admin}
                canEdit={admin}
                canDelete={admin}
                canToggle={admin}
                rowActions={[
                  {
                    key: "detail",
                    label: tx("plugins.details"),
                    onClick: setSelected,
                  },
                ]}
              />
            ),
          },
          {
            key: "market",
            label: tx("tab.market"),
            children: (
              <Row gutter={[16, 16]}>
                {market.map((row) => (
                  <Col xs={24} sm={12} md={8} key={row.id}>
                    <Card
                      title={row.name}
                      extra={
                        <Button onClick={() => setSelected(row)}>
                          {tx("plugins.details")}
                        </Button>
                      }
                    >
                      <Typography.Paragraph>
                        {row.description}
                      </Typography.Paragraph>
                      <Typography.Text type="secondary">
                        {row.values?.publisher} · {row.values?.version}
                      </Typography.Text>
                      <div>
                        <Button
                          disabled={!admin || staged.includes(row.id)}
                          style={{ marginTop: 12 }}
                          onClick={() =>
                            setStaged((current) => [...current, row.id])
                          }
                        >
                          {staged.includes(row.id)
                            ? tx("plugins.staged")
                            : tx("plugins.stagePreview")}
                        </Button>
                      </div>
                    </Card>
                  </Col>
                ))}
              </Row>
            ),
          },
        ]}
      />
      {staged.length > 0 && (
        <Alert
          style={{ marginTop: 12 }}
          type="info"
          showIcon
          message={tx("plugins.stagingNotice")}
        />
      )}
      <Drawer
        open={selected !== null}
        title={selected?.name}
        onClose={() => setSelected(null)}
        width={480}
      >
        {selected && (
          <Descriptions
            column={1}
            size="small"
            items={[
              {
                key: "version",
                label: tx("field.version"),
                children: String(selected.values?.version || "2.1.0"),
              },
              {
                key: "publisher",
                label: tx("field.publisher"),
                children: String(
                  selected.values?.publisher || tx("plugins.fixtureCatalog"),
                ),
              },
              {
                key: "description",
                label: tx("field.description"),
                children:
                  selected.description || tx("plugins.localPluginDetails"),
              },
            ]}
          />
        )}
        <DemoBoundary>{tx("plugins.noCodeExecution")}</DemoBoundary>
        <DemoExternalAction
          label={tx("external.pluginInstall")}
          detail={tx("external.pluginInstallDetail")}
        />
      </Drawer>
    </>
  );
}

function SharingResourcesJourney({ t }: { t: (key: string) => string }) {
  const tx = (key: string) => t(`${P}.sharingJourney.${key}`);
  const [grantsByResource, setGrantsByResource] = useDemoState<
    Record<string, string[]>
  >("admin.sharing-resource-grants", {});
  const [selected, setSelected] = useState<DemoRow | null>(null);
  const [permissions, setPermissions] = useState<string[]>(["personalization"]);
  const [savedGrantName, setSavedGrantName] = useState("");
  const [scenario, setScenario] = useState("admin");
  const admin = scenario === "admin";
  const visibleRows = sharingRows;
  return (
    <>
      <Space wrap style={{ marginBottom: 12 }}>
        <Select
          aria-label={tx("scenarioLabel")}
          value={scenario}
          onChange={setScenario}
          options={["admin", "readOnly", "forbidden"].map((value) => ({
            value,
            label: tx(`scenario.${value}`),
          }))}
          style={{ width: 220 }}
        />
      </Space>
      {scenario === "readOnly" && (
        <Alert type="warning" showIcon message={tx("readOnly")} />
      )}
      {scenario === "forbidden" ? (
        <Alert type="error" showIcon message={tx("forbidden")} />
      ) : (
        <DemoCollection
          storageKey="admin.sharing"
          title={t(`${P}.sharing.resources`)}
          initialRows={visibleRows}
          fields={fields(
            ["name", "visibility", "permission", "grants", "description"],
            t,
          )}
          canCreate={admin}
          canEdit={admin}
          canDelete={admin}
          rowActions={[
            {
              key: "permissions",
              label: tx("inspectGrants"),
              onClick: (row) => {
                setSelected(row);
                setPermissions(
                  grantsByResource[row.id] || ["personalization", "models"],
                );
              },
            },
          ]}
        />
      )}
      {savedGrantName && (
        <Alert
          style={{ marginBottom: 12 }}
          type="success"
          showIcon
          message={tx("savedLocalNotice")}
          description={savedGrantName}
        />
      )}
      <Drawer
        open={selected !== null}
        title={tx("effectiveGrants")}
        onClose={() => setSelected(null)}
        width={480}
      >
        <Typography.Paragraph>{selected?.name}</Typography.Paragraph>
        <PermissionCheckboxPicker
          catalog={demoPermissionCatalog}
          value={permissions}
          disabled={!admin}
          onChange={setPermissions}
        />
        <Button
          type="primary"
          disabled={!admin || !selected}
          style={{ marginTop: 16 }}
          onClick={() => {
            if (!selected) return;
            setGrantsByResource((current) => ({
              ...current,
              [selected.id]: permissions,
            }));
            setSavedGrantName(selected.name);
            setSelected(null);
          }}
        >
          {tx("saveLocal")}
        </Button>
        <DemoBoundary>{tx("noExternalGrant")}</DemoBoundary>
      </Drawer>
    </>
  );
}
export function AdministrationPages({ path, tab, navigate }: DemoPageProps) {
  const { t } = useTranslation();
  const tx = (key: string) => {
    if (key.startsWith("usage.group."))
      return t(`${P}.usageGroup.${key.slice("usage.group.".length)}`);
    if (key === "action.disable") return t(`${P}.actions.disable`);
    if (key.startsWith("agent."))
      return t(`${P}.agentRuntime.${key.slice("agent.".length)}`);
    if (key === "users.roleHelp") return t(`${P}.users.roleHelp`);
    return t(`${P}.${key}`);
  };
  const [activeTab, setActiveTab] = useState(tab || "");
  const [query, setQuery] = useState("");
  const [pwaScenario, setPwaScenario] = useState("ready");
  const [pwaProtocol, setPwaProtocol] = useState("https");
  const [pwaGuideOpen, setPwaGuideOpen] = useState(false);
  const defaults: Record<string, string> = {
    "/admin/users": "local",
    "/admin/models": "chat",
    "/admin/backend": "my",
    "/admin/plugins": "installed",
    "/sharing": "queue",
    "/admin/security": "hitl",
    "/admin/advanced": "env-vars",
    "/token-usage": "day",
  };
  const chosenTab = tab || defaults[path] || "";
  const title = useMemo(
    () => ADMINISTRATION_PAGES.find((page) => page.path === path)?.titleKey,
    [path],
  );
  const changeTab = (value: string) => {
    setActiveTab(value);
    navigate(path, value);
  };
  const pageBody =
    path.startsWith("/admin/") || path === "/sharing"
      ? collection(path, chosenTab, t, setActiveTab, navigate)
      : null;
  const external = (label: string, detail: string) => (
    <DemoExternalAction label={tx(label)} detail={tx(detail)} />
  );
  const usageTotals = usageRecords.reduce(
    (totals, row) => ({
      input: totals.input + row.input,
      output: totals.output + row.output,
      cost: totals.cost + row.cost,
      requests: totals.requests + 1,
    }),
    { input: 0, output: 0, cost: 0, requests: 0 },
  );
  const groupKey = (
    ["day", "expert", "feature", "model"].includes(tab || activeTab)
      ? tab || activeTab
      : "day"
  ) as "day" | "expert" | "feature" | "model";
  const groupedUsage = groupUsage(groupKey);
  const dailyUsage = groupUsage("day").sort((left, right) =>
    left.key.localeCompare(right.key),
  );
  const maxDailyTokens = Math.max(
    ...dailyUsage.map((row) => row.input + row.output),
  );
  if (path === "/token-usage")
    return (
      <PageShell title={t(title || "pageShell.tokenUsage.title")}>
        <DemoBoundary>
          <Space wrap>{tx("sampleData")}</Space>
        </DemoBoundary>
        <Row gutter={[16, 16]} className={styles.stats}>
          {[
            [tx("usage.total"), usageTotals.input + usageTotals.output],
            [tx("usage.cost"), `$${usageTotals.cost.toFixed(2)}`],
            [tx("usage.requests"), usageTotals.requests],
            [
              tx("usage.avg"),
              Math.round(
                (usageTotals.input + usageTotals.output) / usageTotals.requests,
              ),
            ],
          ].map(([label, value]) => (
            <Col xs={24} sm={12} md={6} key={String(label)}>
              <Card>
                <Statistic
                  title={String(label)}
                  value={
                    typeof value === "number" ? value.toLocaleString() : value
                  }
                />
              </Card>
            </Col>
          ))}
        </Row>
        <Card title={tx("usage.daily")}>
          <div className={styles.chart}>
            {dailyUsage.map((row) => (
              <div key={row.key}>
                <Progress
                  type="circle"
                  percent={Math.round(
                    ((row.input + row.output) / maxDailyTokens) * 100,
                  )}
                  size={52}
                  format={() =>
                    `${((row.input + row.output) / 1000000).toFixed(2)}M`
                  }
                />
                <Typography.Text>{row.key.slice(5)}</Typography.Text>
              </div>
            ))}
          </div>
        </Card>
        <Tabs
          activeKey={groupKey}
          onChange={changeTab}
          items={(["day", "expert", "feature", "model"] as const).map(
            (key) => ({
              key,
              label: tx(`usage.group.${key}`),
              children: (
                <Table
                  size="small"
                  pagination={{ pageSize: 5 }}
                  dataSource={groupedUsage.map((row) => ({
                    ...row,
                    key: row.key,
                  }))}
                  columns={[
                    { title: tx(`usage.group.${key}`), dataIndex: "key" },
                    {
                      title: tx("usage.input"),
                      dataIndex: "input",
                      render: (value: number) => value.toLocaleString(),
                    },
                    {
                      title: tx("usage.output"),
                      dataIndex: "output",
                      render: (value: number) => value.toLocaleString(),
                    },
                    {
                      title: tx("usage.cost"),
                      dataIndex: "cost",
                      render: (value: number) => `$${value.toFixed(2)}`,
                    },
                    { title: tx("usage.requests"), dataIndex: "count" },
                  ]}
                />
              ),
            }),
          )}
        />
      </PageShell>
    );
  if (["/login", "/setup", "/invite", "/login/oidc/complete"].includes(path))
    return (
      <PublicAccessPage
        path={path}
        tab={tab}
        titleKey={title}
        navigate={navigate}
        onBack={() => navigate("/login")}
      />
    );
  if (path === "/agent-config")
    return (
      <PageShell title={t(title || "agentConfig.title")}>
        <DemoSettings
          storageKey="admin.agentConfig.runtime"
          title={tx("agent.runtime")}
          description={tx("agent.runtimeDesc")}
          fields={[
            {
              name: "max_iters",
              label: tx("agent.max_iters"),
              kind: "number",
              initialValue: 20,
            },
            {
              name: "max_input_length",
              label: tx("agent.max_input_length"),
              kind: "number",
              initialValue: 32000,
            },
            {
              name: "enable_trajectory",
              label: tx("agent.enable_trajectory"),
              kind: "switch",
              initialValue: true,
            },
            {
              name: "timeout",
              label: tx("field.timeout"),
              kind: "number",
              initialValue: 60,
            },
          ]}
          initialValues={{
            max_iters: 20,
            max_input_length: 32000,
            enable_trajectory: true,
            timeout: 60,
          }}
        />
      </PageShell>
    );
  if (path === "/pwa-debug")
    return (
      <PageShell title={t(title || "pageShell.pwaDebug.title")}>
        <DemoBoundary />
        <Card title={tx("pwa.diagnostics")} style={{ marginBottom: 16 }}>
          <Space wrap>
            <Select
              aria-label={tx("pwa.installScenario")}
              value={pwaScenario}
              onChange={setPwaScenario}
              options={[
                "ready",
                "loading",
                "installedSample",
                "unsupported",
                "failed",
              ].map((value) => ({ value, label: tx(`pwa.scenario.${value}`) }))}
              style={{ width: 240 }}
            />
            <Select
              aria-label={tx("pwa.protocol")}
              value={pwaProtocol}
              onChange={setPwaProtocol}
              options={["https", "localhost", "http"].map((value) => ({
                value,
                label: tx(`pwa.protocol.${value}`),
              }))}
              style={{ width: 180 }}
            />
            <Button onClick={() => setPwaGuideOpen(true)}>
              {tx("pwa.manualGuide")}
            </Button>
          </Space>
          <Alert
            style={{ marginTop: 12 }}
            type={
              pwaScenario === "failed"
                ? "error"
                : pwaScenario === "unsupported"
                ? "warning"
                : "info"
            }
            showIcon
            message={tx(`pwa.scenarioMessage.${pwaScenario}`)}
            description={tx(`pwa.protocolMessage.${pwaProtocol}`)}
          />
          <DemoExternalAction
            label={tx("pwa.installPreview")}
            detail={tx("pwa.installBoundary")}
          />
          <DemoExternalAction
            label={tx("pwa.copyDiagnostics")}
            detail={tx("pwa.copyBoundary")}
          />
        </Card>
        <Row gutter={[16, 16]}>
          {["serviceWorker", "installPrompt", "push", "cache"].map((key) => (
            <Col xs={24} md={12} key={key}>
              <Card
                title={tx(`pwa.${key}`)}
                extra={
                  <Tag>
                    {query === key
                      ? tx("action.enabled")
                      : tx("pwa.demoStatus")}
                  </Tag>
                }
              >
                <Typography.Paragraph>
                  {tx(`pwa.${key}Desc`)}
                </Typography.Paragraph>
                <Space wrap>
                  <Button onClick={() => setQuery(query === key ? "" : key)}>
                    {query === key ? tx("action.disable") : tx("action.toggle")}
                  </Button>
                  {external(
                    "external.openBrowserSettings",
                    "external.browserSettingsDetail",
                  )}
                </Space>
              </Card>
            </Col>
          ))}
        </Row>
        <Card title={tx("pwa.diagnostics")}>
          <Input.TextArea
            rows={6}
            value={`User agent: Demo Browser\nService worker: preview-only\nCache entries: 12 sample records\nInstallation: not detected`}
            readOnly
          />
        </Card>
        <Drawer
          open={pwaGuideOpen}
          title={tx("pwa.manualGuide")}
          onClose={() => setPwaGuideOpen(false)}
          width={480}
        >
          <Typography.Paragraph>
            {tx("pwa.manualGuideBody")}
          </Typography.Paragraph>
          <DemoBoundary>{tx("pwa.installBoundary")}</DemoBoundary>
        </Drawer>
      </PageShell>
    );
  if (path === "/admin/security")
    return (
      <PageShell title={t(title || "pageShell.security.title")}>
        <Tabs
          activeKey={chosenTab || "hitl"}
          onChange={changeTab}
          items={TABS[path].map((key) => ({
            key,
            label: tx(`tab.${key}`),
            children: <SecurityContent key={key} tab={key} />,
          }))}
        />
      </PageShell>
    );
  if (path === "/admin/advanced")
    return (
      <PageShell title={t(title || "pageShell.adminAdvanced.title")}>
        <Tabs
          activeKey={chosenTab || "env-vars"}
          onChange={changeTab}
          items={TABS[path].map((key) => ({
            key,
            label: tx(`advanced.tab.${key}`),
            children: <AdvancedContent key={key} tab={key} />,
          }))}
        />
      </PageShell>
    );
  return (
    <PageShell title={t(title || "pageShell.adminAdvanced.title")}>
      {pageBody}
    </PageShell>
  );
}
