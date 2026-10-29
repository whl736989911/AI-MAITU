import { useEffect, useState } from "react";
import {
  Alert,
  Avatar,
  Button,
  Card,
  Form,
  Input,
  Modal,
  Popconfirm,
  Select,
  InputNumber,
  Space,
  Table,
  Tabs,
  Tag,
  Typography,
} from "antd";
import { useTranslation } from "react-i18next";
import { ArrowLeft, Plus, Send, Users } from "lucide-react";
import { AgentCard } from "../../Experts/components/AgentCard";
import type { OctopAgent } from "../../../context/AgentContext";
import { registerPreviewApi } from "./previewTransport";
import PageShell from "../../../layouts/PageShell";
import type { FeatureWorkflow } from "../../../api/modules/featureWorkflow";
import type { PreviewRecord } from "../model";
import type { DemoPageDefinition, DemoPageProps, DemoRow } from "./contract";
import {
  DemoBoundary,
  DemoCollection,
  DemoExternalAction,
  useDemoState,
} from "./DemoKit";
import FeatureDataDemo from "./FeatureDataDemo";
import FeatureConversationDemo from "./FeatureConversationDemo";
import WorkflowDemo from "./WorkflowDemo";
import styles from "./WorkspacePages.module.less";

const { Text, Paragraph } = Typography;
const featureExamples: DemoRow[] = [
  {
    id: "feature-contract",
    name: "合同审阅助手",
    description: "提取合同条款并生成风险提示",
    enabled: true,
    values: { category: "法务", updated: "今天 10:20" },
  },
  {
    id: "feature-inspection",
    name: "设备检定助手",
    description: "维护设备检定台账、到期筛选与证书引用",
    enabled: true,
    values: { category: "质量管理", updated: "昨天 16:40" },
  },
  {
    id: "feature-report",
    name: "月度经营简报",
    description: "汇总业务对象并生成简报草稿",
    enabled: false,
    values: { category: "分析", updated: "10月18日" },
  },
];
const expertExamples: DemoRow[] = [
  {
    id: "expert-research",
    name: "行业研究专家",
    description: "梳理行业趋势、竞品与公开资料",
    enabled: true,
    values: {
      model: "示例模型 A",
      skills: "研究 · 汇总",
      instructions: "优先给出来源范围和结论依据。",
      tools: "知识检索、摘要整理",
      temperature: 0.3,
    },
  },
  {
    id: "expert-writing",
    name: "文案编辑专家",
    description: "润色中文内容并适配目标受众",
    enabled: true,
    values: {
      model: "示例模型 B",
      skills: "写作 · 校对",
      instructions: "先保留事实，再调整表达。",
      tools: "文案润色、术语检查",
      temperature: 0.5,
    },
  },
  {
    id: "expert-analyst",
    name: "数据分析专家",
    description: "解释指标变化并整理分析结论",
    enabled: false,
    values: {
      model: "自动选择",
      skills: "分析 · 可视化",
      instructions: "说明样本范围和不确定性。",
      tools: "指标解释、趋势整理",
      temperature: 0.2,
    },
  },
];
const expertAgentFixtures: OctopAgent[] = expertExamples.map((row, index) => ({
  id: index + 1,
  agent_id: row.id,
  kind: "agent",
  user_id: 1,
  is_owner: true,
  can_manage: true,
  name: row.name,
  description: row.description ?? null,
  persona_mbti: null,
  default_model: String(row.values?.model || ""),
  system_prompt: String(row.values?.instructions || ""),
  template_name: null,
  state: index === 2 ? "failed" : "stopped",
  last_error:
    index === 2 ? "示例模型配置不可用；本地演示未发起模型请求。" : null,
  icon: null,
  icon_name: index === 0 ? "research" : index === 1 ? "writing" : "analysis",
  icon_url: null,
  color: "#5b8ff9",
  config: {
    temperature: row.values?.temperature ?? 0.3,
    skills: String(row.values?.skills || ""),
    tools: String(row.values?.tools || ""),
  },
}));
const expertState = new Map(
  expertAgentFixtures.map((agent) => [agent.agent_id, agent.state]),
);
registerPreviewApi("workspace-expert-lifecycle", (request) => {
  const url = new URL(request.url);
  const parts = url.pathname.split("/");
  if (
    parts[1] !== "api" ||
    parts[2] !== "agents" ||
    (parts.length !== 4 && parts.length !== 5)
  )
    return undefined;
  const agentId = decodeURIComponent(parts[3]);
  if (!expertState.has(agentId)) return undefined;
  const action = parts[4];
  if (request.method === "GET" && action === "status")
    return Response.json({
      state: expertState.get(agentId) || "stopped",
      last_error: null,
      memory_maintenance: null,
    });
  if (request.method === "GET" && action === "subagents")
    return Response.json([]);
  if (request.method === "POST" && action === "start") {
    expertState.set(agentId, "running");
    return Response.json({ accepted: true });
  }
  if (request.method === "POST" && action === "stop") {
    expertState.set(agentId, "stopped");
    return Response.json({ accepted: true });
  }
  if (request.method === "POST" && action === "reload")
    return Response.json({ accepted: true });
  if (request.method === "DELETE" && action === undefined) {
    expertState.delete(agentId);
    return Response.json({ deleted: true });
  }
  return undefined;
});
const teamExamples: DemoRow[] = [
  {
    id: "team-product",
    name: "产品洞察小组",
    description: "负责用户反馈归纳和竞品洞察",
    enabled: true,
    values: {
      members: "行业研究专家、文案编辑专家",
      welcome: "先明确问题，再协同给出结论。",
      defaultModel: "示例模型 A",
    },
  },
  {
    id: "team-ops",
    name: "运营协作组",
    description: "整理运营活动复盘与执行计划",
    enabled: true,
    values: {
      members: "数据分析专家、文案编辑专家",
      welcome: "请提供目标、周期和已有材料。",
      defaultModel: "示例模型 B",
    },
  },
];
const taskExamples: DemoRow[] = [
  {
    id: "task-weekly",
    name: "每周合同到期检查",
    description: "每周一 09:00 检查近期到期合同",
    enabled: true,
    values: {
      schedule: "每周一 09:00",
      target: "合同审阅助手",
      lastRun: "10月26日 09:00 · 示例记录",
      nextRun: "11月2日 09:00",
    },
  },
  {
    id: "task-digest",
    name: "月度经营简报草稿",
    description: "每月最后一天整理经营数据",
    enabled: false,
    values: {
      schedule: "每月最后一天 17:00",
      target: "月度经营简报",
      lastRun: "10月1日 · 示例记录",
      nextRun: "已暂停",
    },
  },
];
const FEATURE_ROUTE_TABS = [
  "workflow",
  "data",
  "schema",
  "records",
  "attachments",
  "history",
  "notifications",
];

export const WORKSPACE_PAGES: readonly DemoPageDefinition[] = [
  {
    path: "/features",
    titleKey: "pageShell.features.title",
    tabs: [
      "workflow",
      "data",
      "schema",
      "records",
      "attachments",
      "history",
      "notifications",
    ],
    tabItems: Object.fromEntries(
      FEATURE_ROUTE_TABS.map((tab) => [
        tab,
        tab === "workflow" ? "feature-contract" : "feature-inspection",
      ]),
    ),
  },
  {
    path: "/experts",
    titleKey: "pageShell.experts.title",
    tabs: ["my", "library", "market"],
  },
  {
    path: "/teams",
    titleKey: "interfacePreview.full.workspace.teamsTitle",
    tabs: ["teams", "overview", "members", "conversation"],
    tabItems: {
      overview: "team-product",
      members: "team-product",
      conversation: "team-product",
    },
  },
  {
    path: "/tasks",
    titleKey: "interfacePreview.full.workspace.tasksTitle",
    tabs: ["jobs", "history"],
  },
];
export function WorkspacePages(props: DemoPageProps) {
  const { t } = useTranslation();
  const [expertAgents, setExpertAgents] = useState(expertAgentFixtures);
  const [expertEditorOpen, setExpertEditorOpen] = useState(false);
  const [editingExpert, setEditingExpert] = useState<OctopAgent | null>(null);
  const [expertForm] = Form.useForm<{
    name: string;
    description: string;
    model: string;
    system_prompt: string;
    temperature: number;
  }>();
  const [features] = useDemoState<DemoRow[]>(
    "workspace.features",
    featureExamples,
  );
  const [tasks] = useDemoState<DemoRow[]>("workspace.tasks", taskExamples);
  const [runs, setRuns] = useDemoState<DemoRow[]>("workspace.taskRuns", [
    {
      id: "run-1",
      name: "每周合同到期检查",
      description: "10月26日 09:00 · 示例记录",
      values: {
        status: "演示记录",
        detail: "仅记录界面演示状态；没有实际执行任务。",
      },
    },
    {
      id: "run-2",
      name: "月度经营简报草稿",
      description: "10月1日 17:00 · 示例记录",
      values: {
        status: "演示记录",
        detail: "本地示例，不代表外部任务执行成功。",
      },
    },
  ]);
  const requestedFeatureTab =
    props.path === "/features" && FEATURE_ROUTE_TABS.includes(props.tab || "")
      ? props.tab
      : "workflow";
  const [featureTab, setFeatureTab] = useState(
    requestedFeatureTab === "workflow" ? "workflow" : "data",
  );
  const [expertTab, setExpertTab] = useState(
    props.path === "/experts" &&
      ["my", "library", "market"].includes(props.tab || "")
      ? props.tab!
      : "my",
  );
  const [taskTab, setTaskTab] = useState(
    props.path === "/tasks" && ["jobs", "history"].includes(props.tab || "")
      ? props.tab!
      : "jobs",
  );
  const [selectedFeature, setSelectedFeature] = useState<DemoRow | undefined>(
    () => features.find((feature) => feature.id === props.item),
  );
  const [featureWorkflow, setFeatureWorkflow] = useDemoState<
    Record<string, FeatureWorkflow>
  >("workspace.featureWorkflows", {
    "feature-contract": {
      version: 1,
      status: "draft",
      inputs: {
        type: "object",
        required: ["contract_text"],
        properties: {
          contract_text: {
            type: "string",
            title: { zh: "合同文本", en: "Contract text" },
            format: "textarea",
          },
          focus: {
            type: "string",
            title: { zh: "审查侧重点", en: "Review focus" },
          },
        },
      },
      steps: [
        {
          id: "extract_terms",
          name: "提取条款",
          prompt: "提取双方、金额与日期",
        },
        {
          id: "review_risk",
          name: "审查风险",
          prompt: "检查付款、责任与解除条款",
          depends_on: ["extract_terms"],
          gate: "confirm",
        },
      ],
      outputs: [
        {
          name: "风险摘要",
          form: "markdown",
          description: {
            zh: "按风险等级列出依据与建议",
            en: "List evidence and recommendations by risk",
          },
        },
      ],
      rules: ["不得将示例分析当作法律意见。", "关键结论需引用原文依据。"],
    },
    "feature-inspection": {
      version: 1,
      status: "draft",
      inputs: {
        type: "object",
        required: ["equipment_id"],
        properties: {
          equipment_id: {
            type: "string",
            title: { zh: "设备编号", en: "Equipment ID" },
          },
          inspection_notes: {
            type: "string",
            title: { zh: "巡检记录", en: "Inspection notes" },
            format: "textarea",
          },
        },
      },
      steps: [
        {
          id: "summarize_inspection",
          name: "整理巡检结果",
          prompt: "识别异常并关联设备档案，缺失信息标为未知。",
        },
      ],
      outputs: [{ name: "异常列表", form: "markdown" }],
      rules: ["无数据时明确标注未知。"],
    },
  });
  const [selectedRun, setSelectedRun] = useState<DemoRow | null>(
    () =>
      runs.find((run) => run.id === props.item) ||
      tasks.find((task) => task.id === props.item) ||
      null,
  );
  const [runConfirmOpen, setRunConfirmOpen] = useState(false);
  const [runCandidate, setRunCandidate] = useState<DemoRow | null>(null);
  useEffect(() => {
    if (props.path === "/features") {
      const tab = FEATURE_ROUTE_TABS.includes(props.tab || "")
        ? props.tab
        : "workflow";
      setFeatureTab(tab === "workflow" ? "workflow" : "data");
      setSelectedFeature(
        props.item
          ? features.find((feature) => feature.id === props.item)
          : undefined,
      );
    }
    if (props.path === "/experts")
      setExpertTab(
        ["my", "library", "market"].includes(props.tab || "")
          ? props.tab!
          : "my",
      );
    if (props.path === "/tasks") {
      setTaskTab(
        ["jobs", "history"].includes(props.tab || "") ? props.tab! : "jobs",
      );
      setSelectedRun(
        runs.find((run) => run.id === props.item) ||
          tasks.find((task) => task.id === props.item) ||
          null,
      );
    }
  }, [props.path, props.tab, props.item, features, runs, tasks]);

  const openDocument = (documentId: string) => {
    const document = props.data.documents.find(
      (item) => item.id === documentId,
    );
    if (document) props.actions.openDocument(document);
  };
  const openRecord = (record: PreviewRecord) => {
    props.actions.openRecord(record);
  };
  const showFeature = (row: DemoRow) => {
    setSelectedFeature(row);
    setFeatureTab("workflow");
    props.navigate("/features", "workflow", row.id);
  };
  const workflow = selectedFeature
    ? featureWorkflow[selectedFeature.id] || {
        version: 1,
        status: "draft",
        steps: [],
      }
    : undefined;
  const openExpertEditor = (agent?: OctopAgent) => {
    setEditingExpert(agent || null);
    expertForm.setFieldsValue({
      name: agent?.name || "",
      description: agent?.description || "",
      model: agent?.default_model || "示例模型 A",
      system_prompt: agent?.system_prompt || "",
      temperature: Number(agent?.config.temperature ?? 0.3),
    });
    setExpertEditorOpen(true);
  };
  const saveExpert = (values: {
    name: string;
    description: string;
    model: string;
    system_prompt: string;
    temperature: number;
  }) => {
    if (editingExpert) {
      setExpertAgents((current) =>
        current.map((agent) =>
          agent.agent_id === editingExpert.agent_id
            ? {
                ...agent,
                name: values.name,
                description: values.description || null,
                default_model: values.model,
                system_prompt: values.system_prompt,
                config: { ...agent.config, temperature: values.temperature },
              }
            : agent,
        ),
      );
    } else {
      const id = `expert-local-${Date.now()}`;
      const agent: OctopAgent = {
        id: Date.now(),
        agent_id: id,
        kind: "agent",
        user_id: 1,
        is_owner: true,
        can_manage: true,
        name: values.name,
        description: values.description || null,
        persona_mbti: null,
        default_model: values.model,
        system_prompt: values.system_prompt,
        template_name: null,
        state: "stopped",
        last_error: null,
        icon: null,
        icon_name: null,
        icon_url: null,
        color: "#5b8ff9",
        config: { temperature: values.temperature },
      };
      expertState.set(id, "stopped");
      setExpertAgents((current) => [...current, agent]);
    }
    setExpertEditorOpen(false);
  };

  if (props.path === "/features")
    return (
      <PageShell
        title={t("pageShell.features.title")}
        subtitle={t("interfacePreview.full.workspace.featuresSubtitle")}
      >
        {selectedFeature ? (
          <>
            <Button
              type="link"
              icon={<ArrowLeft size={15} />}
              onClick={() => {
                setSelectedFeature(undefined);
                props.navigate("/features", "workflow");
              }}
            >
              {t("interfacePreview.full.workspace.backToList")}
            </Button>
            <div className={styles.detailHeader}>
              <div>
                <Typography.Title level={4}>
                  {selectedFeature.name}
                </Typography.Title>
                <Text type="secondary">{selectedFeature.description}</Text>
              </div>
              <DemoExternalAction
                label={t("interfacePreview.full.workspace.runFeature")}
                detail={t("interfacePreview.full.workspace.runFeatureBoundary")}
              />
            </div>
            <Tabs
              activeKey={featureTab}
              onChange={(key) => {
                setFeatureTab(key);
                props.navigate("/features", key, selectedFeature.id);
              }}
              items={[
                {
                  key: "workflow",
                  label: t("interfacePreview.full.workspace.workflow"),
                  children: workflow && (
                    <WorkflowDemo
                      key={selectedFeature.id}
                      featureId={selectedFeature.id}
                      t={t}
                      workflow={workflow}
                      onChange={(next) =>
                        setFeatureWorkflow((current) => ({
                          ...current,
                          [selectedFeature.id]: next,
                        }))
                      }
                    />
                  ),
                },
                {
                  key: "data",
                  label: t("interfacePreview.full.workspace.businessData"),
                  children: (
                    <FeatureDataDemo
                      key={selectedFeature.id}
                      featureId={selectedFeature.id}
                      t={t}
                      openRecord={openRecord}
                      openDocument={openDocument}
                      previewRecords={props.data.records}
                      previewDocuments={props.data.documents}
                      tab={props.tab}
                      navigate={props.navigate}
                    />
                  ),
                },
              ]}
            />
            <FeatureConversationDemo
              featureId={selectedFeature.id}
              featureName={selectedFeature.name}
              t={t}
            />
          </>
        ) : (
          <DemoCollection
            storageKey="workspace.features"
            title={t("interfacePreview.full.workspace.featureList")}
            description={t("interfacePreview.full.workspace.featureListHint")}
            initialRows={features}
            fields={[
              {
                name: "name",
                label: t("interfacePreview.full.workspace.name"),
                required: true,
              },
              {
                name: "description",
                label: t("interfacePreview.full.workspace.description"),
                kind: "textarea",
              },
              {
                name: "category",
                label: t("interfacePreview.full.workspace.category"),
              },
              {
                name: "updated",
                label: t("interfacePreview.full.workspace.updated"),
              },
              {
                name: "enabled",
                label: t("interfacePreview.full.workspace.enabled"),
                kind: "switch",
              },
            ]}
            canToggle
            rowActions={[
              {
                key: "detail",
                label: t("interfacePreview.full.workspace.configure"),
                onClick: showFeature,
              },
              {
                key: "data",
                label: t("interfacePreview.full.workspace.businessData"),
                onClick: (row) => {
                  setSelectedFeature(row);
                  setFeatureTab("data");
                  props.navigate("/features", "data", row.id);
                },
              },
            ]}
          />
        )}
        <DemoBoundary />
      </PageShell>
    );
  if (props.path === "/experts")
    return (
      <PageShell.FillTabs
        title={t("pageShell.experts.title")}
        subtitle={t("interfacePreview.full.workspace.expertsSubtitle")}
      >
        <Tabs
          activeKey={expertTab}
          onChange={(key) => {
            setExpertTab(key);
            props.navigate("/experts", key);
          }}
          items={[
            {
              key: "my",
              label: t("interfacePreview.full.workspace.myExperts"),
              children: (
                <Space direction="vertical" style={{ width: "100%" }}>
                  <Button
                    type="primary"
                    icon={<Plus size={15} />}
                    onClick={() => openExpertEditor()}
                  >
                    {t("interfacePreview.businessFlow.createExpert")}
                  </Button>
                  <div className={styles.teamGrid}>
                    {expertAgents.map((agent) => (
                      <AgentCard
                        key={agent.agent_id}
                        agent={agent}
                        onEdit={(agentId) =>
                          openExpertEditor(
                            expertAgents.find(
                              (item) => item.agent_id === agentId,
                            ),
                          )
                        }
                        onDeleted={(agentId) =>
                          setExpertAgents((current) =>
                            current.filter((item) => item.agent_id !== agentId),
                          )
                        }
                        onStateChange={(agentId, state) => {
                          expertState.set(
                            agentId,
                            state === "starting"
                              ? "running"
                              : state === "stopping"
                              ? "stopped"
                              : state,
                          );
                          setExpertAgents((current) =>
                            current.map((item) =>
                              item.agent_id === agentId
                                ? {
                                    ...item,
                                    state,
                                    last_error:
                                      state === "failed"
                                        ? item.last_error
                                        : null,
                                  }
                                : item,
                            ),
                          );
                        }}
                      />
                    ))}
                  </div>
                  <DemoBoundary>
                    {t("interfacePreview.businessFlow.expertCardLocalNotice")}
                  </DemoBoundary>
                  <Modal
                    title={
                      editingExpert
                        ? t("interfacePreview.businessFlow.editExpert")
                        : t("interfacePreview.businessFlow.createExpert")
                    }
                    open={expertEditorOpen}
                    onCancel={() => setExpertEditorOpen(false)}
                    onOk={() => expertForm.submit()}
                    destroyOnHidden
                  >
                    <Form
                      form={expertForm}
                      layout="vertical"
                      onFinish={saveExpert}
                    >
                      <Form.Item
                        name="name"
                        label={t("interfacePreview.full.workspace.name")}
                        rules={[{ required: true, whitespace: true }]}
                      >
                        <Input />
                      </Form.Item>
                      <Form.Item
                        name="description"
                        label={t("interfacePreview.full.workspace.description")}
                      >
                        <Input.TextArea />
                      </Form.Item>
                      <Form.Item
                        name="model"
                        label={t("interfacePreview.full.workspace.model")}
                      >
                        <Select
                          options={["示例模型 A", "示例模型 B", "自动选择"].map(
                            (value) => ({ value, label: value }),
                          )}
                        />
                      </Form.Item>
                      <Form.Item
                        name="system_prompt"
                        label={t(
                          "interfacePreview.full.workspace.instructions",
                        )}
                      >
                        <Input.TextArea rows={4} />
                      </Form.Item>
                      <Form.Item
                        name="temperature"
                        label={t("interfacePreview.full.workspace.temperature")}
                      >
                        <InputNumber
                          min={0}
                          max={2}
                          step={0.1}
                          style={{ width: "100%" }}
                        />
                      </Form.Item>
                    </Form>
                    <Alert
                      type="info"
                      showIcon
                      message={t(
                        "interfacePreview.businessFlow.expertConfigLocalOnly",
                      )}
                    />
                  </Modal>
                </Space>
              ),
            },
            {
              key: "library",
              label: t("interfacePreview.full.workspace.expertLibrary"),
              children: (
                <DemoCollection
                  storageKey="workspace.expertLibrary"
                  title={t("interfacePreview.full.workspace.expertLibrary")}
                  initialRows={[
                    {
                      id: "library-writing",
                      name: "写作与校对模板",
                      description: "针对公告、说明和邮件的文本协助模板",
                      values: { category: "内容" },
                    },
                    {
                      id: "library-analysis",
                      name: "数据分析模板",
                      description: "指标解释、趋势归纳与报告结构",
                      values: { category: "分析" },
                    },
                  ]}
                  fields={[
                    {
                      name: "name",
                      label: t("interfacePreview.full.workspace.name"),
                      required: true,
                    },
                    {
                      name: "description",
                      label: t("interfacePreview.full.workspace.description"),
                      kind: "textarea",
                    },
                    {
                      name: "category",
                      label: t("interfacePreview.full.workspace.category"),
                    },
                  ]}
                  canCreate={false}
                  canEdit={false}
                  canDelete={false}
                  rowActions={[
                    {
                      key: "preview",
                      label: t(
                        "interfacePreview.full.workspace.previewTemplate",
                      ),
                      onClick: (row) =>
                        Modal.info({
                          title: row.name,
                          content: row.description,
                        }),
                    },
                  ]}
                />
              ),
            },
            {
              key: "market",
              label: t("interfacePreview.full.workspace.expertMarket"),
              children: (
                <>
                  <DemoCollection
                    storageKey="workspace.expertMarket"
                    title={t("interfacePreview.full.workspace.expertMarket")}
                    initialRows={[
                      {
                        id: "market-research",
                        name: "市场调研顾问",
                        description: "面向公开信息研究的可配置模板",
                        values: { category: "研究", publisher: "示例目录" },
                      },
                      {
                        id: "market-qa",
                        name: "质量检查员",
                        description: "检查文档结构与规范一致性",
                        values: { category: "质量", publisher: "示例目录" },
                      },
                    ]}
                    fields={[
                      {
                        name: "name",
                        label: t("interfacePreview.full.workspace.name"),
                      },
                      {
                        name: "description",
                        label: t("interfacePreview.full.workspace.description"),
                        kind: "textarea",
                      },
                      {
                        name: "category",
                        label: t("interfacePreview.full.workspace.category"),
                      },
                      {
                        name: "publisher",
                        label: t("interfacePreview.full.workspace.publisher"),
                      },
                    ]}
                    canCreate={false}
                    canEdit={false}
                    canDelete={false}
                    rowActions={[
                      {
                        key: "install",
                        label: t(
                          "interfacePreview.full.workspace.installTemplate",
                        ),
                        onClick: () =>
                          Modal.info({
                            title: t(
                              "interfacePreview.full.workspace.installBoundaryTitle",
                            ),
                            content: t(
                              "interfacePreview.full.workspace.installBoundary",
                            ),
                          }),
                      },
                    ]}
                  />
                  <DemoBoundary />
                </>
              ),
            },
          ]}
        />
        <DemoBoundary />
      </PageShell.FillTabs>
    );
  if (props.path === "/teams")
    return (
      <TeamWorkspace
        t={t}
        tab={props.tab}
        item={props.item}
        navigate={props.navigate}
      />
    );
  if (props.path === "/tasks")
    return (
      <PageShell
        title={t("interfacePreview.full.workspace.tasksTitle")}
        subtitle={t("interfacePreview.full.workspace.tasksSubtitle")}
      >
        <Tabs
          activeKey={taskTab}
          onChange={(key) => {
            setTaskTab(key);
            props.navigate("/tasks", key);
          }}
          items={[
            {
              key: "jobs",
              label: t("interfacePreview.full.workspace.scheduledJobs"),
              children: (
                <DemoCollection
                  storageKey="workspace.tasks"
                  title={t("interfacePreview.full.workspace.taskList")}
                  description={t(
                    "interfacePreview.full.workspace.taskListHint",
                  )}
                  initialRows={tasks}
                  fields={[
                    {
                      name: "name",
                      label: t("interfacePreview.full.workspace.name"),
                      required: true,
                    },
                    {
                      name: "description",
                      label: t("interfacePreview.full.workspace.description"),
                      kind: "textarea",
                    },
                    {
                      name: "schedule",
                      label: t("interfacePreview.full.workspace.schedule"),
                      required: true,
                    },
                    {
                      name: "target",
                      label: t("interfacePreview.full.workspace.targetFeature"),
                    },
                    {
                      name: "lastRun",
                      label: t("interfacePreview.full.workspace.lastRun"),
                    },
                    {
                      name: "nextRun",
                      label: t("interfacePreview.full.workspace.nextRun"),
                    },
                    {
                      name: "enabled",
                      label: t("interfacePreview.full.workspace.enabled"),
                      kind: "switch",
                    },
                  ]}
                  canToggle
                  extra={
                    <DemoExternalAction
                      label={t("interfacePreview.full.workspace.executeTask")}
                      detail={t(
                        "interfacePreview.full.workspace.executeTaskBoundary",
                      )}
                    />
                  }
                  rowActions={[
                    {
                      key: "history",
                      label: t("interfacePreview.full.workspace.runHistory"),
                      onClick: (row) => {
                        setSelectedRun(row);
                        setTaskTab("history");
                        props.navigate("/tasks", "history", row.id);
                      },
                    },
                    {
                      key: "run",
                      label: t("interfacePreview.full.workspace.executeTask"),
                      onClick: (row) => {
                        setRunCandidate(row);
                        setRunConfirmOpen(true);
                      },
                    },
                  ]}
                />
              ),
            },
            {
              key: "history",
              label: t("interfacePreview.full.workspace.runHistory"),
              children: (
                <>
                  <Card title={t("interfacePreview.full.workspace.runHistory")}>
                    <Table
                      rowKey="id"
                      dataSource={runs}
                      pagination={false}
                      onRow={(row) => ({
                        onClick: () => {
                          setSelectedRun(row);
                          props.navigate("/tasks", "history", row.id);
                        },
                      })}
                      columns={[
                        {
                          title: t("interfacePreview.full.workspace.task"),
                          dataIndex: "name",
                        },
                        {
                          title: t("interfacePreview.full.workspace.time"),
                          dataIndex: "description",
                        },
                        {
                          title: t("interfacePreview.full.workspace.status"),
                          key: "status",
                          render: (_, row) => (
                            <Tag color="blue">{row.values?.status}</Tag>
                          ),
                        },
                      ]}
                    />
                  </Card>
                  {selectedRun && (
                    <Card
                      className={styles.runDetail}
                      title={selectedRun.name}
                      extra={
                        <Button
                          type="link"
                          onClick={() => {
                            setSelectedRun(null);
                            props.navigate("/tasks", "history");
                          }}
                        >
                          {t("interfacePreview.full.workspace.closeDetail")}
                        </Button>
                      }
                    >
                      <Paragraph>
                        {selectedRun.values?.detail ||
                          t("interfacePreview.full.workspace.taskRunLocalNote")}
                      </Paragraph>
                    </Card>
                  )}
                </>
              ),
            },
          ]}
        />
        <Modal
          title={t("interfacePreview.full.workspace.executeTask")}
          open={runConfirmOpen}
          onCancel={() => setRunConfirmOpen(false)}
          onOk={() => {
            if (runCandidate)
              setRuns((current) => [
                {
                  id: `run-${Date.now()}`,
                  name: runCandidate.name,
                  description: `${t(
                    "interfacePreview.full.workspace.justNow",
                  )} · ${t("interfacePreview.full.workspace.sampleRun")}`,
                  values: {
                    status: t("interfacePreview.full.workspace.sampleRun"),
                    detail: t(
                      "interfacePreview.full.workspace.taskRunLocalNote",
                    ),
                  },
                },
                ...current,
              ]);
            setRunConfirmOpen(false);
            Modal.info({
              title: t("interfacePreview.full.workspace.sampleRun"),
              content: t("interfacePreview.full.workspace.taskRunLocalNote"),
            });
          }}
          okText={t("interfacePreview.full.workspace.addSampleRun")}
        >
          <Alert
            type="warning"
            showIcon
            message={t("interfacePreview.full.workspace.taskRunLocalNote")}
          />
        </Modal>
        <DemoBoundary />
      </PageShell>
    );
  return null;
}

interface TeamEditorValues {
  defaultModel: string;
  name: string;
  description: string;
  members: string[];
  welcome: string;
}

interface TeamConversationMessage {
  id: string;
  role: "assistant" | "user";
  text: string;
  sample?: boolean;
}

function TeamWorkspace({
  t,
  tab,
  item,
  navigate,
}: {
  t: (key: string) => string;
  tab?: string;
  item?: string;
  navigate: (path: string, tab?: string, item?: string) => void;
}) {
  const [teams, setTeams] = useDemoState<DemoRow[]>(
    "workspace.teams",
    teamExamples,
  );
  const [conversations, setConversations] = useDemoState<
    Record<string, TeamConversationMessage[]>
  >("workspace.teamConversations", {
    "team-product": [
      {
        id: "tp-1",
        role: "user",
        text: "整理这周用户反馈里的主要问题。",
        sample: true,
      },
      {
        id: "tp-2",
        role: "assistant",
        text: "示例讨论：可以先按易用性、稳定性和需求建议归类，再标注出现频次。",
        sample: true,
      },
    ],
    "team-ops": [
      {
        id: "to-1",
        role: "user",
        text: "请一起整理本月活动复盘框架。",
        sample: true,
      },
      {
        id: "to-2",
        role: "assistant",
        text: "示例讨论：建议覆盖目标、触达、转化、成本和后续行动。",
        sample: true,
      },
    ],
  });
  const initialTeam = teams.find((team) => team.id === item);
  const [selectedId, setSelectedId] = useState<string | null>(
    () => initialTeam?.id || null,
  );
  const [detailTab, setDetailTab] = useState(
    ["overview", "members", "conversation"].includes(tab || "")
      ? tab!
      : "overview",
  );
  const [editorOpen, setEditorOpen] = useState(false);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [memberDraft, setMemberDraft] = useState<string[]>(() =>
    String(initialTeam?.values?.members || "")
      .split("、")
      .filter(Boolean),
  );
  const [composer, setComposer] = useState("");
  const [form] = Form.useForm<TeamEditorValues>();
  const selectedTeam = teams.find((team) => team.id === selectedId) || null;
  const memberOptions = expertExamples.map((expert) => ({
    label: expert.name,
    value: expert.name,
  }));
  useEffect(() => {
    const team = teams.find((entry) => entry.id === item);
    setSelectedId(team?.id || null);
    setDetailTab(
      ["overview", "members", "conversation"].includes(tab || "")
        ? tab!
        : "overview",
    );
    if (team)
      setMemberDraft(
        String(team.values?.members || "")
          .split("、")
          .filter(Boolean),
      );
  }, [teams, item, tab]);
  const startCreate = () => {
    setEditingId(null);
    form.resetFields();
    form.setFieldsValue({
      name: "",
      description: "",
      members: [],
      welcome: "",
      defaultModel: "示例模型 A",
    });
    setEditorOpen(true);
  };
  const startEdit = (team: DemoRow) => {
    setEditingId(team.id);
    form.setFieldsValue({
      name: team.name,
      description: team.description || "",
      members: String(team.values?.members || "")
        .split("、")
        .filter(Boolean),
      welcome: String(team.values?.welcome || ""),
      defaultModel: String(team.values?.defaultModel || "示例模型 A"),
    });
    setEditorOpen(true);
  };
  const saveTeam = (values: TeamEditorValues) => {
    const row: DemoRow = {
      id: editingId || `team-${crypto.randomUUID()}`,
      name: values.name.trim(),
      description: values.description.trim(),
      enabled: editingId
        ? teams.find((team) => team.id === editingId)?.enabled ?? true
        : true,
      values: {
        members: values.members.join("、"),
        welcome: values.welcome.trim(),
        defaultModel: values.defaultModel,
      },
    };
    setTeams((current) =>
      editingId
        ? current.map((team) => (team.id === editingId ? row : team))
        : [...current, row],
    );
    setEditorOpen(false);
  };
  const selectTeam = (team: DemoRow, nextTab = "overview") => {
    setSelectedId(team.id);
    setDetailTab(nextTab);
    setMemberDraft(
      String(team.values?.members || "")
        .split("、")
        .filter(Boolean),
    );
    navigate("/teams", nextTab, team.id);
  };
  const saveMembers = () => {
    if (memberDraft.length < 2) {
      Modal.warning({
        title: t("interfacePreview.businessFlow.teamMinimumMembers"),
      });
      return;
    }
    if (!selectedTeam) return;
    setTeams((current) =>
      current.map((team) =>
        team.id === selectedTeam.id
          ? {
              ...team,
              values: { ...team.values, members: memberDraft.join("、") },
            }
          : team,
      ),
    );
  };
  const sendDraft = () => {
    const text = composer.trim();
    if (!selectedTeam || !text) return;
    setConversations((current) => ({
      ...current,
      [selectedTeam.id]: [
        ...(current[selectedTeam.id] || []),
        { id: `local-${Date.now()}`, role: "user", text },
      ],
    }));
    setComposer("");
  };
  const conversation = selectedTeam ? conversations[selectedTeam.id] || [] : [];
  return (
    <PageShell
      title={t("interfacePreview.full.workspace.teamsTitle")}
      subtitle={t("interfacePreview.full.workspace.teamsSubtitle")}
    >
      {selectedTeam ? (
        <>
          <Button
            type="link"
            icon={<ArrowLeft size={15} />}
            onClick={() => {
              setSelectedId(null);
              navigate("/teams", "teams");
            }}
          >
            {t("interfacePreview.full.workspace.teamList")}
          </Button>
          <div className={styles.detailHeader}>
            <div>
              <Typography.Title level={4}>{selectedTeam.name}</Typography.Title>
              <Text type="secondary">{selectedTeam.description}</Text>
            </div>
            <Space>
              <Button onClick={() => startEdit(selectedTeam)}>
                {t("interfacePreview.full.workspace.editTeam")}
              </Button>
              <Tag color={selectedTeam.enabled ? "green" : "default"}>
                {selectedTeam.enabled
                  ? t("interfacePreview.full.workspace.enabled")
                  : t("interfacePreview.full.workspace.disabled")}
              </Tag>
            </Space>
          </div>
          <Tabs
            activeKey={detailTab}
            onChange={(key) => {
              setDetailTab(key);
              navigate("/teams", key, selectedTeam.id);
              if (key === "members")
                setMemberDraft(
                  String(selectedTeam.values?.members || "")
                    .split("、")
                    .filter(Boolean),
                );
            }}
            items={[
              {
                key: "overview",
                label: t("interfacePreview.full.workspace.teamOverview"),
                children: (
                  <Card
                    title={t("interfacePreview.full.workspace.teamOverview")}
                  >
                    <Paragraph>{selectedTeam.description}</Paragraph>
                    <Paragraph>
                      <Text strong>
                        {t("interfacePreview.full.workspace.members")}:{" "}
                      </Text>
                      {String(selectedTeam.values?.members || "")}
                    </Paragraph>
                    <Paragraph>
                      <Text strong>
                        {t("interfacePreview.full.workspace.model")}:{" "}
                      </Text>
                      {String(
                        selectedTeam.values?.defaultModel || "示例模型 A",
                      )}
                    </Paragraph>
                    <Paragraph>
                      <Text strong>
                        {t("interfacePreview.full.workspace.welcomeMessage")}:{" "}
                      </Text>
                      {String(selectedTeam.values?.welcome || "—")}
                    </Paragraph>
                  </Card>
                ),
              },
              {
                key: "members",
                label: t("interfacePreview.full.workspace.memberConfig"),
                children: (
                  <Card
                    title={t("interfacePreview.full.workspace.memberConfig")}
                  >
                    <Form layout="vertical">
                      <Form.Item
                        label={t("interfacePreview.full.workspace.members")}
                      >
                        <Select
                          mode="multiple"
                          value={memberDraft}
                          options={memberOptions}
                          onChange={setMemberDraft}
                          placeholder={t(
                            "interfacePreview.full.workspace.selectMembers",
                          )}
                        />
                      </Form.Item>
                      <Button type="primary" onClick={saveMembers}>
                        {t("interfacePreview.full.workspace.saveMembers")}
                      </Button>
                    </Form>
                    <DemoBoundary>
                      {t("interfacePreview.full.workspace.memberConfigLocal")}
                    </DemoBoundary>
                  </Card>
                ),
              },
              {
                key: "conversation",
                label: t("interfacePreview.full.workspace.teamConversation"),
                children: (
                  <div className={styles.teamConversation}>
                    <Alert
                      type="info"
                      showIcon
                      message={t(
                        "interfacePreview.full.workspace.teamConversationLocalNotice",
                      )}
                      description={t(
                        "interfacePreview.full.workspace.teamConversationBoundary",
                      )}
                    />
                    <div className={styles.teamMessages}>
                      {conversation.map((message) => (
                        <div
                          key={message.id}
                          className={`${styles.teamMessage} ${
                            message.role === "user"
                              ? styles.teamUserMessage
                              : ""
                          }`}
                        >
                          <Avatar
                            size="small"
                            icon={
                              message.role === "assistant" ? (
                                <Users size={14} />
                              ) : undefined
                            }
                          >
                            {message.role === "user"
                              ? t("interfacePreview.full.workspace.senderSelf")
                              : null}
                          </Avatar>
                          <Card size="small">
                            <Paragraph style={{ marginBottom: 0 }}>
                              {message.text}
                            </Paragraph>
                            {message.sample && (
                              <Tag className={styles.sampleMessageTag}>
                                {t(
                                  "interfacePreview.full.workspace.sampleConversation",
                                )}
                              </Tag>
                            )}
                          </Card>
                        </div>
                      ))}
                    </div>
                    <div className={styles.composer}>
                      <Input.TextArea
                        value={composer}
                        onChange={(event) => setComposer(event.target.value)}
                        placeholder={t(
                          "interfacePreview.full.workspace.teamMessagePlaceholder",
                        )}
                        autoSize={{ minRows: 2, maxRows: 4 }}
                      />
                      <div className={styles.composerActions}>
                        <Text type="secondary">
                          {t(
                            "interfacePreview.full.workspace.conversationDraftOnly",
                          )}
                        </Text>
                        <Button
                          type="primary"
                          icon={<Send size={15} />}
                          onClick={sendDraft}
                        >
                          {t("interfacePreview.full.workspace.addLocalDraft")}
                        </Button>
                      </div>
                    </div>
                  </div>
                ),
              },
            ]}
          />
        </>
      ) : (
        <>
          <div className={styles.teamListToolbar}>
            <Text type="secondary">
              {t("interfacePreview.full.workspace.teamListHint")}
            </Text>
            <Button
              type="primary"
              icon={<Plus size={15} />}
              onClick={startCreate}
            >
              {t("interfacePreview.full.workspace.createTeam")}
            </Button>
          </div>
          <div className={styles.teamGrid}>
            {teams.map((team) => (
              <Card
                key={team.id}
                title={team.name}
                extra={
                  <Tag color={team.enabled ? "green" : "default"}>
                    {team.enabled
                      ? t("interfacePreview.full.workspace.enabled")
                      : t("interfacePreview.full.workspace.disabled")}
                  </Tag>
                }
              >
                <Paragraph type="secondary">{team.description}</Paragraph>
                <Paragraph>
                  <Text strong>
                    {t("interfacePreview.full.workspace.members")}:{" "}
                  </Text>
                  {String(team.values?.members || "")}
                </Paragraph>
                <Space wrap>
                  <Button onClick={() => selectTeam(team)}>
                    {t("interfacePreview.full.workspace.teamOverview")}
                  </Button>
                  <Button onClick={() => selectTeam(team, "members")}>
                    {t("interfacePreview.full.workspace.memberConfig")}
                  </Button>
                  <Button
                    type="primary"
                    onClick={() => selectTeam(team, "conversation")}
                  >
                    {t("interfacePreview.full.workspace.openTeamConversation")}
                  </Button>
                  <Button onClick={() => startEdit(team)}>
                    {t("common.edit")}
                  </Button>
                  <Popconfirm
                    title={t(
                      "interfacePreview.full.workspace.deleteTeamConfirm",
                    )}
                    onConfirm={() =>
                      setTeams((current) =>
                        current.filter((item) => item.id !== team.id),
                      )
                    }
                  >
                    <Button danger>{t("common.delete")}</Button>
                  </Popconfirm>
                  <Button
                    type="link"
                    onClick={() =>
                      setTeams((current) =>
                        current.map((item) =>
                          item.id === team.id
                            ? { ...item, enabled: !item.enabled }
                            : item,
                        ),
                      )
                    }
                  >
                    {team.enabled
                      ? t("interfacePreview.full.workspace.disable")
                      : t("interfacePreview.full.workspace.enable")}
                  </Button>
                </Space>
              </Card>
            ))}
          </div>
          {teams.length === 0 && (
            <Card>
              <Text>{t("interfacePreview.full.workspace.noTeams")}</Text>
            </Card>
          )}
        </>
      )}
      <Modal
        title={
          editingId
            ? t("interfacePreview.full.workspace.editTeam")
            : t("interfacePreview.full.workspace.createTeam")
        }
        open={editorOpen}
        onCancel={() => setEditorOpen(false)}
        onOk={() => form.submit()}
        okText={t("common.save")}
        cancelText={t("common.cancel")}
        destroyOnHidden
      >
        <Form form={form} layout="vertical" onFinish={saveTeam}>
          <Form.Item
            name="name"
            label={t("interfacePreview.full.workspace.name")}
            rules={[{ required: true, whitespace: true }]}
          >
            <Input maxLength={120} />
          </Form.Item>
          <Form.Item
            name="description"
            label={t("interfacePreview.full.workspace.description")}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
          <Form.Item
            name="defaultModel"
            label={t("interfacePreview.full.workspace.model")}
            rules={[{ required: true }]}
          >
            <Select
              options={["示例模型 A", "示例模型 B", "自动选择"].map(
                (value) => ({ value, label: value }),
              )}
            />
          </Form.Item>
          <Form.Item
            name="members"
            label={t("interfacePreview.full.workspace.members")}
            rules={[
              {
                required: true,
                message: t("interfacePreview.full.workspace.selectMembers"),
              },
              {
                validator: (_, value: string[] = []) =>
                  value.length >= 2
                    ? Promise.resolve()
                    : Promise.reject(
                        new Error(
                          t("interfacePreview.businessFlow.teamMinimumMembers"),
                        ),
                      ),
              },
            ]}
          >
            <Select
              mode="multiple"
              options={memberOptions}
              placeholder={t("interfacePreview.full.workspace.selectMembers")}
            />
          </Form.Item>
          <Form.Item
            name="welcome"
            label={t("interfacePreview.full.workspace.welcomeMessage")}
          >
            <Input.TextArea rows={2} />
          </Form.Item>
        </Form>
      </Modal>
      <DemoBoundary>
        {t("interfacePreview.full.workspace.teamAssistHint")}
      </DemoBoundary>
    </PageShell>
  );
}
