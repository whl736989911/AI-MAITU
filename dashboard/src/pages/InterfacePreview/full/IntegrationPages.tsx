import { useEffect, useState, type ReactNode } from "react";
import {
  Alert,
  Button,
  Card,
  Drawer,
  Input,
  Modal,
  Segmented,
  Select,
  Space,
  Tag,
  Tabs as AntTabs,
  Typography,
  message,
} from "antd";
import { useTranslation } from "react-i18next";
import {
  Bot,
  Brain,
  Cable,
  FileCode2,
  Globe,
  HardDrive,
  Monitor,
  Package,
  Puzzle,
  Smartphone,
  Sparkles,
  TerminalSquare,
  Wrench,
} from "lucide-react";
import PageShell from "../../../layouts/PageShell";
import type {
  DemoPageDefinition,
  DemoPageProps,
  DemoRow,
  DemoValues,
} from "./contract";
import {
  DemoBoundary,
  DemoCollection,
  DemoExternalAction,
  DemoSettings,
  useDemoState,
} from "./DemoKit";
import styles from "./IntegrationPages.module.less";

const PERSONAL_TABS = [
  "skills",
  "tools",
  "plugins",
  "subagents",
  "channels",
  "mbti",
  "memory",
] as const;
const PERSONAL_TOOLS = ["builtin", "acp", "plugin"] as const;
const SKILL_TABS = ["custom", "builtin", "skillhub", "packages"] as const;
const WORKBENCH_TABS = ["browser", "terminal"] as const;
const REMOTE_TABS = ["desktop", "phone"] as const;
const PHONE_TABS = ["screen", "shell"] as const;
const CONNECTOR_TABS = ["enabled", "builtin", "custom"] as const;
const MEMORY_TABS = [
  "memoryOverview",
  "memoryProfile",
  "memoryLibrary",
  "memoryEpisodes",
  "memoryCandidates",
  "memoryJournal",
  "memoryConversations",
  "memoryProactive",
  "memorySettings",
  "memoryManual",
  "memoryBackups",
  "memoryPipeline",
  "memoryMigration",
] as const;
const I = "interfacePreview.full.integrations";

export const INTEGRATION_PAGES: readonly DemoPageDefinition[] = [
  {
    path: "/personalization",
    titleKey: `${I}.personalizationTitle`,
    tabs: PERSONAL_TABS,
  },
  ...PERSONAL_TABS.map((tab) => ({
    path: `/personalization/${tab}`,
    titleKey: `${I}.personalizationTitle`,
    tabs:
      tab === "skills"
        ? SKILL_TABS
        : tab === "tools"
        ? PERSONAL_TOOLS
        : tab === "memory"
        ? MEMORY_TABS
        : undefined,
  })),
  {
    path: "/connectors",
    titleKey: `${I}.connectorsTitle`,
    tabs: CONNECTOR_TABS,
  },
  {
    path: "/skill-packages",
    titleKey: `${I}.packagesTitle`,
    tabs: ["packages", "skills", "market"],
  },
  { path: "/workbench", titleKey: `${I}.workbenchTitle`, tabs: WORKBENCH_TABS },
  {
    path: "/workbench/browser",
    titleKey: `${I}.workbenchTitle`,
    tabs: WORKBENCH_TABS,
  },
  {
    path: "/workbench/terminal",
    titleKey: `${I}.workbenchTitle`,
    tabs: WORKBENCH_TABS,
  },
  { path: "/remote-desktop", titleKey: `${I}.remoteTitle`, tabs: REMOTE_TABS },
  {
    path: "/remote-desktop/desktop",
    titleKey: `${I}.remoteTitle`,
    tabs: REMOTE_TABS,
  },
  {
    path: "/remote-desktop/phone",
    titleKey: `${I}.remoteTitle`,
    tabs: PHONE_TABS,
  },
  {
    path: "/remote-desktop/phone/screen",
    titleKey: `${I}.remoteTitle`,
    tabs: PHONE_TABS,
  },
  {
    path: "/remote-desktop/phone/shell",
    titleKey: `${I}.remoteTitle`,
    tabs: PHONE_TABS,
  },
  { path: "/acp", titleKey: `${I}.acpTitle` },
];

const rows = (
  names: string[],
  descriptions: string[],
  kinds: string[],
): DemoRow[] =>
  names.map((name, index) => ({
    id: `${kinds[index]}-${index + 1}`,
    name,
    description: descriptions[index],
    enabled: index !== names.length - 1,
    values: {
      category: kinds[index],
      updated: `2026-10-${String(20 - index).padStart(2, "0")}`,
      scope: index % 2 ? "当前智能体" : "个人",
    },
  }));
const skillRows = rows(
  ["合同条款审阅", "资料归档助手", "会议纪要整理"],
  ["提取关键义务并标记风险", "按项目目录建议文件归档", "整理议题、决议与待办"],
  ["分析", "检索", "写作"],
);
const toolRows = rows(
  ["知识库检索", "网页摘要", "文件读取"],
  ["检索已授权的知识资料", "生成当前网页摘要的本地示例", "读取工作区虚构文件"],
  ["内置", "内置", "内置"],
);
const pluginRows = rows(
  ["表格助手", "流程图生成器"],
  ["整理表格结构（本地演示）", "输出可编辑流程节点"],
  ["插件", "插件"],
);
const subagentRows = rows(
  ["法规研究员", "文档校对员"],
  ["按主题拆分检索任务", "检查用词与引用格式"],
  ["协作", "协作"],
);
const connectorRows = rows(
  ["飞书文档", "企业微信", "GitHub", "自定义 MCP"],
  [
    "文档与云盘连接器示例",
    "企业消息连接器示例",
    "代码仓库连接器示例",
    "本地自定义服务配置",
  ],
  ["文档", "消息", "开发", "自定义"],
);
const packageRows = rows(
  ["项目管理技能集", "法务审阅技能集", "知识整理入门包"],
  ["项目计划、周报与复盘", "合同审查与法规比对", "文档摘要和目录建议"],
  ["6 项技能", "4 项技能", "3 项技能"],
);
const acpRows = rows(
  ["本地代码助手", "文档分析器", "测试运行器"],
  [
    "ACP stdio runner 示例，不会启动进程",
    "解析文档请求的 runner 示例",
    "受信任状态仅供本地界面演示",
  ],
  ["stdio", "stdio", "stdio"],
);
function tabIcon(value: string): ReactNode {
  if (value === "skills") return <Sparkles size={14} />;
  if (value === "tools" || value === "builtin") return <Wrench size={14} />;
  if (value === "plugins" || value === "plugin") return <Puzzle size={14} />;
  if (value === "subagents") return <Bot size={14} />;
  if (value === "channels") return <Cable size={14} />;
  if (value === "mbti") return <Brain size={14} />;
  if (value === "memory") return <HardDrive size={14} />;
  if (value === "terminal" || value === "shell")
    return <TerminalSquare size={14} />;
  if (value === "browser") return <Globe size={14} />;
  if (value === "phone") return <Smartphone size={14} />;
  if (value === "desktop") return <Monitor size={14} />;
  return <Package size={14} />;
}

function currentTab(
  path: string,
  tab: string | undefined,
  allowed: readonly string[],
  fallback: string,
) {
  const suffix = path.split("/").filter(Boolean).at(-1) ?? "";
  if (tab && allowed.includes(tab)) return tab;
  return allowed.includes(suffix) ? suffix : fallback;
}
function Tabs({
  value,
  options,
  onChange,
}: {
  value: string;
  options: readonly string[];
  onChange: (tab: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <AntTabs
      activeKey={value}
      onChange={onChange}
      items={options.map((key) => ({
        key,
        label: t(`${I}.tab.${key}`),
        children: null,
      }))}
    />
  );
}
function routeTab(
  path: string,
  tab: string,
  navigate: DemoPageProps["navigate"],
) {
  navigate(path, tab);
}
function Collection({
  storageKey,
  initialRows,
  fields,
  title,
}: {
  storageKey: string;
  initialRows: DemoRow[];
  fields: Parameters<typeof DemoCollection>[0]["fields"];
  title: string;
}) {
  return (
    <DemoCollection
      storageKey={`integrations.${storageKey}`}
      title={title}
      initialRows={initialRows}
      fields={fields}
      canToggle
    />
  );
}

function Personalization({
  tab,
  item,
  navigate,
  agentId,
}: {
  tab: string;
  item?: string;
  navigate: DemoPageProps["navigate"];
  agentId: string;
}) {
  const { t } = useTranslation();
  const [nestedTab, setNestedTab] = useState(
    SKILL_TABS.includes(item as (typeof SKILL_TABS)[number])
      ? (item as string)
      : "custom",
  );
  useEffect(() => {
    setNestedTab(
      SKILL_TABS.includes(item as (typeof SKILL_TABS)[number])
        ? (item as string)
        : "custom",
    );
  }, [item]);
  const title = t(`${I}.tab.${tab}`);
  const fields = [
    { name: "name", label: t(`${I}.field.name`), required: true },
    {
      name: "description",
      label: t(`${I}.field.description`),
      kind: "textarea" as const,
    },
    { name: "category", label: t(`${I}.field.category`) },
    {
      name: "scope",
      label: t(`${I}.field.scope`),
      kind: "select" as const,
      options: [
        { label: t(`${I}.personal`), value: "个人" },
        { label: t(`${I}.agent`), value: "当前智能体" },
      ],
    },
  ];
  if (tab === "skills")
    return (
      <div className={styles.section}>
        <Tabs
          value={nestedTab}
          options={SKILL_TABS}
          onChange={(value) => {
            setNestedTab(value);
            if (value === "packages") navigate("/skill-packages", "packages");
            else navigate(`/personalization/${tab}`, value);
          }}
        />
        {nestedTab === "skillhub" ? (
          <SkillMarket agentId={agentId} />
        ) : nestedTab === "packages" ? (
          <Card title={t(`${I}.packageList`)}>
            <Typography.Paragraph>
              {t(`${I}.personalPackagesDescription`)}
            </Typography.Paragraph>
            <Button onClick={() => navigate("/skill-packages", "packages")}>
              {t(`${I}.openPackages`)}
            </Button>
          </Card>
        ) : (
          <Collection
            storageKey={`${agentId}-personal-skills-${nestedTab}`}
            title={title}
            initialRows={nestedTab === "builtin" ? toolRows : skillRows}
            fields={fields}
          />
        )}
      </div>
    );
  if (tab === "tools")
    return (
      <ToolsDemo
        title={title}
        agentId={agentId}
        item={item}
        navigate={navigate}
      />
    );
  if (tab === "plugins")
    return (
      <Collection
        storageKey={`${agentId}-personal-plugins`}
        title={title}
        initialRows={pluginRows}
        fields={fields}
      />
    );
  if (tab === "subagents")
    return (
      <Collection
        storageKey={`${agentId}-personal-subagents`}
        title={title}
        initialRows={subagentRows}
        fields={fields}
      />
    );
  if (tab === "channels")
    return <ChannelsDemo title={title} agentId={agentId} />;
  if (tab === "mbti") return <MbtiJourney agentId={agentId} />;
  return <MemoryDemo agentId={agentId} item={item} navigate={navigate} />;
}
function MbtiJourney({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [type, setType] = useDemoState(`integrations.${agentId}.mbti-type`, "");
  const [appliedType, setAppliedType] = useDemoState<string | null>(
    `integrations.${agentId}.mbti-applied`,
    null,
  );
  const [scenario, setScenario] = useState("ready");
  const [detailOpen, setDetailOpen] = useState(false);
  const [testStep, setTestStep] = useState<
    "intro" | "questions" | "result" | null
  >(null);
  const [answer, setAnswer] = useState("");
  const title = (key: string) => t(`${I}.mbtiJourney.${key}`);
  const types = [
    "INTJ",
    "INTP",
    "ENTJ",
    "ENTP",
    "INFJ",
    "INFP",
    "ENFJ",
    "ENFP",
    "ISTJ",
    "ISFJ",
    "ESTJ",
    "ESFJ",
    "ISTP",
    "ISFP",
    "ESTP",
    "ESFP",
  ];
  return (
    <div className={styles.section}>
      <Card title={title("title")}>
        <Space wrap>
          <Select
            value={scenario}
            onChange={setScenario}
            options={["ready", "loading", "error", "readOnly"].map((value) => ({
              value,
              label: title(`scenario.${value}`),
            }))}
            style={{ minWidth: 170 }}
          />
          <Select
            value={type || undefined}
            placeholder={title("unset")}
            onChange={setType}
            options={types.map((value) => ({ value, label: value }))}
            disabled={
              scenario === "loading" ||
              scenario === "error" ||
              scenario === "readOnly"
            }
            style={{ minWidth: 140 }}
          />
          <Button onClick={() => setDetailOpen(true)} disabled={!type}>
            {title("details")}
          </Button>
          <Button onClick={() => setTestStep("intro")}>
            {title("startTest")}
          </Button>
          <Button
            type="primary"
            disabled={!type || scenario === "readOnly"}
            onClick={() => setAppliedType(type)}
          >
            {title("apply")}
          </Button>
        </Space>
        {appliedType && (
          <Alert
            type="success"
            showIcon
            message={title("appliedLocal").replace("{type}", appliedType)}
          />
        )}
        {scenario === "loading" && (
          <Alert type="info" showIcon message={title("loading")} />
        )}
        {scenario === "error" && (
          <Alert
            type="error"
            showIcon
            message={title("loadError")}
            action={
              <Button size="small" onClick={() => setScenario("ready")}>
                {title("retry")}
              </Button>
            }
          />
        )}
        {scenario === "readOnly" && (
          <Alert type="warning" showIcon message={title("readOnly")} />
        )}
        {!type && scenario === "ready" && (
          <Alert type="info" message={title("unset")} />
        )}
        <Typography.Paragraph type="secondary">
          {title("disclaimer")}
        </Typography.Paragraph>
      </Card>
      <Modal
        open={detailOpen}
        title={title("detailsTitle")}
        onCancel={() => setDetailOpen(false)}
        footer={
          <Button onClick={() => setDetailOpen(false)}>{title("close")}</Button>
        }
      >
        <Typography.Title level={4}>{type}</Typography.Title>
        <Typography.Paragraph>
          {title("detailDescription")}
        </Typography.Paragraph>
      </Modal>
      <Modal
        open={testStep !== null}
        title={title("testTitle")}
        onCancel={() => setTestStep(null)}
        footer={null}
      >
        {testStep === "intro" ? (
          <>
            <Typography.Paragraph>{title("testIntro")}</Typography.Paragraph>
            <Button type="primary" onClick={() => setTestStep("questions")}>
              {title("begin")}
            </Button>
          </>
        ) : testStep === "questions" ? (
          <>
            <Typography.Paragraph>{title("question")}</Typography.Paragraph>
            <Segmented
              value={answer}
              onChange={(value) => setAnswer(String(value))}
              options={["A", "B"]}
            />
            <Button
              type="primary"
              disabled={!answer}
              onClick={() => setTestStep("result")}
            >
              {title("submit")}
            </Button>
          </>
        ) : testStep === "result" ? (
          <>
            <Alert type="success" message={title("result")} />
            <Button
              onClick={() => {
                setType(answer === "A" ? "INFJ" : "ENTP");
                setTestStep(null);
              }}
            >
              {title("useResult")}
            </Button>
          </>
        ) : null}
      </Modal>
    </div>
  );
}

function ToolsDemo({
  title,
  agentId,
  item,
  navigate,
}: {
  title: string;
  agentId: string;
  item?: string;
  navigate: DemoPageProps["navigate"];
}) {
  const { t } = useTranslation();
  const [active, setActive] = useState(
    PERSONAL_TOOLS.includes(item as (typeof PERSONAL_TOOLS)[number])
      ? (item as string)
      : "builtin",
  );
  useEffect(() => {
    setActive(
      PERSONAL_TOOLS.includes(item as (typeof PERSONAL_TOOLS)[number])
        ? (item as string)
        : "builtin",
    );
  }, [item]);
  const data =
    active === "plugin" ? pluginRows : active === "acp" ? acpRows : toolRows;
  return (
    <div className={styles.section}>
      <DemoBoundary>{t(`${I}.toolsBoundary`)}</DemoBoundary>
      <Tabs
        value={active}
        options={PERSONAL_TOOLS}
        onChange={(value) => {
          setActive(value);
          navigate("/personalization/tools", value);
        }}
      />
      {active === "acp" && (
        <DemoExternalAction
          label={t(`${I}.runnerNoExecution`)}
          detail={t(`${I}.acpBoundary`)}
        />
      )}
      <Collection
        storageKey={`${agentId}-personal-tools-${active}`}
        title={title}
        initialRows={data}
        fields={[
          { name: "name", label: t(`${I}.field.name`), required: true },
          {
            name: "description",
            label: t(`${I}.field.description`),
            kind: "textarea",
          },
          { name: "category", label: t(`${I}.field.category`) },
        ]}
      />
    </div>
  );
}

const marketSkills = rows(
  ["资料检索", "会议纪要", "合同审阅", "表格分析"],
  [
    "基于知识库整理可追溯资料",
    "提取议题、决议和负责人",
    "识别合同义务与风险点",
    "归纳表格字段和异常值",
  ],
  ["检索", "协作", "法务", "数据"],
);

function SkillMarket({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const [selected, setSelected] = useState(marketSkills[0].id);
  const available = marketSkills.filter((skill) =>
    `${skill.name} ${skill.description}`
      .toLowerCase()
      .includes(query.toLowerCase()),
  );
  const skill = available.find((item) => item.id === selected) ?? available[0];
  return (
    <div className={styles.section}>
      <DemoBoundary>{t(`${I}.marketBoundary`)}</DemoBoundary>
      <div className={styles.marketBrowser}>
        <Card title={t(`${I}.marketTitle`)} className={styles.marketList}>
          <Input.Search
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            placeholder={t(`${I}.marketSearch`)}
            allowClear
          />
          {available.map((item) => (
            <Button
              key={item.id}
              block
              type={skill?.id === item.id ? "primary" : "text"}
              onClick={() => setSelected(item.id)}
            >
              {item.name}
            </Button>
          ))}
        </Card>
        <Card
          title={skill?.name ?? t(`${I}.noMarketMatches`)}
          className={styles.marketDetail}
        >
          {skill ? (
            <>
              <Typography.Paragraph>{skill.description}</Typography.Paragraph>
              <Tag>{String(skill.values?.category)}</Tag>
              <p>{t(`${I}.marketAgentHint`, { agent: agentId })}</p>
              <DemoExternalAction
                label={t(`${I}.importMarket`)}
                detail={t(`${I}.importBoundary`)}
              />
            </>
          ) : (
            <Typography.Text>{t(`${I}.noMarketMatches`)}</Typography.Text>
          )}
        </Card>
      </div>
    </div>
  );
}

const CHANNEL_KINDS = [
  "feishu",
  "dingtalk",
  "wecom",
  "qq",
  "weixin",
  "discord",
  "yuanbao",
  "xiaoyi",
  "mqtt",
  "telegram",
];
const channelRowsForAgent: DemoRow[] = [
  {
    id: "feishu-main",
    name: "项目协作群",
    description: "飞书机器人通知示例",
    enabled: true,
    values: {
      kind: "feishu",
      bot_id: "cli_demo_project",
      secret: "demo-secret",
      destination: "oc_demo_project",
      agent: "项目助理",
      response_mode: "stream",
      show_thinking: false,
      show_tool_hints: true,
    },
  },
  {
    id: "wecom-ops",
    name: "运维通知",
    description: "企业微信机器人示例",
    enabled: true,
    values: {
      kind: "wecom",
      bot_id: "ww_demo_ops",
      secret: "demo-secret",
      destination: "运维群",
      agent: "值班助理",
      response_mode: "invoke",
      show_thinking: false,
      show_tool_hints: false,
    },
  },
  {
    id: "telegram-lab",
    name: "研发实验室",
    description: "Telegram Bot 配置示例",
    enabled: false,
    values: {
      kind: "telegram",
      bot_id: "demo_bot",
      secret: "demo-token",
      destination: "研发测试群",
      agent: "代码助理",
      response_mode: "stream",
      show_thinking: true,
      show_tool_hints: true,
    },
  },
];

function ChannelsDemo({ title, agentId }: { title: string; agentId: string }) {
  const { t } = useTranslation();
  const channelKindOptions = CHANNEL_KINDS.map((value) => ({
    label: t(`channels.label_${value}`),
    value,
  }));
  const fields = [
    {
      name: "kind",
      label: t(`${I}.field.channelKind`),
      kind: "select" as const,
      options: channelKindOptions,
      required: true,
    },
    { name: "bot_id", label: t(`${I}.field.botId`), required: true },
    {
      name: "secret",
      label: t(`${I}.field.botCredential`),
      kind: "password" as const,
    },
    { name: "app_id", label: t(`${I}.field.appId`) },
    {
      name: "app_secret",
      label: t(`${I}.field.appSecret`),
      kind: "password" as const,
    },
    { name: "webhook_url", label: t(`${I}.field.webhookUrl`) },
    { name: "api_host", label: t(`${I}.field.apiHost`) },
    { name: "proxy", label: t(`${I}.field.proxy`) },
    { name: "topic", label: t(`${I}.field.topic`) },
    { name: "destination", label: t(`${I}.field.destination`) },
    {
      name: "agent",
      label: t(`${I}.field.targetAgent`),
      kind: "select" as const,
      options: [
        { label: t(`${I}.agentAnalyst`), value: "资料助理" },
        { label: t(`${I}.agentProject`), value: "项目助理" },
        { label: t(`${I}.agentOps`), value: "值班助理" },
      ],
    },
    {
      name: "response_mode",
      label: t(`${I}.field.responseMode`),
      kind: "select" as const,
      options: [
        { label: t(`${I}.responseStream`), value: "stream" },
        { label: t(`${I}.responseInvoke`), value: "invoke" },
      ],
    },
    {
      name: "show_thinking",
      label: t(`${I}.field.showThinking`),
      kind: "switch" as const,
    },
    {
      name: "show_tool_hints",
      label: t(`${I}.field.showToolHints`),
      kind: "switch" as const,
    },
    {
      name: "enabled",
      label: t(`${I}.field.enabled`),
      kind: "switch" as const,
    },
  ];
  return (
    <div className={styles.section}>
      <DemoBoundary>{t(`${I}.channelDemoBoundary`)}</DemoBoundary>
      <DemoCollection
        storageKey={`integrations.${agentId}.channels`}
        title={title}
        description={t(`${I}.channelDescription`)}
        initialRows={channelRowsForAgent}
        fields={fields}
        canToggle
      />
      <Space wrap>
        <DemoExternalAction
          label={t(`${I}.testChannel`)}
          detail={t(`${I}.channelTestBoundary`)}
        />
        <DemoExternalAction
          label={t(`${I}.sendMessage`)}
          detail={t(`${I}.messageBoundary`)}
        />
      </Space>
    </div>
  );
}
const LIBRARY_VIEWS = ["tree", "atoms", "raw"] as const;

function MemoryDemo({
  agentId,
  item,
  navigate,
}: {
  agentId: string;
  item?: string;
  navigate: DemoPageProps["navigate"];
}) {
  const { t } = useTranslation();
  const requestedView = item?.split(":")[1];
  const [active, setActive] = useState(
    MEMORY_TABS.includes(item?.split(":")[0] as (typeof MEMORY_TABS)[number])
      ? item!.split(":")[0]
      : "memoryOverview",
  );
  const [scope, setScope] = useState("private");
  const [libraryView, setLibraryView] = useState(
    LIBRARY_VIEWS.includes(requestedView as (typeof LIBRARY_VIEWS)[number])
      ? requestedView!
      : "tree",
  );
  const [manualFile, setManualFile] = useState(
    requestedView === "MEMORY.md" ? "MEMORY.md" : "USER.md",
  );
  useEffect(() => {
    const [nextActive, nextView] = item?.split(":") ?? [];
    if (
      nextActive &&
      MEMORY_TABS.includes(nextActive as (typeof MEMORY_TABS)[number])
    ) {
      setActive(nextActive);
      setLibraryView(
        nextActive === "memoryLibrary" &&
          nextView &&
          LIBRARY_VIEWS.includes(nextView as (typeof LIBRARY_VIEWS)[number])
          ? nextView
          : "tree",
      );
      setManualFile(
        nextActive === "memoryManual" && nextView === "MEMORY.md"
          ? "MEMORY.md"
          : "USER.md",
      );
    } else {
      setActive("memoryOverview");
      setLibraryView("tree");
      setManualFile("USER.md");
    }
  }, [item]);
  const initialManualFiles = {
    "USER.md": "# 用户画像\n\n偏好结构化摘要，关注项目节点和负责人。",
    "MEMORY.md":
      "# 长期记忆\n\n项目资料按年度和主题归档；周报包含进展、风险与下周计划。",
  };
  const [manualFiles, setManualFiles] = useDemoState<Record<string, string>>(
    `integrations.${agentId}.${scope}.memory-manual`,
    initialManualFiles,
  );
  const backupSample = JSON.stringify(initialManualFiles);
  const [backups, setBackups] = useDemoState<DemoRow[]>(
    `integrations.${agentId}.${scope}.memory-backups`,
    [
      {
        id: "memory-backup-20261024",
        name: "2026-10-24 日常备份",
        description: "包含用户画像、主题记忆与维护记录",
        enabled: true,
        values: { category: "完整", snapshot: backupSample },
      },
      {
        id: "memory-backup-20261017",
        name: "2026-10-17 周度备份",
        description: "用于演示的历史备份",
        enabled: true,
        values: { category: "完整", snapshot: backupSample },
      },
    ],
  );
  const [restored, setRestored] = useState("");
  const localMemoryRows = rows(
    ["项目交付节奏", "资料归档偏好", "采购流程联系人"],
    [
      "每周五更新项目周报，列出风险及负责人。",
      "按项目/年度/文档类型分类归档。",
      "采购事项需标注审批节点与预算负责人。",
    ],
    ["项目", "偏好", "联系人"],
  );
  const memoryKey = (section: string) =>
    `integrations.${agentId}.${scope}.${section}`;
  const memoryTitle = t(`${I}.tab.memory`);
  let panel;
  if (active === "memoryOverview") {
    panel = (
      <div className={styles.section}>
        <div className={styles.memoryStats}>
          <Card>
            <Typography.Text>{t(`${I}.memoryStatLongTerm`)}</Typography.Text>
            <Typography.Title level={3}>24</Typography.Title>
          </Card>
          <Card>
            <Typography.Text>{t(`${I}.memoryStatCandidates`)}</Typography.Text>
            <Typography.Title level={3}>3</Typography.Title>
          </Card>
          <Card>
            <Typography.Text>{t(`${I}.memoryStatLastReview`)}</Typography.Text>
            <Typography.Title level={3}>10/24</Typography.Title>
          </Card>
        </div>
        <DemoCollection
          storageKey={memoryKey("recent")}
          title={t(`${I}.recentMemoryChanges`)}
          initialRows={localMemoryRows.slice(0, 2)}
          fields={[
            { name: "name", label: t(`${I}.field.name`) },
            {
              name: "description",
              label: t(`${I}.field.description`),
              kind: "textarea",
            },
            { name: "category", label: t(`${I}.field.category`) },
          ]}
        />
      </div>
    );
  } else if (active === "memoryProfile") {
    panel = (
      <DemoSettings
        storageKey={memoryKey("profile")}
        title={t(`${I}.memoryProfileTitle`)}
        description={t(`${I}.memoryDescription`)}
        fields={[
          {
            name: "summary",
            label: t(`${I}.field.memorySummary`),
            kind: "textarea",
            initialValue: t(`${I}.memorySample`),
          },
          {
            name: "preferences",
            label: t(`${I}.field.preferences`),
            kind: "textarea",
            initialValue: t(`${I}.memoryPreferenceSample`),
          },
          {
            name: "reviewed",
            label: t(`${I}.field.lastReviewed`),
            kind: "date",
            initialValue: "2026-10-24",
          },
        ]}
      />
    );
  } else if (active === "memoryLibrary") {
    panel = (
      <div className={styles.section}>
        <Tabs
          value={libraryView}
          options={LIBRARY_VIEWS}
          onChange={(value) => {
            setLibraryView(value);
            navigate("/personalization/memory", active, `${active}:${value}`);
          }}
        />
        <DemoCollection
          storageKey={memoryKey(`library-${libraryView}`)}
          title={t(`${I}.memoryLibraryTitle`)}
          initialRows={
            libraryView === "raw"
              ? rows(
                  ["需求沟通原始素材", "项目复盘原始素材"],
                  ["来自示例对话的未整理片段", "包含问题、决策与后续行动"],
                  ["原始素材", "原始素材"],
                )
              : localMemoryRows
          }
          fields={[
            { name: "name", label: t(`${I}.field.name`) },
            {
              name: "description",
              label: t(`${I}.field.description`),
              kind: "textarea",
            },
            { name: "category", label: t(`${I}.field.category`) },
          ]}
        />
        <Card size="small" title={t(`${I}.memoryLineageTitle`)}>
          <Typography.Paragraph>
            {t(`${I}.memoryLineageDescription`)}
          </Typography.Paragraph>
        </Card>
      </div>
    );
  } else if (active === "memoryManual") {
    panel = (
      <Card title={t(`${I}.manualDocumentsTitle`)}>
        <AntTabs
          activeKey={manualFile}
          items={Object.keys(manualFiles).map((name) => ({
            key: name,
            label: name,
          }))}
          onChange={(value) => {
            setManualFile(value);
            navigate("/personalization/memory", active, `${active}:${value}`);
          }}
        />
        <Input.TextArea
          value={manualFiles[manualFile]}
          autoSize={{ minRows: 12, maxRows: 24 }}
          onChange={(event) =>
            setManualFiles((current) => ({
              ...current,
              [manualFile]: event.target.value,
            }))
          }
        />
        <Typography.Paragraph type="secondary">
          {t(`${I}.manualDocumentsHint`)}
        </Typography.Paragraph>
      </Card>
    );
  } else if (active === "memoryBackups") {
    panel = (
      <div className={styles.section}>
        <Space>
          <Button
            type="primary"
            onClick={() =>
              setBackups((current) => [
                {
                  id: `backup-${Date.now()}`,
                  name: t(`${I}.newBackupName`),
                  description: t(`${I}.backupCreatedLocal`),
                  enabled: true,
                  values: {
                    category: t(`${I}.backupComplete`),
                    snapshot: JSON.stringify(manualFiles),
                  },
                },
                ...current,
              ])
            }
          >
            {t(`${I}.createBackup`)}
          </Button>
          <Button
            onClick={() => {
              const link = document.createElement("a");
              link.href = URL.createObjectURL(
                new Blob(
                  [
                    JSON.stringify(
                      { scope, agentId, manualFiles, backups },
                      null,
                      2,
                    ),
                  ],
                  { type: "application/json" },
                ),
              );
              link.download = "memory-demo-backup.json";
              link.click();
              URL.revokeObjectURL(link.href);
            }}
          >
            {t(`${I}.exportLocalBackup`)}
          </Button>
          <DemoExternalAction
            label={t(`${I}.exportBackup`)}
            detail={t(`${I}.backupExportBoundary`)}
          />
        </Space>
        {restored && (
          <Alert
            type="success"
            message={t(`${I}.backupRestored`, { name: restored })}
          />
        )}
        <DemoCollection
          storageKey={memoryKey("memory-backups")}
          title={t(`${I}.backupsTitle`)}
          initialRows={backups}
          fields={[
            { name: "name", label: t(`${I}.field.name`) },
            {
              name: "description",
              label: t(`${I}.field.description`),
              kind: "textarea",
            },
            { name: "category", label: t(`${I}.field.status`) },
          ]}
          rowActions={[
            {
              key: "restore",
              label: t(`${I}.restoreBackup`),
              onClick: (row) => {
                const snapshot = row.values?.snapshot;
                if (snapshot)
                  setManualFiles(
                    JSON.parse(String(snapshot)) as Record<string, string>,
                  );
                setRestored(row.name);
              },
            },
          ]}
        />
      </div>
    );
  } else if (active === "memoryProactive") {
    panel = (
      <DemoSettings
        storageKey={memoryKey("proactive")}
        title={t(`${I}.proactiveTitle`)}
        fields={[
          {
            name: "enabled",
            label: t(`${I}.field.enabled`),
            kind: "switch",
            initialValue: true,
          },
          {
            name: "quietHours",
            label: t(`${I}.field.quietHours`),
            initialValue: "22:00–08:00",
          },
          {
            name: "reminder",
            label: t(`${I}.field.reminderRule`),
            kind: "textarea",
            initialValue: t(`${I}.proactiveRule`),
          },
        ]}
      />
    );
  } else if (active === "memorySettings") {
    panel = (
      <DemoSettings
        storageKey={memoryKey("maintenance")}
        title={t(`${I}.maintenanceTitle`)}
        fields={[
          {
            name: "autoExtract",
            label: t(`${I}.field.autoExtract`),
            kind: "switch",
            initialValue: true,
          },
          {
            name: "reviewInterval",
            label: t(`${I}.field.reviewInterval`),
            kind: "select",
            options: [
              { label: t(`${I}.weekly`), value: "weekly" },
              { label: t(`${I}.monthly`), value: "monthly" },
            ],
          },
          {
            name: "archiveDays",
            label: t(`${I}.field.archiveDays`),
            kind: "number",
            initialValue: 90,
          },
        ]}
      />
    );
  } else if (active === "memoryPipeline") {
    panel = (
      <Card title={t(`${I}.memoryPipelineTitle`)}>
        <Alert type="info" showIcon message={t(`${I}.memoryPipelineEmpty`)} />
        <Button
          onClick={() => {
            setActive("memoryCandidates");
            navigate("/personalization/memory", "memoryCandidates");
          }}
        >
          {t(`${I}.memoryPipelineAction`)}
        </Button>
      </Card>
    );
  } else if (active === "memoryMigration") {
    panel = (
      <div className={styles.section}>
        <DemoSettings
          storageKey={memoryKey("migration")}
          title={t(`${I}.memoryMigrationTitle`)}
          description={t(`${I}.memoryMigrationDescription`)}
          fields={[
            {
              name: "source",
              label: t(`${I}.field.source`),
              initialValue: "legacy-memory",
            },
            {
              name: "target",
              label: t(`${I}.field.target`),
              initialValue: "agent-memory-v2",
            },
            {
              name: "scope",
              label: t(`${I}.field.scope`),
              initialValue: scope,
            },
          ]}
        />
        <DemoExternalAction
          label={t(`${I}.memoryMigrationAction`)}
          detail={t(`${I}.memoryMigrationBoundary`)}
        />
      </div>
    );
  } else {
    const data =
      active === "memoryEpisodes"
        ? rows(
            ["项目验收后的踏实感", "方案被采纳的成就感"],
            ["记录一次阶段验收情绪与原因", "回顾方案采纳带来的正向反馈"],
            ["情绪日记", "情绪日记"],
          )
        : active === "memoryCandidates"
        ? rows(
            ["客户简称规则", "版本命名约定", "交付评审结论"],
            [
              "待审阅的个人资料候选",
              "可沉淀为长期记忆",
              "来自复盘的结构化结论",
            ],
            ["待审阅", "待审阅", "待审阅"],
          )
        : active === "memoryJournal"
        ? rows(
            ["整理：项目资料偏好", "维护：合并重复联系人"],
            ["由候选记忆生成并保留来源", "移除过期联系信息，保留变更记录"],
            ["整理记录", "维护记录"],
          )
        : rows(
            ["项目启动会", "月度计划回顾"],
            ["议题、结论与行动项示例", "确认节点、依赖和风险"],
            ["对话记录", "对话记录"],
          );
    panel = (
      <DemoCollection
        storageKey={memoryKey(active)}
        title={t(`${I}.${active}`)}
        initialRows={data}
        fields={[
          { name: "name", label: t(`${I}.field.name`) },
          {
            name: "description",
            label: t(`${I}.field.description`),
            kind: "textarea",
          },
          { name: "category", label: t(`${I}.field.category`) },
        ]}
        canToggle={active === "memoryCandidates"}
      />
    );
  }
  return (
    <div className={styles.section}>
      <Typography.Title level={5}>{memoryTitle}</Typography.Title>
      <Segmented
        value={scope}
        onChange={(value) => setScope(String(value))}
        options={[
          { value: "private", label: t(`${I}.memoryPrivate`) },
          { value: "shared", label: t(`${I}.memoryShared`) },
        ]}
      />
      <Tabs
        value={active}
        options={MEMORY_TABS}
        onChange={(value) => {
          setActive(value);
          navigate("/personalization/memory", value);
        }}
      />
      {panel}
    </div>
  );
}

function Connectors({ tab }: { tab: string }) {
  const { t } = useTranslation();
  const custom = tab === "custom";
  const [connectorSettings, setConnectorSettings] = useDemoState<
    Record<string, { authMode: string; credential: string; endpoint: string }>
  >("integrations.connector-settings", {});
  const [selected, setSelected] = useState<DemoRow | null>(null);
  const [authMode, setAuthMode] = useState("oauth2");
  const [credential, setCredential] = useState("demo-token-not-real");
  const [endpoint, setEndpoint] = useState("https://service.example.test/mcp");
  const [scenario, setScenario] = useState("configured");
  const [probeResult, setProbeResult] = useState(false);
  const localTitle = (key: string) => t(`${I}.connectorJourney.${key}`);
  const openConnector = (row: DemoRow) => {
    const saved = connectorSettings[row.id];
    setAuthMode(
      saved?.authMode ??
        (row.values?.category === "自定义" ? "custom" : "oauth2"),
    );
    setCredential(saved?.credential ?? "demo-token-not-real");
    setEndpoint(saved?.endpoint ?? "https://service.example.test/mcp");
    setSelected(row);
  };
  return (
    <div className={styles.section}>
      <Space wrap>
        <Select
          value={scenario}
          onChange={setScenario}
          style={{ width: 210 }}
          options={["configured", "missingCredentials", "readOnly"].map(
            (value) => ({ value, label: localTitle(`scenario.${value}`) }),
          )}
        />
        <Button onClick={() => openConnector(connectorRows[3])}>
          {localTitle("configureCustom")}
        </Button>
      </Space>
      {custom && (
        <DemoBoundary>{t(`${I}.customConnectorBoundary`)}</DemoBoundary>
      )}
      {scenario === "missingCredentials" && (
        <Alert
          type="warning"
          showIcon
          message={localTitle("credentialsRequired")}
        />
      )}
      {scenario === "readOnly" && (
        <Alert type="info" showIcon message={localTitle("readOnly")} />
      )}
      {Object.keys(connectorSettings).length > 0 && (
        <Alert
          type="success"
          showIcon
          message={`${localTitle("locallyConfigured")}: ${
            Object.keys(connectorSettings).length
          }`}
        />
      )}
      <DemoCollection
        storageKey="integrations.connectors"
        title={t(`${I}.connectorHeading`)}
        initialRows={connectorRows}
        rowFilter={(row) =>
          tab === "enabled"
            ? Boolean(row.enabled)
            : custom
            ? row.values?.category === "自定义"
            : row.values?.category !== "自定义"
        }
        fields={[
          { name: "name", label: t(`${I}.field.name`), required: true },
          {
            name: "description",
            label: t(`${I}.field.description`),
            kind: "textarea",
          },
          { name: "category", label: t(`${I}.field.category`) },
          { name: "enabled", label: t(`${I}.field.enabled`), kind: "switch" },
        ]}
        canToggle={scenario !== "readOnly"}
        canEdit={scenario !== "readOnly"}
        canDelete={scenario !== "readOnly"}
        rowActions={[
          {
            key: "configure",
            label: localTitle("configure"),
            onClick: openConnector,
          },
        ]}
      />
      <Space wrap>
        {[
          t(`${I}.oauth`),
          t(`${I}.devicePairing`),
          t(`${I}.serviceInstall`),
        ].map((label) => (
          <DemoExternalAction
            key={label}
            label={label}
            detail={t(`${I}.connectorExternalDetail`)}
          />
        ))}
      </Space>
      <Drawer
        open={selected !== null}
        title={selected?.name}
        onClose={() => {
          setSelected(null);
          setProbeResult(false);
        }}
        width={420}
      >
        <Alert
          type="info"
          showIcon
          message={localTitle("secretMasked")}
          description={localTitle("oauthBoundary")}
        />
        <Typography.Paragraph>{localTitle("authKind")}</Typography.Paragraph>
        <Select
          value={authMode}
          onChange={setAuthMode}
          style={{ width: "100%", marginBottom: 12 }}
          options={[
            {
              value: "personal-token",
              label: localTitle("auth.personalToken"),
            },
            { value: "oauth2", label: localTitle("auth.oauth2") },
            { value: "auth-code", label: localTitle("auth.authCode") },
            { value: "api-key", label: localTitle("auth.apiKey") },
            { value: "imap-password", label: localTitle("auth.imapPassword") },
            {
              value: "api-credentials",
              label: localTitle("auth.apiCredentials"),
            },
            { value: "custom", label: localTitle("auth.custom") },
          ]}
        />
        <Input.Password
          value={credential}
          onChange={(event) => setCredential(event.target.value)}
          placeholder={localTitle("credentialPlaceholder")}
        />
        <Input
          style={{ marginTop: 12 }}
          value={endpoint}
          onChange={(event) => setEndpoint(event.target.value)}
          aria-label={localTitle("endpoint")}
        />
        <Space wrap style={{ marginTop: 16 }}>
          <Button
            disabled={
              scenario === "readOnly" || scenario === "missingCredentials"
            }
            onClick={() => setProbeResult(true)}
          >
            {localTitle("testConnection")}
          </Button>
          <Button
            type="primary"
            disabled={scenario === "readOnly"}
            onClick={() => {
              if (selected)
                setConnectorSettings((current) => ({
                  ...current,
                  [selected.id]: { authMode, credential, endpoint },
                }));
              setSelected(null);
              setProbeResult(false);
            }}
          >
            {localTitle("saveLocal")}
          </Button>
        </Space>
        {probeResult && (
          <Alert
            style={{ marginTop: 16 }}
            type="warning"
            showIcon
            message={localTitle("probeNotRun")}
            description={localTitle("probeBoundary")}
          />
        )}
      </Drawer>
      <Typography.Paragraph className={styles.muted}>
        {t(`${I}.localStateHint`)}
      </Typography.Paragraph>
    </div>
  );
}

function Packages({
  tab: selectedTab,
  navigate,
  agentId,
}: {
  tab: string;
  navigate: DemoPageProps["navigate"];
  agentId: string;
}) {
  const { t } = useTranslation();
  const [packages, setPackages] = useDemoState(
    "integrations.packages-list",
    packageRows,
  );
  const [selected, setSelected] = useDemoState(
    "integrations.selected-package",
    packageRows[0].id,
  );
  const [files, setFiles] = useDemoState<Record<string, string>>(
    `integrations.package-files.${selected}`,
    {
      "SKILL.md":
        "---\nname: project-weekly\ndescription: 项目周报整理示例\n---\n\n# 周报整理\n\n归纳本周进展、风险与下周计划。",
      "references/template.md":
        "# 周报模板\n\n## 本周进展\n## 风险与阻塞\n## 下周计划",
      "skills/weekly-plan/SKILL.md":
        "---\nname: weekly-plan\ndescription: 整理项目周报\n---\n\n将进展、风险和下一步计划归纳为周报。",
      "skills/risk-register/SKILL.md":
        "---\nname: risk-register\ndescription: 维护风险清单\n---\n\n列出风险、影响范围、负责人和缓解措施。",
      "skills/meeting-notes/SKILL.md":
        "---\nname: meeting-notes\ndescription: 结构化会议纪要\n---\n\n记录议题、决议、负责人和截止日期。",
    },
  );
  const [activeFile, setActiveFile] = useState("SKILL.md");
  const [tab, setTab] = useState(selectedTab);
  const [exported, setExported] = useState(false);
  const [importResult, setImportResult] = useState("");
  const [importBusy, setImportBusy] = useState(false);
  const [packagePermission, setPackagePermission] = useState("writable");
  const [draft, setDraft] = useState("");
  const [pendingImportFiles, setPendingImportFiles] = useState<Record<
    string,
    string
  > | null>(null);
  useEffect(() => {
    if (selected.startsWith("pkg-import-") && pendingImportFiles) {
      setFiles(pendingImportFiles);
      setPendingImportFiles(null);
    }
  }, [selected, pendingImportFiles, setFiles]);
  useEffect(
    () => setDraft(files[activeFile] ?? ""),
    [selected, activeFile, files],
  );
  const saveFile = () => {
    setFiles((current) => ({ ...current, [activeFile]: draft }));
    message.success(t(`${I}.fileSaved`));
  };
  useEffect(() => setTab(selectedTab), [selectedTab]);
  const addPackage = () => {
    const next: DemoRow = {
      id: `pkg-local-${Date.now()}`,
      name: t(`${I}.newPackageName`),
      description: t(`${I}.newPackageDescription`),
      enabled: true,
      values: { category: t(`${I}.newPackageCount`) },
    };
    setPackages((current) => [...current, next]);
    setSelected(next.id);
  };
  const importPackageFile = async (file?: File) => {
    if (!file) return;
    setImportBusy(true);
    setImportResult(t(`${I}.packageImporting`));
    try {
      if (!file.name.toLowerCase().endsWith(".json"))
        throw new Error("unsupported");
      const parsed: unknown = JSON.parse(await file.text());
      if (!parsed || typeof parsed !== "object") throw new Error("invalid");
      const bundle = parsed as {
        package?: DemoRow;
        files?: Record<string, unknown>;
      };
      if (
        !bundle.package ||
        typeof bundle.package.name !== "string" ||
        !bundle.files ||
        typeof bundle.files !== "object" ||
        Object.values(bundle.files).some((value) => typeof value !== "string")
      )
        throw new Error("invalid");
      const imported = { ...bundle.package, id: `pkg-import-${Date.now()}` };
      setPackages((current) => [...current, imported]);
      setPendingImportFiles(bundle.files as Record<string, string>);
      setSelected(imported.id);
      setImportResult(t(`${I}.packageImportComplete`));
    } catch {
      setImportResult(t(`${I}.packageImportFailed`));
    } finally {
      setImportBusy(false);
    }
  };
  const exportPackage = () => {
    const bundle = JSON.stringify(
      { package: packages.find((row) => row.id === selected), files },
      null,
      2,
    );
    const link = document.createElement("a");
    link.href = URL.createObjectURL(
      new Blob([bundle], { type: "application/json" }),
    );
    link.download = "skill-package-demo.json";
    link.click();
    URL.revokeObjectURL(link.href);
  };
  return (
    <div className={styles.packageLayout}>
      <Card title={t(`${I}.packageList`)} className={styles.packageList}>
        {packages.map((row) => (
          <Button
            key={row.id}
            block
            type={selected === row.id ? "primary" : "text"}
            onClick={() => setSelected(row.id)}
          >
            {row.name}
            <Tag>{String(row.values?.category)}</Tag>
          </Button>
        ))}
        <Button
          className={styles.addButton}
          disabled={packagePermission === "readOnly"}
          onClick={addPackage}
        >
          {t(`${I}.addPackage`)}
        </Button>
      </Card>
      <div className={styles.packageDetail}>
        <Tabs
          value={tab}
          options={["packages", "skills", "market"]}
          onChange={(value) => {
            setTab(value);
            navigate("/skill-packages", value);
          }}
        />
        <Space wrap>
          <Select
            value={packagePermission}
            onChange={setPackagePermission}
            style={{ width: 200 }}
            options={["writable", "readOnly", "exportDisabled"].map(
              (value) => ({ value, label: t(`${I}.packageJourney.${value}`) }),
            )}
          />
          {packagePermission !== "writable" && (
            <Alert
              type="warning"
              showIcon
              message={t(`${I}.packageJourney.permissionNotice`)}
            />
          )}
        </Space>
        {tab === "market" ? (
          <SkillMarket agentId={agentId} />
        ) : (
          <>
            {tab === "packages" ? (
              <>
                <Card
                  title={t(`${I}.packageDetailTitle`)}
                  extra={
                    <Space>
                      <label className={styles.filePicker}>
                        {t(`${I}.importPackage`)}
                        <input
                          type="file"
                          accept=".json,.zip"
                          disabled={
                            importBusy || packagePermission === "readOnly"
                          }
                          onChange={(event) => {
                            void importPackageFile(event.target.files?.[0]);
                            event.currentTarget.value = "";
                          }}
                        />
                      </label>
                      <Button
                        disabled={packagePermission === "exportDisabled"}
                        onClick={() => {
                          exportPackage();
                          setExported(true);
                        }}
                      >
                        {t(`${I}.exportPackage`)}
                      </Button>
                    </Space>
                  }
                >
                  {exported && (
                    <Alert
                      type="success"
                      showIcon
                      message={t(`${I}.exportReady`)}
                    />
                  )}
                  {importResult && (
                    <Alert
                      type={
                        importResult.includes(t(`${I}.packageImportComplete`))
                          ? "success"
                          : importBusy
                          ? "info"
                          : "error"
                      }
                      showIcon
                      message={importResult}
                    />
                  )}
                </Card>
                {packagePermission === "writable" ? (
                  <DemoSettings
                    key={selected}
                    storageKey={`integrations.package-meta.${selected}`}
                    title={t(`${I}.editPackage`)}
                    initialValues={{
                      name:
                        packages.find((row) => row.id === selected)?.name ?? "",
                      description:
                        packages.find((row) => row.id === selected)
                          ?.description ?? "",
                    }}
                    fields={[
                      {
                        name: "name",
                        label: t(`${I}.field.name`),
                        required: true,
                      },
                      {
                        name: "description",
                        label: t(`${I}.field.description`),
                        kind: "textarea",
                      },
                    ]}
                    onSaved={(values: DemoValues) =>
                      setPackages((current) =>
                        current.map((row) =>
                          row.id === selected
                            ? {
                                ...row,
                                name: String(values.name ?? row.name),
                                description: String(values.description ?? ""),
                              }
                            : row,
                        ),
                      )
                    }
                  />
                ) : (
                  <Alert
                    type="warning"
                    showIcon
                    message={t(`${I}.packageJourney.permissionNotice`)}
                  />
                )}
              </>
            ) : (
              <>
                <Card title={t(`${I}.packageSkills`)}>
                  <Space wrap>
                    {["weekly-plan", "risk-register", "meeting-notes"].map(
                      (slug) => (
                        <Button
                          key={slug}
                          type={
                            activeFile === `skills/${slug}/SKILL.md`
                              ? "primary"
                              : "default"
                          }
                          onClick={() =>
                            setActiveFile(`skills/${slug}/SKILL.md`)
                          }
                        >
                          {slug}
                        </Button>
                      ),
                    )}
                  </Space>
                </Card>
                <Card
                  className={styles.editorCard}
                  title={
                    <Space>
                      <FileCode2 />
                      {activeFile}
                    </Space>
                  }
                  extra={
                    <Button
                      type="primary"
                      disabled={packagePermission === "readOnly"}
                      onClick={saveFile}
                    >
                      {t(`${I}.saveFile`)}
                    </Button>
                  }
                >
                  <div className={styles.fileEditor}>
                    <nav>
                      {Object.keys(files).map((file) => (
                        <Button
                          key={file}
                          type={activeFile === file ? "primary" : "text"}
                          block
                          onClick={() => setActiveFile(file)}
                        >
                          {file}
                        </Button>
                      ))}
                    </nav>
                    <Input.TextArea
                      disabled={packagePermission === "readOnly"}
                      value={draft}
                      autoSize={{ minRows: 14, maxRows: 24 }}
                      onChange={(event) => setDraft(event.target.value)}
                    />
                  </div>
                </Card>
              </>
            )}
          </>
        )}
      </div>
    </div>
  );
}

function Workbench({ tab }: { tab: string }) {
  const { t } = useTranslation();
  const [command, setCommand] = useState("ls");
  const [log, setLog] = useDemoState("integrations.terminal-log", [
    "示例终端 · 本地虚构工作区",
    "$ ls",
    "docs/  notes.md  report.md",
  ]);
  const [url, setUrl] = useState("https://example.com/project");
  const [address, setAddress] = useDemoState(
    "integrations.browser-address",
    "https://example.com/project",
  );
  const executeExample = () => {
    if (sessionState !== "ready") return;
    const result: Record<string, string> = {
      ls: "docs/  notes.md  report.md",
      pwd: "/demo/workspace",
      "cat notes.md": "项目周报：已完成目录整理；下周补充风险清单。",
      "cat report.md": "演示报告\n状态：草稿\n仅存在于浏览器本地示例。",
    };
    setLog((prev) => [
      ...prev,
      `$ ${command}`,
      result[command.trim()] ??
        "仅支持示例命令：ls、pwd、cat notes.md、cat report.md",
    ]);
  };
  const [sessionState, setSessionState] = useState("ready");
  const journey = (key: string) => t(`${I}.workbenchJourney.${key}`);
  return (
    <div className={styles.section}>
      <Space wrap>
        <Select
          value={sessionState}
          onChange={setSessionState}
          options={[
            "ready",
            "connecting",
            "disconnected",
            "failed",
            "permission",
          ].map((value) => ({ value, label: journey(`state.${value}`) }))}
          style={{ width: 200 }}
        />
        <Typography.Text type="secondary">
          {journey("noRuntime")}
        </Typography.Text>
      </Space>
      {sessionState !== "ready" && (
        <Alert
          showIcon
          type={
            sessionState === "failed"
              ? "error"
              : sessionState === "permission"
              ? "warning"
              : "info"
          }
          message={journey(`state.${sessionState}`)}
          action={
            <Button size="small" onClick={() => setSessionState("ready")}>
              {journey("retryLocal")}
            </Button>
          }
        />
      )}
      <DemoBoundary>{t(`${I}.workbenchBoundary`)}</DemoBoundary>
      {tab === "terminal" ? (
        <Card
          title={
            <Space>
              <TerminalSquare />
              {t(`${I}.terminalTitle`)}
            </Space>
          }
        >
          <div className={styles.terminalLog}>
            {log.map((line, index) => (
              <div key={`${index}-${line}`}>{line}</div>
            ))}
          </div>
          <Space.Compact className={styles.commandInput}>
            <Input
              value={command}
              onChange={(event) => setCommand(event.target.value)}
              onPressEnter={executeExample}
              aria-label={t(`${I}.commandPlaceholder`)}
            />
            <Button
              type="primary"
              disabled={sessionState !== "ready"}
              onClick={executeExample}
            >
              {t(`${I}.runSample`)}
            </Button>
          </Space.Compact>
          <Typography.Text type="secondary">
            {t(`${I}.supportedCommands`)}
          </Typography.Text>
        </Card>
      ) : (
        <Card
          title={
            <Space>
              <Globe />
              {t(`${I}.browserTitle`)}
            </Space>
          }
        >
          <Space.Compact className={styles.commandInput}>
            <Input
              value={url}
              onChange={(event) => setUrl(event.target.value)}
              aria-label={t(`${I}.addressPlaceholder`)}
            />
            <Button type="primary" onClick={() => setAddress(url)}>
              {t(`${I}.showSample`)}
            </Button>
          </Space.Compact>
          <div className={styles.browserFrame}>
            <div className={styles.browserChrome}>
              <span>● ● ●</span>
              <span>{address}</span>
            </div>
            <div className={styles.browserContent}>
              <Globe size={32} />
              <Typography.Title level={4}>
                {t(`${I}.samplePageTitle`)}
              </Typography.Title>
              <Typography.Paragraph>
                {t(`${I}.samplePageDescription`)}
              </Typography.Paragraph>
              <Typography.Paragraph type="secondary">
                {journey("browserStatic")}
              </Typography.Paragraph>
            </div>
          </div>
        </Card>
      )}
    </div>
  );
}

function Remote({
  tab,
  phoneTab,
  navigate,
}: {
  tab: string;
  phoneTab: string;
  navigate: DemoPageProps["navigate"];
}) {
  const { t } = useTranslation();
  const isPhone = tab === "phone";
  const [phoneView, setPhoneView] = useState(
    phoneTab === "shell" ? "shell" : "screen",
  );
  useEffect(
    () => setPhoneView(phoneTab === "shell" ? "shell" : "screen"),
    [phoneTab],
  );
  const [files, setFiles] = useDemoState<Record<string, string>>(
    "integrations.remote-files",
    {
      "/sdcard/Download/notes.txt":
        "本地示例手机文件\n演示内容仅保存在浏览器内存。",
    },
  );
  const [shell, setShell] = useState("ls /sdcard/Download");
  const [shellLog, setShellLog] = useDemoState("integrations.phone-shell-log", [
    "示例 Shell · 未连接设备",
    "$ ls /sdcard/Download",
    "notes.txt",
  ]);
  const [remoteState, setRemoteState] = useState("idle");
  const remoteJourney = (key: string) => t(`${I}.remoteJourney.${key}`);
  const runShell = () =>
    setShellLog((prev) => [
      ...prev,
      `$ ${shell}`,
      shell.trim() === "cat /sdcard/Download/notes.txt"
        ? files["/sdcard/Download/notes.txt"]
        : shell.trim() === "ls /sdcard/Download"
        ? "notes.txt  receipts/  photos/"
        : t(`${I}.shellSupported`),
    ]);
  return (
    <div className={styles.section}>
      <Space wrap>
        <Select
          value={remoteState}
          onChange={setRemoteState}
          options={[
            "idle",
            "connecting",
            "permission",
            "failed",
            "connected",
          ].map((value) => ({ value, label: remoteJourney(`state.${value}`) }))}
          style={{ width: 210 }}
        />
        <Typography.Text type="secondary">
          {remoteJourney("noDeviceRuntime")}
        </Typography.Text>
      </Space>
      {remoteState !== "idle" && (
        <Alert
          showIcon
          type={
            remoteState === "failed"
              ? "error"
              : remoteState === "permission"
              ? "warning"
              : "info"
          }
          message={remoteJourney(`state.${remoteState}`)}
          action={
            <Button size="small" onClick={() => setRemoteState("idle")}>
              {remoteJourney("reset")}
            </Button>
          }
        />
      )}
      <DemoBoundary>{t(`${I}.remoteBoundary`)}</DemoBoundary>
      <Space wrap>
        <DemoExternalAction
          label={t(`${I}.connectDevice`)}
          detail={t(`${I}.deviceBoundary`)}
        />
        <DemoExternalAction
          label={t(`${I}.installAgent`)}
          detail={t(`${I}.installBoundary`)}
        />
      </Space>
      {!isPhone ? (
        <div className={styles.remoteScreen}>
          <div className={styles.remoteToolbar}>
            <Tag color="blue">{t(`${I}.sampleScreenBadge`)}</Tag>
            <Button onClick={() => message.info(t(`${I}.screenBoundary`))}>
              {t(`${I}.refreshFrame`)}
            </Button>
          </div>
          <div className={styles.desktopPreview}>
            <div className={styles.desktopDock}>
              <span>▦</span>
              <span>◈</span>
              <span>●</span>
            </div>
            <div>
              <Typography.Title level={3}>
                {t(`${I}.desktopSampleTitle`)}
              </Typography.Title>
              <Typography.Paragraph>
                {t(`${I}.desktopSampleDescription`)}
              </Typography.Paragraph>
              <Button onClick={() => message.info(t(`${I}.screenBoundary`))}>
                {t(`${I}.openFileSample`)}
              </Button>
            </div>
          </div>
        </div>
      ) : (
        <>
          <Tabs
            value={phoneView}
            options={PHONE_TABS}
            onChange={(value) => {
              setPhoneView(value);
              navigate("/remote-desktop/phone", value);
            }}
          />
          {phoneView === "screen" ? (
            <div className={styles.phoneStage}>
              <div className={styles.phoneDevice}>
                <div className={styles.phoneStatus}>9:41 ••• 87%</div>
                <div className={styles.phoneApp}>
                  <div className={styles.phoneIcon}>▣</div>
                  <b>{t(`${I}.phoneSampleTitle`)}</b>
                  <p>{t(`${I}.phoneSampleDescription`)}</p>
                  <Button
                    size="small"
                    onClick={() => message.info(t(`${I}.screenBoundary`))}
                  >
                    {t(`${I}.screenAction`)}
                  </Button>
                </div>
                <div className={styles.phoneNav} />
              </div>
            </div>
          ) : (
            <Card
              title={
                <Space>
                  <TerminalSquare />
                  {t(`${I}.phoneShellTitle`)}
                </Space>
              }
            >
              <div className={styles.terminalLog}>
                {shellLog.map((line, index) => (
                  <div key={`${index}-${line}`}>{line}</div>
                ))}
              </div>
              <Space.Compact className={styles.commandInput}>
                <Input
                  value={shell}
                  onChange={(event) => setShell(event.target.value)}
                  onPressEnter={runShell}
                />
                <Button type="primary" onClick={runShell}>
                  {t(`${I}.runSample`)}
                </Button>
              </Space.Compact>
              <Typography.Text type="secondary">
                {t(`${I}.shellSupported`)}
              </Typography.Text>
              <Card
                className={styles.fileEdit}
                title={t(`${I}.localFileEditor`)}
              >
                <Input.TextArea
                  value={files["/sdcard/Download/notes.txt"]}
                  autoSize={{ minRows: 3 }}
                  onChange={(event) =>
                    setFiles({
                      "/sdcard/Download/notes.txt": event.target.value,
                    })
                  }
                />
              </Card>
            </Card>
          )}
        </>
      )}
    </div>
  );
}

function Acp({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const [enabled, setEnabled] = useDemoState(
    `integrations.${agentId}.acp-tool`,
    true,
  );
  const fields = [
    { name: "name", label: t(`${I}.field.runnerName`), required: true },
    {
      name: "description",
      label: t(`${I}.field.description`),
      kind: "textarea" as const,
    },
    {
      name: "command",
      label: t(`${I}.field.command`),
      initialValue: "node ./examples/runner.js",
    },
    { name: "args", label: t(`${I}.field.arguments`), initialValue: "--stdio" },
    {
      name: "workingDirectory",
      label: t(`${I}.field.workingDirectory`),
      initialValue: "./workspace",
    },
    {
      name: "environment",
      label: t(`${I}.field.environment`),
      kind: "textarea" as const,
      initialValue: "NODE_ENV=demo",
    },
    {
      name: "trusted",
      label: t(`${I}.field.trusted`),
      kind: "switch" as const,
      initialValue: true,
    },
    {
      name: "parseMode",
      label: t(`${I}.field.parseMode`),
      kind: "select" as const,
      options: ["call_title", "update_detail", "call_detail"].map((value) => ({
        label: value,
        value,
      })),
    },
    {
      name: "bufferLimit",
      label: t(`${I}.field.bufferLimit`),
      kind: "number" as const,
      initialValue: 52428800,
    },
  ];
  return (
    <div className={styles.section}>
      <DemoBoundary>{t(`${I}.acpBoundary`)}</DemoBoundary>
      <DemoCollection
        storageKey="integrations.acp-runners"
        title={t(`${I}.runnerHeading`)}
        initialRows={acpRows}
        fields={fields}
        canToggle
      />
      <DemoSettings
        storageKey={`integrations.${agentId}.acp-settings`}
        title={t(`${I}.agentToolSettings`)}
        fields={[
          {
            name: "toolEnabled",
            label: t(`${I}.field.enabled`),
            kind: "switch",
            initialValue: enabled,
          },
          {
            name: "allowFileTools",
            label: t(`${I}.field.allowFileTools`),
            kind: "switch",
            initialValue: true,
          },
          {
            name: "allowNetworkTools",
            label: t(`${I}.field.allowNetworkTools`),
            kind: "switch",
            initialValue: false,
          },
        ]}
        onSaved={(values: DemoValues) =>
          setEnabled(Boolean(values.toolEnabled))
        }
      />
      <Alert type="info" showIcon message={t(`${I}.runnerNoExecution`)} />
    </div>
  );
}

export function IntegrationPages(props: DemoPageProps) {
  const { t } = useTranslation();
  const { path, navigate } = props;
  const [agentId, setAgentId] = useDemoState(
    "integrations.active-agent",
    "agent-project",
  );
  const isPersonal = path.startsWith("/personalization");
  const isConnector = path === "/connectors";
  const isPackages = path === "/skill-packages";
  const isWorkbench = path.startsWith("/workbench");
  const isRemote = path.startsWith("/remote-desktop");
  const isAcp = path === "/acp";
  const parentTab = isPersonal
    ? currentTab(path, props.tab, PERSONAL_TABS, "skills")
    : isConnector
    ? currentTab(path, props.tab, CONNECTOR_TABS, "enabled")
    : isWorkbench
    ? currentTab(path, props.tab, WORKBENCH_TABS, "browser")
    : isRemote
    ? path.includes("/phone")
      ? "phone"
      : currentTab(path, props.tab, REMOTE_TABS, "desktop")
    : "";
  const [connectorTab, setConnectorTab] = useState(parentTab);
  const [workbenchTab, setWorkbenchTab] = useState(parentTab);
  const [remoteTab, setRemoteTab] = useState(parentTab);
  useEffect(() => {
    setConnectorTab(parentTab);
    setWorkbenchTab(parentTab);
    setRemoteTab(parentTab);
  }, [parentTab]);
  const titleKey = isPersonal
    ? "personalizationTitle"
    : isConnector
    ? "connectorsTitle"
    : isPackages
    ? "packagesTitle"
    : isWorkbench
    ? "workbenchTitle"
    : isRemote
    ? "remoteTitle"
    : "acpTitle";
  const title = t(`${I}.${titleKey}`);
  const content = isPersonal ? (
    <Personalization
      tab={parentTab}
      item={
        props.item ??
        (props.tab && !PERSONAL_TABS.some((tab) => tab === props.tab)
          ? props.tab
          : undefined)
      }
      navigate={navigate}
      agentId={agentId}
    />
  ) : isConnector ? (
    <div className={styles.section}>
      <Tabs
        value={connectorTab}
        options={CONNECTOR_TABS}
        onChange={(value) => {
          setConnectorTab(value);
          routeTab("/connectors", value, navigate);
        }}
      />
      <Connectors tab={connectorTab} />
    </div>
  ) : isPackages ? (
    <Packages
      tab={props.tab ?? "packages"}
      navigate={navigate}
      agentId={agentId}
    />
  ) : isWorkbench ? (
    <Workbench tab={workbenchTab} />
  ) : isRemote ? (
    <Remote
      tab={remoteTab}
      phoneTab={currentTab(path, props.tab, PHONE_TABS, "screen")}
      navigate={navigate}
    />
  ) : (
    <Acp agentId={agentId} />
  );
  const subtitleKey = isPersonal
    ? "personalizationSubtitle"
    : isConnector
    ? "connectorsSubtitle"
    : isPackages
    ? "packagesSubtitle"
    : isWorkbench
    ? "workbenchSubtitle"
    : isRemote
    ? "remoteSubtitle"
    : "acpSubtitle";
  const pathTabValues = isPersonal
    ? PERSONAL_TABS
    : isWorkbench
    ? WORKBENCH_TABS
    : isRemote
    ? REMOTE_TABS
    : [];
  const pathTabs = pathTabValues.length
    ? {
        value: parentTab,
        options: pathTabValues.map((value) => ({
          value,
          label: t(`${I}.tab.${value}`),
          icon: tabIcon(value),
        })),
        onChange: (raw: string | number) => {
          const value = String(raw);
          if (isPersonal) navigate(`/personalization/${value}`);
          else if (isWorkbench) navigate("/workbench", value);
          else if (value === "phone")
            navigate("/remote-desktop/phone", "screen");
          else navigate("/remote-desktop", value);
        },
      }
    : undefined;
  return (
    <PageShell
      title={title}
      subtitle={t(`${I}.${subtitleKey}`)}
      agentScoped={isPersonal || isAcp}
      pathTabs={pathTabs}
    >
      {isPersonal && (
        <div className={styles.agentSelector}>
          <Typography.Text>{t(`${I}.personalizationAgent`)}</Typography.Text>
          <Select
            value={agentId}
            onChange={setAgentId}
            options={[
              { value: "agent-project", label: t(`${I}.agentProject`) },
              { value: "agent-analyst", label: t(`${I}.agentAnalyst`) },
              { value: "agent-ops", label: t(`${I}.agentOps`) },
            ]}
          />
        </div>
      )}
      {content}
    </PageShell>
  );
}

export default IntegrationPages;
