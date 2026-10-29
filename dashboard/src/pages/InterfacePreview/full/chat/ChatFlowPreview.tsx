import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type ComponentProps,
  type MouseEvent,
  type ReactNode,
} from "react";
import { Button, Drawer, Segmented, Tag } from "antd";
import { useTranslation } from "react-i18next";
import type { DemoPageProps } from "../contract";
import type {
  ChatMessage,
  ChatAttachment,
} from "../../../Chat/hooks/sseHelpers";
import type { Session } from "../../../Chat/hooks/useSessions";
import type { OctopAgent } from "../../../../context/AgentContext";
import ChatSidebarPanel from "../../../Chat/components/ChatSidebarPanel";
import ChatTitleBar from "../../../Chat/components/ChatTitleBar";
import MessageList from "../../../Chat/components/MessageList";
import ChatInput from "../../../Chat/components/ChatInput";
import ChatComposerChrome from "../../../Chat/components/ChatComposerChrome";
import { useChatDetailStyles } from "./useChatDetailStyles";
import styles from "./ChatFlowPreview.module.less";
import FileChangesPreview from "./FileChangesPreview";
import { ensureBuiltinToolRenderers } from "../../../../plugins/toolRenderers/ensureBuiltins";
import { registerToolRenderer } from "../../../../plugins/toolRenderers/registry";
import type { ToolRenderProps } from "../../../../plugins/toolRenderers/types";
import { registerPreviewApi } from "../previewTransport";
import type { SlashCommandListResponse } from "../../../../api/modules/slash";
import { ChatFilePreviewProvider } from "../../../Chat/ChatFilePreviewContext";
import PlanReadyCard from "../../../Chat/components/PlanReadyCard";
import WorkflowInputCard from "../../../Chat/components/WorkflowInputCard";
import WorkflowOutputCard from "../../../Chat/components/WorkflowOutputCard";
import AskQuestionCard from "../../../Chat/components/AskQuestionCard";
import WelcomeScreen from "../../../Chat/components/WelcomeScreen";
import AgentNotReadyScreen from "../../../Chat/components/AgentNotReadyScreen";
import type { AskQuestion } from "../../../../api/types/hitl";
import { CHAT_DEMO_VIDEO_URL } from "./demoVideo";
import type {
  WorkflowInputs,
  WorkflowOutput,
} from "../../../../api/modules/featureWorkflow";
import type { ThreadRun } from "../../../Chat/hooks/useWorkflowRun";
import type {
  EnqueueChatItemInput,
  QueuedChatItem,
} from "../../../Chat/hooks/useChatMessageQueue";
import { useIsMobile } from "../../../../hooks/useIsMobile";
import { registerBundledToolRenderers } from "./bundledRendererCatalog";
import { bundledToolFixtures } from "./bundledRendererFixtures";
import FilePanelContent from "../../../Chat/components/FilePanelContent";
import KnowledgeCitationPanelContent from "../../../Chat/components/KnowledgeCitationPanelContent";
import type { KnowledgeCitation } from "../../../../utils/parseKnowledgeCitations";
import type { KnowledgeDocument } from "../../../../api/modules/knowledgeBases";
import {
  ChatToolDockProvider,
  dockTabIdForToolCall,
  type OpenToolUiPanelOptions,
} from "../../../Chat/ChatToolDockContext";
import { ToolDetailsInline } from "../../../Chat/components/MessageBubble";
import ChatDockToolUiContent from "../../../Chat/components/ChatDockToolUiContent";

registerPreviewApi("chat-flow:slash-commands", (request) => {
  const url = new URL(request.url);
  if (request.method !== "GET" || url.pathname !== "/api/slash/commands")
    return undefined;
  const origin = url.searchParams.get("origin") ?? "ui";
  const response: SlashCommandListResponse = {
    origin,
    commands: [
      {
        name: "new",
        command: "new",
        aliases: ["new-chat"],
        label_en: "New chat",
        label_zh: "新建对话",
        description_en: "Start a local preview conversation.",
        description_zh: "开始本地预览会话。",
        usage: "/new",
        icon: "message-square",
        tone: "neutral",
        category: "conversation",
        origins: ["ui"],
        client_action: "new_chat",
      },
    ],
  };
  return Response.json(response);
});
let workflowChangeStatus: "applied" | "reverted" = "applied";
const uploadedFixtureFiles = new Map<string, Blob>();
const editedFixtureFiles = new Map<string, string>();

registerPreviewApi("chat-flow:artifact-download", (request) => {
  const url = new URL(request.url);
  if (
    request.method !== "GET" ||
    url.pathname !== "/api/agents/demo-agent/workspace/download"
  )
    return undefined;
  const path = url.searchParams.get("path");
  const stored = path ? uploadedFixtureFiles.get(path) : undefined;
  if (path && editedFixtureFiles.has(path)) {
    return new Response(editedFixtureFiles.get(path), {
      headers: {
        "Content-Type": stored?.type || "text/markdown; charset=utf-8",
      },
    });
  }
  if (stored)
    return new Response(stored, {
      headers: { "Content-Type": stored.type || "application/octet-stream" },
    });
  if (!path || !Object.prototype.hasOwnProperty.call(localFiles, path))
    return undefined;
  const body = editedFixtureFiles.get(path) ?? localFiles[path];
  return new Response(body, {
    headers: {
      "Content-Type": "text/markdown; charset=utf-8",
      "Content-Disposition": `attachment; filename="${path.split("/").pop()}"`,
    },
  });
});
registerPreviewApi("chat-flow:local-upload", async (request) => {
  const url = new URL(request.url);
  const targetAgentId = url.pathname.match(
    /^\/api\/agents\/([^/]+)\/upload$/,
  )?.[1];
  if (
    request.method !== "POST" ||
    (targetAgentId !== "demo-agent" &&
      !fixtureAgents.some((agent) => agent.agent_id === targetAgentId))
  )
    return undefined;
  const form = await request.formData();
  const file = form.get("file");
  if (!(file instanceof File))
    return Response.json(
      { message: "A fixture file is required." },
      { status: 400 },
    );
  const path = `inbound/${encodeURIComponent(file.name)}`;
  uploadedFixtureFiles.set(path, file);
  const access = URL.createObjectURL(file);
  return Response.json({
    path,
    workspace_path: path,
    filename: file.name,
    media_type: file.type || "application/octet-stream",
    url: access,
    access_url: access,
  });
});

registerPreviewApi("chat-flow:workflow-change", (request) => {
  const url = new URL(request.url);
  const match = url.pathname.match(
    /^\/api\/agents\/demo-agent\/workflow\/changes\/([a-z0-9-]+)\/revert$/,
  );
  if (request.method !== "POST" || !match || match[1] !== "change-demo")
    return undefined;
  workflowChangeStatus = "reverted";
  return Response.json({
    change_id: match[1],
    status: workflowChangeStatus,
    receipt: "local-preview-only",
  });
});

ensureBuiltinToolRenderers();
registerToolRenderer({
  id: "chat_fixture_card",
  pluginId: "preview",
  tools: ["demo_ui_card"],
  component: FixtureToolRenderer,
});

function FixtureToolRenderer({ data, output }: ToolRenderProps) {
  const { t } = useTranslation();
  const record =
    data && typeof data === "object" ? (data as Record<string, unknown>) : {};
  if (record.kind === "file_changes") return <FileChangesPreview />;
  if (record.previewError === true)
    throw new Error("Intentional local renderer boundary fixture.");
  return (
    <div className={styles.plugin}>
      <div className={styles.pluginHeader}>
        <strong>
          {String(record.title ?? t("interfacePreview.chatFlow.demo"))}
        </strong>
        {record.count != null && <span>×{String(record.count)}</span>}
        {record.status != null && (
          <span className={styles.pluginStatus}>{String(record.status)}</span>
        )}
      </div>
      {record.note != null && <p>{String(record.note)}</p>}
      <details className={styles.pluginDetails}>
        <summary>{t("common.viewDetail")}</summary>
        <pre>{output ?? JSON.stringify(data, null, 2)}</pre>
      </details>
      <small>{t("interfacePreview.chatFlow.fixturePluginNote")}</small>
    </div>
  );
}
const img =
  "data:image/svg+xml,%3Csvg xmlns='http://www.w3.org/2000/svg' width='96' height='64'%3E%3Crect width='96' height='64' fill='%23dbeafe'/%3E%3Ccircle cx='48' cy='28' r='13' fill='%2360a5fa'/%3E%3Cpath d='M12 60 36 38l15 13 11-9 22 18' fill='%2334d399'/%3E%3C/svg%3E";
const makeFixtureAudio = () => {
  const sampleRate = 8000;
  const sampleCount = 1600;
  const buffer = new ArrayBuffer(44 + sampleCount * 2);
  const view = new DataView(buffer);
  const writeText = (offset: number, value: string) =>
    Array.from(value).forEach((character, index) =>
      view.setUint8(offset + index, character.charCodeAt(0)),
    );
  writeText(0, "RIFF");
  view.setUint32(4, 36 + sampleCount * 2, true);
  writeText(8, "WAVE");
  writeText(12, "fmt ");
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeText(36, "data");
  view.setUint32(40, sampleCount * 2, true);
  for (let index = 0; index < sampleCount; index += 1)
    view.setInt16(
      44 + index * 2,
      Math.sin((index / sampleRate) * Math.PI * 2 * 440) * 5000,
      true,
    );
  const bytes = new Uint8Array(buffer);
  return `data:audio/wav;base64,${btoa(
    Array.from(bytes, (byte) => String.fromCharCode(byte)).join(""),
  )}`;
};
const audioSample = makeFixtureAudio();
const now = Date.now();
const policyCitation: KnowledgeCitation = {
  kbId: "kb-demo",
  kbName: "员工政策库",
  docId: "doc-expense",
  filename: "remote-work-policy.md",
  path: "policies/remote-work-policy.md",
};
const rendererCitation: KnowledgeCitation = {
  kbId: "kb-demo",
  kbName: "组件手册",
  docId: "doc-components",
  filename: "renderer-guide.md",
  path: "guides/renderer-guide.md",
};
const messagesByScenario: Record<string, ChatMessage[]> = {
  ask: [
    {
      id: "ask-user",
      role: "user",
      content: "请帮我确认清理归档资料的范围。",
      timestamp: now - 6000,
    },
    {
      id: "ask-intro",
      role: "assistant",
      content:
        "请先确认目标环境与清理条件。下面的问题只记录本地回答，不会执行清理。",
      status: "done",
      timestamp: now - 5000,
    },
  ],
  knowledge: [
    {
      id: "k-u",
      role: "user",
      content: "在知识库中查一下远程办公报销政策，并总结差旅和设备的限制。",
      composerContext: {
        knowledgeBaseIds: ["kb-demo"],
        skills: ["policy-search"],
        model: "local-fixture",
      },
      timestamp: now - 60000,
    },
    {
      id: "k-think",
      role: "assistant",
      content: "我先定位最新政策，再核对差旅和设备两个章节。",
      type: "reasoning",
      status: "done",
      timestamp: now - 57000,
    },
    {
      id: "k-search",
      role: "assistant",
      content: "",
      toolData: {
        name: "search_knowledge",
        displayName: "知识库搜索",
        callId: "k-1",
        arguments: JSON.stringify({ query: "远程办公 报销 差旅 设备" }),
        output:
          JSON.stringify({
            results: [
              { title: "远程办公与费用政策", score: 0.94 },
              { title: "设备采购规范", score: 0.82 },
            ],
          }) +
          `\n\n<!--octop-kb-citations:${JSON.stringify([policyCitation])}-->`,
      },
      timestamp: now - 54000,
    },
    {
      id: "k-answer",
      role: "assistant",
      content:
        "## 远程办公报销\n\n差旅需事前审批，设备报销限于标准目录内的必要设备。\n\n| 类别 | 规则 |\n| --- | --- |\n| 差旅 | 主管预先批准 |\n| 设备 | 按标准目录申报 |",
      status: "done",
      usage: { input_tokens: 1830, output_tokens: 312, total_tokens: 2142 },
      timestamp: now - 50000,
    },
    {
      id: "k-system",
      role: "system",
      content: "此为本地知识库场景；真实知识库读取未发生。",
      timestamp: now - 49000,
    },
  ],
  document: [
    {
      id: "d-u",
      role: "user",
      content: "请生成一个新员工入职指南，先列计划，再整理成文件。",
      timestamp: now - 80000,
    },
    {
      id: "d-plan",
      role: "assistant",
      content:
        "计划：\n1. 梳理入职前准备\n2. 汇总首周安排\n3. 输出 Markdown 指南",
      toolData: {
        name: "write_todos",
        callId: "d-todo",
        arguments: JSON.stringify({
          todos: [
            { id: "1", content: "梳理入职前准备", status: "completed" },
            { id: "2", content: "汇总首周安排", status: "in_progress" },
            { id: "3", content: "输出 Markdown 指南", status: "pending" },
            { id: "4", content: "废弃旧版本草案", status: "cancelled" },
          ],
        }),
        output: "已更新任务列表",
      },
      timestamp: now - 76000,
    },
    {
      id: "d-file",
      role: "assistant",
      content: "",
      toolData: {
        name: "write_file",
        callId: "d-file-call",
        arguments: JSON.stringify({ path: "onboarding/guide.md" }),
        output: "已写入 onboarding/guide.md",
      },
      timestamp: now - 72000,
    },
    {
      id: "d-change",
      role: "assistant",
      content: "",
      toolData: {
        name: "feature_workflow_change",
        callId: "change-demo",
        arguments: JSON.stringify({ summary: "增加审核门槛" }),
        output: JSON.stringify({
          change_id: "change-demo",
          target: "definition",
          summary: "增加审核门槛",
          items: [{ path: "/steps/0/gate", before: "auto", after: "confirm" }],
          status: "applied",
        }),
      },
      timestamp: now - 70000,
    },
    {
      id: "d-conflict",
      role: "assistant",
      content: "",
      toolData: {
        name: "feature_workflow_change",
        callId: "change-refused",
        arguments: JSON.stringify({ summary: "被拒绝的变更" }),
        output: JSON.stringify({ error: "conflict" }),
        errorCode: "WORKFLOW_CONFLICT",
      },
      timestamp: now - 69000,
    },
    {
      id: "d-answer",
      role: "assistant",
      content:
        "已完成入职指南草稿。\n\n```markdown\n# 新员工入职指南\n\n欢迎加入团队。\n\n- 完成账号配置\n- 阅读安全规范\n- 与导师确认首周目标\n```",
      status: "done",
      timestamp: now - 68000,
    },
  ],
  approval: [
    {
      id: "a-u",
      role: "user",
      content: "准备清理旧备份。",
      timestamp: now - 40000,
    },
    {
      id: "a-tool",
      role: "assistant",
      content: "",
      toolData: {
        name: "delete_old_backup",
        callId: "approval-tool",
        arguments: JSON.stringify({ path: "backups/2024-01" }),
      },
      timestamp: now - 36000,
    },
    {
      id: "a-hitl",
      role: "assistant",
      content: "",
      hitlData: {
        pending_id: "demo-approval",
        action_requests: [
          {
            name: "delete_old_backup",
            args: { path: "backups/2024-01" },
            description: "删除归档备份目录",
          },
        ],
        review_configs: [
          {
            action_name: "delete_old_backup",
            allowed_decisions: ["approve", "reject"],
          },
        ],
      },
      timestamp: now - 35000,
    },
    {
      id: "a-question",
      role: "assistant",
      content: "",
      hitlData: {
        pending_id: "demo-question",
        action_requests: [
          {
            name: "ask_user_question",
            args: {
              questions: [
                {
                  header: "目标环境",
                  question: "要清理哪个环境？",
                  options: [
                    { label: "测试环境", description: "非生产数据" },
                    { label: "预发布", description: "上线前环境" },
                  ],
                  multi_select: true,
                },
                {
                  header: "保留期",
                  question: "保留周期是多少天？",
                  options: [{ label: "30 天" }, { label: "90 天" }],
                },
              ],
            },
          },
        ],
      },
      timestamp: now - 34000,
    },
    {
      id: "a-approved",
      role: "assistant",
      content: "",
      hitlData: {
        status: "approved",
        action_requests: [
          {
            name: "ask_user_question",
            args: {
              questions: [
                {
                  question: "Which environment?",
                  options: [{ label: "Staging" }],
                },
              ],
            },
          },
        ],
      },
      timestamp: now - 33000,
    },
    {
      id: "a-rejected",
      role: "assistant",
      content: "",
      hitlData: {
        status: "rejected",
        action_requests: [
          {
            name: "delete_old_backup",
            args: { path: "backups/old" },
            description: "已拒绝的本地审批历史",
          },
        ],
      },
      timestamp: now - 32000,
    },
  ],
  error: [
    {
      id: "e-u",
      role: "user",
      content: "运行目录检查并告诉我失败原因。",
      timestamp: now - 30000,
    },
    {
      id: "e-tool",
      role: "assistant",
      content: "",
      toolData: {
        name: "terminal",
        callId: "e-call",
        arguments: JSON.stringify({ command: "ls -la ./reports" }),
        errorCode: "PROCESS_EXIT",
        returnCode: 2,
        output: "目录不存在",
        pluginId: "builtin",
      },
      timestamp: now - 28000,
    },
    {
      id: "e-error",
      role: "assistant",
      content: "本地脚本播放到这里展示错误与重试控件。",
      status: "error",
      errorInfo: {
        message: "本地演示错误：路径未找到",
        code: "ENOENT",
        source: "demo",
        status_code: 404,
        retryable: true,
      },
      timestamp: now - 26000,
    },
  ],
  browser: [
    {
      id: "b-u",
      role: "user",
      content: "在浏览器查看产品发布说明，并展示截图。用户附件均为本地样例。",
      attachments: [
        {
          kind: "image",
          url: img,
          filename: "user-reference.svg",
          mediaType: "image/svg+xml",
        },
        {
          kind: "audio",
          url: audioSample,
          filename: "user-note.wav",
          mediaType: "audio/wav",
        },
        {
          kind: "video",
          url: CHAT_DEMO_VIDEO_URL,
          filename: "user-process.webm",
          mediaType: "video/webm",
        },
      ],
      timestamp: now - 20000,
    },
    {
      id: "b-browser",
      role: "assistant",
      content: "",
      toolData: {
        name: "browser_use",
        callId: "b-call",
        arguments: JSON.stringify({ url: "local fixture", action: "capture" }),
        output: JSON.stringify({
          title: "发布说明",
          text: "版本 2.4 · 本地夹具页面",
        }),
      },
      timestamp: now - 18000,
    },
    {
      id: "b-result",
      role: "assistant",
      content: "页面已在脚本夹具中捕获；不会打开真实浏览器。",
      attachments: [
        {
          kind: "image",
          url: img,
          filename: "release-preview.svg",
          mediaType: "image/svg+xml",
        },
        {
          kind: "video",
          url: CHAT_DEMO_VIDEO_URL,
          filename: "demo-process.webm",
          mediaType: "video/webm",
        },
        {
          kind: "audio",
          url: audioSample,
          filename: "note.wav",
          mediaType: "audio/wav",
        },
        {
          kind: "file",
          url: "data:text/plain,fixture%20report",
          filename: "release-notes.txt",
          mediaType: "text/plain",
        },
      ],
      status: "done",
      timestamp: now - 16000,
    },
  ],
  tools: [
    {
      id: "t-u",
      role: "user",
      content: "演示工具调用结果、结构化插件卡片和异常输入。",
      timestamp: now - 12000,
    },
    {
      id: "t-custom",
      role: "assistant",
      content: "",
      toolData: {
        name: "demo_ui_card",
        pluginId: "preview",
        callId: "t-card",
        arguments: JSON.stringify({ query: "quarterly" }),
        output: JSON.stringify({
          octop_ui: { renderer: "chat_fixture_card", version: 1 },
          data: {
            title: "本季度报告",
            count: 3,
            status: "已验证",
            note: "组件由本地注册，宿主 API 不会调用",
          },
          text: "只使用实际的 octop_ui 协议",
        }),
      },
      timestamp: now - 11000,
    },
    {
      id: "t-unknown",
      role: "assistant",
      content: "",
      toolData: {
        name: "unknown_ui",
        callId: "t-unknown",
        arguments: "malformed {",
        output: JSON.stringify({
          octop_ui: { renderer: "not-installed", version: 4 },
          data: { title: "未知 renderer fallback", count: 1 },
        }),
      },
      timestamp: now - 10000,
    },
    {
      id: "t-acp",
      role: "assistant",
      content: "",
      toolData: {
        name: "acp_runner",
        callId: "t-acp",
        arguments: JSON.stringify({ runner: "fixture" }),
        output:
          "[permission_required] Read repository\n- Allow once (id=allow_once)\n- Deny (id=deny)",
      },
      timestamp: now - 9000,
    },
    {
      id: "t-done",
      role: "assistant",
      content: "普通工具已完成；附有格式化输入输出。",
      toolData: {
        name: "read_file",
        callId: "t-done",
        arguments: JSON.stringify({ path: "README.md" }),
        output: "# Local fixture\nNo files are read from the workspace.",
      },
      timestamp: now - 8000,
    },
    {
      id: "t-running",
      role: "assistant",
      content: "",
      toolData: {
        name: "search",
        callId: "t-running",
        arguments: JSON.stringify({ query: "等待本地步骤完成" }),
      },
      status: "streaming",
      timestamp: now - 7000,
    },
    {
      id: "t-silent",
      role: "assistant",
      content: "",
      toolData: {
        name: "demo_silent_ui",
        callId: "t-silent",
        arguments: "{}",
        output: JSON.stringify({
          octop_ui: { renderer: "chat_fixture_card", version: 1 },
          data: { silent: true },
        }),
      },
      timestamp: now - 6500,
    },
    {
      id: "t-boundary",
      role: "assistant",
      content: "",
      toolData: {
        name: "demo_ui_card",
        pluginId: "preview",
        callId: "t-boundary",
        arguments: "{}",
        output: JSON.stringify({
          octop_ui: { renderer: "chat_fixture_card", version: 1 },
          data: { title: "Renderer error boundary", previewError: true },
          text: "Intentional local error-boundary fixture.",
        }),
      },
      timestamp: now - 6000,
    },
    {
      id: "t-mermaid-stream",
      role: "assistant",
      content: "```mermaid\ngraph TD\n  search --> cite\n```",
      status: "streaming",
      timestamp: now - 5500,
    },
  ],
  rich: [
    {
      id: "r-u",
      role: "user",
      content: "请整理插件输出，包含公式、代码、图表和来源。",
      attachments: [
        {
          kind: "image",
          url: img,
          filename: "reference.svg",
          mediaType: "image/svg+xml",
        },
      ],
      timestamp: now - 7000,
    },
    {
      id: "r-tool",
      role: "assistant",
      content: "",
      toolData: {
        name: "image_gen_tool",
        callId: "r-image",
        arguments: JSON.stringify({ prompt: "simple blue diagram" }),
        output: JSON.stringify({
          images: [{ type: "image", url: img, filename: "diagram.svg" }],
          files: [
            {
              type: "file",
              url: "data:text/plain,generated%20artifact",
              filename: "diagram-source.txt",
            },
            {
              type: "video",
              url: CHAT_DEMO_VIDEO_URL,
              filename: "demo-process.webm",
              mediaType: "video/webm",
            },
          ],
        }),
      },
      timestamp: now - 6000,
    },
    {
      id: "r-citation",
      role: "assistant",
      content: "",
      toolData: {
        name: "search_knowledge",
        callId: "r-search",
        arguments: JSON.stringify({ query: "工具结果组件协议" }),
        output: `工具组件协议说明。\n\n<!--octop-kb-citations:${JSON.stringify([
          rendererCitation,
        ])}-->`,
      },
      timestamp: now - 5500,
    },
    {
      id: "r-answer",
      role: "assistant",
      content:
        "## Rich response\n\nInline math $E=mc^2$ and display math:\n\n$$\\int_0^1 x^2 dx = \\frac{1}{3}$$\n\n```typescript\nconst fixture = { safe: true };\nconsole.log(fixture);\n```\n\n| Renderer | State |\n| --- | --- |\n| Markdown | complete |\n| Plugin | local |\n\n```mermaid\ngraph TD\n  search --> cite\n```",
      status: "done",
      usage: {
        input_tokens: 2480,
        cache_read_tokens: 300,
        output_tokens: 750,
        total_tokens: 3230,
      },
      timestamp: now - 5000,
    },
  ],
  bundled: [
    {
      id: "bundle-user",
      role: "user",
      content:
        "请汇总内置组件演示。以下卡片来自仓库内固定的一方插件 UI；工具本身没有运行，数据是脚本夹具。",
      timestamp: now - 120000,
    },
    ...bundledToolFixtures.map(
      (fixture, index): ChatMessage => ({
        id: `bundled-${fixture.pluginId}-${fixture.toolName}-${index}`,
        role: "assistant",
        content: "",
        toolData: {
          name: fixture.toolName,
          displayName: fixture.title,
          pluginId: fixture.pluginId,
          callId: `bundled-${fixture.pluginId}-${index}`,
          arguments: "{}",
          output: JSON.stringify({
            octop_ui: { renderer: fixture.rendererId, version: 1 },
            data: fixture.data,
          }),
        },
        status: "done",
        timestamp: now - (bundledToolFixtures.length - index) * 1000,
      }),
    ),
  ],
  files: [
    {
      id: "files-user",
      role: "user",
      content:
        "整理入职资料：新增指南、更新检查清单，移除旧版说明。请展示文件变更。",
      timestamp: now - 30000,
    },
    {
      id: "files-review",
      role: "assistant",
      content: "",
      toolData: {
        name: "demo_ui_card",
        displayName: "文件变更",
        callId: "files-review-demo",
        output: JSON.stringify({
          octop_ui: { renderer: "chat_fixture_card", version: 1 },
          data: { kind: "file_changes" },
        }),
      },
      timestamp: now - 29000,
    },
    {
      id: "files-answer",
      role: "assistant",
      content:
        "变更已按文件归组。展开文件行可查看新增、删除和上下文差异；以上均为只读展示数据，不会修改工作区。",
      status: "done",
      timestamp: now - 28000,
    },
  ],
};

const scenarios = [
  { id: "knowledge", title: "知识库问答", sub: "搜索 → 引用 → 答案" },
  { id: "document", title: "生成文档", sub: "计划 → todo → 文件" },
  { id: "approval", title: "操作审批", sub: "查看操作、编辑决定、批准或拒绝" },
  { id: "ask", title: "继续追问", sub: "单/多选、自由输入、回答或关闭" },
  { id: "error", title: "终端错误 / 重试", sub: "失败状态与恢复入口" },
  { id: "browser", title: "浏览器与媒体", sub: "图像、视频、音频、文件" },
  { id: "tools", title: "工具协议 / ACP", sub: "自定义、未知、损坏、权限" },
  { id: "rich", title: "丰富内容 / 插件", sub: "Markdown、代码、数学、表格" },
  {
    id: "bundled",
    title: "内置插件组件",
    sub: "26 个仓库一方 UI · 仅静态 fixture，无工具执行",
  },
  { id: "files", title: "文件变更", sub: "编辑汇总 → 路径列表 → 逐文件差异" },
] as const;
const workflowInputs: WorkflowInputs = {
  type: "object",
  required: ["name", "start_date", "role"],
  properties: {
    name: { type: "string", title: { zh: "员工姓名", en: "Employee name" } },
    email: {
      type: "string",
      format: "email",
      title: { zh: "工作邮箱", en: "Work email" },
    },
    start_date: {
      type: "string",
      format: "date",
      title: { zh: "入职日期", en: "Start date" },
    },
    notes: {
      type: "string",
      format: "textarea",
      title: { zh: "备注", en: "Notes" },
    },
    role: {
      type: "string",
      enum: ["Engineer", "Designer", "Operations"],
      title: { zh: "岗位", en: "Role" },
    },
    equipment_budget: {
      type: "number",
      title: { zh: "设备预算", en: "Equipment budget" },
    },
    buddy_days: {
      type: "integer",
      title: { zh: "导师支持天数", en: "Buddy support days" },
    },
    remote: { type: "boolean", title: { zh: "远程办公", en: "Remote work" } },
    topics: {
      type: "array",
      items: { type: "string" },
      title: { zh: "关注主题", en: "Topics" },
    },
    reference_file: {
      type: "file",
      accept: ".txt,.md,.pdf",
      title: { zh: "本地参考文件", en: "Local reference file" },
    },
  },
};
const workflowOutputs: WorkflowOutput[] = [
  {
    name: "Onboarding guide",
    form: "markdown",
    path: "onboarding/guide.md",
    description: { zh: "新员工指南", en: "New employee guide" },
  },
  {
    name: "Workflow summary",
    form: "file",
    path: "workflows/summary.md",
    description: { zh: "流程摘要", en: "Workflow summary" },
  },
];
const pendingQuestions: AskQuestion[] = [
  {
    header: "目标环境",
    question: "要清理哪个环境？",
    options: [
      { label: "测试环境", description: "非生产数据" },
      { label: "预发布", description: "上线前环境" },
    ],
    multi_select: true,
  },
  {
    header: "保留周期",
    question: "保留多少天？",
    options: [{ label: "30 天" }, { label: "90 天" }],
  },
];
const localFiles: Record<string, string> = {
  "onboarding/guide.md":
    "# 新员工入职指南\n\n## 第一天\n- 配置账号和开发环境\n- 与导师确认首周目标\n\n## 权限\n仅申请岗位所需的最小权限。",
  "workflows/summary.md":
    "# 入职流程摘要\n\n已收集姓名、岗位与开始日期。该文件来自当前本地工作流夹具。",
  "plans/onboarding.md":
    "# 计划：新员工入职\n\n1. 准备账号和设备\n2. 安排导师沟通\n3. 完成首周回顾",
  "policies/remote-work-policy.md":
    "# 远程办公政策\n\n工作日核心协作时段为 10:00–15:00。设备和数据访问必须遵循最小权限原则。\n\n## 报销\n差旅需主管事前批准；设备报销仅限标准目录内必要设备。",
  "references/security-baseline.md":
    "# 安全基线\n\n所有访问凭据必须使用公司密码管理器；公共网络下请启用 VPN。",
  "guides/renderer-guide.md":
    "# Renderer guide\n\nBuilt-in tool result UIs receive a versioned `octop_ui` envelope and render the payload locally. The preview does not invoke tools.",
};
registerPreviewApi("chat-flow:text-files", async (request) => {
  const url = new URL(request.url);
  if (url.pathname !== "/api/agents/demo-agent/workspace/file")
    return undefined;
  const path = url.searchParams.get("path")?.replace(/^\/+/, "") ?? "";
  if (
    !Object.prototype.hasOwnProperty.call(localFiles, path) &&
    !uploadedFixtureFiles.has(path)
  ) {
    return Response.json(
      { message: "Local fixture not found." },
      { status: 404 },
    );
  }
  if (request.method === "GET") {
    return Response.json({
      content:
        editedFixtureFiles.get(path) ??
        localFiles[path] ??
        (await uploadedFixtureFiles.get(path)!.text()),
    });
  }
  if (request.method === "PUT") {
    const { content } = (await request.json()) as { content: string };
    editedFixtureFiles.set(path, content);
    return Response.json({ path });
  }
  return undefined;
});
const citationFixtures = [policyCitation, rendererCitation];
registerPreviewApi("chat-flow:knowledge-documents", (request) => {
  const url = new URL(request.url);
  if (request.method !== "GET") return undefined;
  if (url.pathname === "/api/knowledge-bases/kb-demo/documents") {
    const documents: KnowledgeDocument[] = citationFixtures.map((citation) => ({
      id: citation.docId,
      kb_id: citation.kbId,
      filename: citation.filename,
      path: citation.path,
      is_dir: false,
      content_type: "text/markdown",
      byte_size: new TextEncoder().encode(localFiles[citation.path!])
        .byteLength,
      content_hash: "local-preview-fixture",
      status: "ready",
      error_message: "",
      chunk_count: 1,
      created_at: now / 1000,
      updated_at: now / 1000,
      has_original: true,
    }));
    return Response.json(documents);
  }
  const match = url.pathname.match(
    /^\/api\/knowledge-bases\/kb-demo\/documents\/([^/]+)\/(content|file)$/,
  );
  const citation =
    match && citationFixtures.find((item) => item.docId === match[1]);
  if (!citation) return undefined;
  const text =
    editedFixtureFiles.get(citation.path!) ?? localFiles[citation.path!];
  return match![2] === "content"
    ? Response.json({
        id: citation.docId,
        filename: citation.filename,
        content_type: "text/markdown",
        title: citation.filename,
        text,
      })
    : new Response(text, {
        headers: { "Content-Type": "text/markdown; charset=utf-8" },
      });
});

const fixtureAgents: OctopAgent[] = (
  ["running", "stopped", "failed"] as const
).map((state, index) => ({
  id: index + 1,
  agent_id: `preview-${state}`,
  name: `示例助手 · ${state}`,
  description: "Local chat preview fixture.",
  persona_mbti: null,
  default_model: "fixture-model",
  system_prompt: null,
  template_name: null,
  state,
  last_error: state === "failed" ? "静态失败状态夹具" : null,
  icon: null,
  icon_name: "bot",
  icon_url: null,
  color: "#4f7cac",
  config: {},
}));
const fixtureModels: NonNullable<
  ComponentProps<typeof ChatInput>["availableModels"]
> = [
  {
    provider_id: 0,
    provider_name: "Local preview",
    provider_kind: "fixture",
    model: "fixture-model",
    name: "Fixture model",
    context_window: 32000,
    max_tokens: 4096,
    reasoning: true,
  },
];
const fixtureKnowledgeBases: NonNullable<
  ComponentProps<typeof ChatInput>["availableKnowledgeBases"]
> = [
  {
    id: "kb-demo",
    owner_user_id: 1,
    name: "员工政策库 · fixture",
    description: "本地只读引用示例",
    default_open: true,
    shared: false,
    is_enterprise: false,
    icon_name: "book",
    embedding_model: "fixture",
    embedding_dim: 0,
    doc_count: 2,
    max_documents: 2,
    created_at: 0,
    updated_at: 0,
  },
];
const fixtureSkills: NonNullable<
  ComponentProps<typeof ChatInput>["availableSkills"]
> = [
  {
    slug: "policy-search",
    name: "政策检索",
    description: "本地技能选择示例",
    enabled: true,
    kind: "builtin",
  },
];
const fixtureConnectors: NonNullable<
  ComponentProps<typeof ChatInput>["availableConnectors"]
> = [
  {
    mcp_server_name: "preview-fixture",
    label: "Local fixture connector",
    kind: "fixture",
  },
];
export default function ChatFlowPreview(_props: DemoPageProps) {
  const { t, i18n } = useTranslation();
  const [refined, setRefined] = useState(true);
  const detailClass = useChatDetailStyles(refined);
  const [scenario, setScenario] = useState<string>("knowledge");
  const [messages, setMessages] = useState<ChatMessage[]>(
    messagesByScenario.knowledge,
  );
  const [streaming, setStreaming] = useState(false);
  const [historyLoading, setHistoryLoading] = useState(false);
  const [historyLoadingMore, setHistoryLoadingMore] = useState(false);
  const [historyRefreshing, setHistoryRefreshing] = useState(false);
  const [historyHasMore, setHistoryHasMore] = useState(false);
  const historyLoadPending = useRef(false);
  const [workflowRun, setWorkflowRun] = useState<ThreadRun | null>(null);
  const [entryState, setEntryState] = useState<
    "conversation" | "welcome" | "notReady"
  >("conversation");
  const [pendingAskStatus, setPendingAskStatus] = useState<
    "pending" | "approved" | "rejected"
  >("pending");
  const [queuedItems, setQueuedItems] = useState<QueuedChatItem[]>([]);
  const isMobile = useIsMobile();
  const [sidebarWidth, setSidebarWidth] = useState(280);
  const [isSidebarResizing, setIsSidebarResizing] = useState(false);
  const resizeOrigin = useRef<{ x: number; width: number } | null>(null);
  const [sessionsHasMore, setSessionsHasMore] = useState(true);
  const [sidebarOpen, setSidebarOpen] = useState(!isMobile);
  const [drawer, setDrawer] = useState("");
  const [notice, setNotice] = useState("");
  const [session, setSession] = useState<Session>({
    id: "demo-knowledge",
    threadId: "demo-knowledge",
    name: t(
      "interfacePreview.chatFlow.scenarioKnowledge",
      "Knowledge-base Q&A",
    ),
    updatedAt: new Date(now).toISOString(),
    channelType: "ui",
    pinned: false,
  });
  const [sessions, setSessions] = useState<Session[]>(() =>
    scenarios.map(
      (entry, index) =>
        ({
          id: `demo-${entry.id}`,
          threadId: `demo-${entry.id}`,
          name: t(
            `interfacePreview.chatFlow.scenario${entry.id[0].toUpperCase()}${entry.id.slice(
              1,
            )}`,
            entry.title,
          ),
          updatedAt: new Date(now - index * 60000).toISOString(),
          channelType: "ui",
          isActive: true,
        }) as Session,
    ),
  );
  const [threadSnapshots, setThreadSnapshots] = useState<
    Record<string, ChatMessage[]>
  >({});
  const [fileContents, setFileContents] =
    useState<Record<string, string>>(localFiles);
  const [drawerAssetUrl, setDrawerAssetUrl] = useState("");
  const [activeCitation, setActiveCitation] =
    useState<KnowledgeCitation | null>(null);
  const [viewerActions, setViewerActions] = useState<ReactNode>(null);
  const [toolPanel, setToolPanel] = useState<OpenToolUiPanelOptions | null>(
    null,
  );
  const openLocalToolPanel = useCallback((options: OpenToolUiPanelOptions) => {
    setToolPanel(options);
    setDrawer(`tool:${options.callId}`);
  }, []);
  const closeLocalToolPanel = useCallback((callId: string) => {
    setDrawer((current) => (current === `tool:${callId}` ? "" : current));
  }, []);
  const [selectedAgentId, setSelectedAgentId] = useState(
    fixtureAgents[0].agent_id,
  );
  const [selectedModel, setSelectedModel] = useState<string | null>(
    "fixture-model",
  );
  const [selectedKbIds, setSelectedKbIds] = useState<string[]>(["kb-demo"]);
  const [selectedConnectors, setSelectedConnectors] = useState<string[]>([]);
  useEffect(
    () => () => {
      if (drawerAssetUrl) URL.revokeObjectURL(drawerAssetUrl);
    },
    [drawerAssetUrl],
  );
  useEffect(() => {
    if (!isSidebarResizing) return;
    const onMove = (event: PointerEvent) => {
      const origin = resizeOrigin.current;
      if (origin)
        setSidebarWidth(
          Math.max(220, Math.min(480, origin.width + event.clientX - origin.x)),
        );
    };
    const onEnd = () => {
      resizeOrigin.current = null;
      setIsSidebarResizing(false);
    };
    window.addEventListener("pointermove", onMove);
    window.addEventListener("pointerup", onEnd, { once: true });
    return () => {
      window.removeEventListener("pointermove", onMove);
      window.removeEventListener("pointerup", onEnd);
    };
  }, [isSidebarResizing]);
  const labels: Record<string, string> = {
    "interfacePreview.chatFlow.demo": "脚本演示",
    "interfacePreview.chatFlow.notice": "本地演示操作",
    "interfacePreview.chatFlow.original": "原始",
    "interfacePreview.chatFlow.refined": "轻度优化",
    "interfacePreview.chatFlow.scenarios": "本地会话场景",
    "interfacePreview.chatFlow.history": "历史加载演示",
    "interfacePreview.chatFlow.queue": "待发送消息",
    "interfacePreview.chatFlow.pendingAsk": "待回答问题",
    "interfacePreview.chatFlow.loading": "显示历史加载中",
    "interfacePreview.chatFlow.loadMore": "加载更早消息",
    "interfacePreview.chatFlow.refresh": "刷新历史",
    "interfacePreview.chatFlow.openFiles": "打开本地文件夹夹具",
    "interfacePreview.chatFlow.openBrowser": "打开浏览器夹具",
    "interfacePreview.chatFlow.regenerate": "本地重播此回复",
    "interfacePreview.chatFlow.fork": "复制为本地分支",
    "interfacePreview.chatFlow.sendBoundary":
      "输入仅保存在本地，不会发送给模型。",
    "interfacePreview.chatFlow.scenarioKnowledge": "知识库问答",
    "interfacePreview.chatFlow.scenarioDocument": "生成文档",
    "interfacePreview.chatFlow.scenarioApproval": "审批与追问",
    "interfacePreview.chatFlow.scenarioError": "终端错误 / 重试",
    "interfacePreview.chatFlow.scenarioBrowser": "浏览器与媒体",
    "interfacePreview.chatFlow.scenarioTools": "工具协议 / ACP",
    "interfacePreview.chatFlow.scenarioRich": "丰富内容 / 插件",
    "interfacePreview.chatFlow.scenarioBundled": "内置插件组件",
    "interfacePreview.chatFlow.bundleScenarioDescription":
      "26 个仓库一方 UI · 静态夹具，无工具执行",
    "interfacePreview.chatFlow.mobileHistory": "历史",
    "interfacePreview.chatFlow.workflowCards": "计划、输入和工作流输出夹具",
    "interfacePreview.chatFlow.localFilesTitle": "本地夹具文件",
    "interfacePreview.chatFlow.workspaceFileTitle": "工作区文件 · 本地夹具",
    "interfacePreview.chatFlow.citationTitle": "知识引用 · 本地来源",
    "interfacePreview.chatFlow.editFileTitle": "编辑本地文件夹具",
    "interfacePreview.chatFlow.browserTitle": "浏览器页面快照夹具",
    "interfacePreview.chatFlow.citationSource": "引用来源夹具",
    "interfacePreview.chatFlow.artifactSource": "工作区产物夹具",
    "interfacePreview.chatFlow.editBoundary":
      "编辑仅保存在此页面内存中，不会上传。",
    "interfacePreview.chatFlow.missingFixture":
      "该路径没有本地内容夹具；未读取工作区文件。",
    "interfacePreview.chatFlow.blockedPlayback":
      "本地预览已禁用外部播放；剧集选择仍保存在本地。",
    "interfacePreview.chatFlow.blockedRequest":
      "请求已被预览宿主隔离；没有发出网络请求。",
  };
  Object.assign(labels, {
    "interfacePreview.chatFlow.welcome": "欢迎",
    "interfacePreview.chatFlow.notReady": "助手未就绪",
    "interfacePreview.chatFlow.loadingHistory": "加载历史",
    "interfacePreview.chatFlow.longHistory": "长历史 / 虚拟列表",
    "interfacePreview.chatFlow.replayScenario": "播放脚本",
    "interfacePreview.chatFlow.resetWorkflow": "重置输入夹具",
    "interfacePreview.chatFlow.againAsk": "再次提问",
    "interfacePreview.chatFlow.pendingDockNote":
      "本地待回答问题 dock（不是真实待办）",
    "interfacePreview.chatFlow.queueSteer": "接管",
    "interfacePreview.chatFlow.queueFollowUp": "后续发送",
    "interfacePreview.chatFlow.queueWithdraw": "撤回",
    "interfacePreview.chatFlow.queueAdd": "添加脚本消息",
    "interfacePreview.chatFlow.browserCaptureNote":
      "本地页面快照 · 未启动浏览器",
    "interfacePreview.chatFlow.notExecuted": "未执行",
    "interfacePreview.chatFlow.externalRequestBlocked":
      "请求被预览宿主阻止；没有发出网络请求。",
  });
  const tx = (key: keyof typeof labels) => t(key, labels[key]);
  const scenarioTitle = (id: string, fallback: string) =>
    t(
      `interfacePreview.chatFlow.scenario${id[0].toUpperCase()}${id.slice(1)}`,
      fallback,
    );
  const updateLocalSession = (id: string, changes: Partial<Session>) => {
    setSessions((items) =>
      items.map((item) => (item.id === id ? { ...item, ...changes } : item)),
    );
    if (session.id === id)
      setSession((current) => ({ ...current, ...changes }));
  };
  const deleteLocalSession = (id: string) => {
    setSessions((items) => items.filter((item) => item.id !== id));
    if (session.id === id) {
      setMessages([]);
      setStreaming(false);
      setEntryState("welcome");
      setNotice("本地会话已清空。");
    }
  };

  const chooseScenario = (id: string) => {
    const found = scenarios.find((entry) => entry.id === id);
    const title = scenarioTitle(id, found?.title ?? id);
    setScenario(id);
    setEntryState("conversation");
    setWorkflowRun(null);
    setPendingAskStatus("pending");
    setQueuedItems(
      id === "tools"
        ? [
            {
              id: "queue-followup",
              text: "继续总结差旅条款",
              createdAt: Date.now(),
              remoteStatus: "queued",
            },
          ]
        : [],
    );
    setHistoryLoading(false);
    setHistoryHasMore(false);
    setThreadSnapshots((current) => ({ ...current, [session.id]: messages }));
    const selected = sessions.find((item) => item.id === `demo-${id}`);
    setSession(
      selected
        ? { ...selected, updatedAt: new Date().toISOString() }
        : {
            id: `demo-${id}`,
            threadId: `demo-${id}`,
            name: title,
            updatedAt: new Date().toISOString(),
            channelType: "ui",
          },
    );
    setMessages(messagesByScenario[id] ?? []);
    setStreaming(id === "tools");
    setNotice(`${title} · ${tx("interfacePreview.chatFlow.demo")}`);
  };
  const selectLocalSession = (id: string) => {
    if (id.startsWith("demo-")) {
      chooseScenario(id.slice(5));
      return;
    }
    const selected = sessions.find((item) => item.id === id);
    if (!selected) return;
    setThreadSnapshots((current) => ({ ...current, [session.id]: messages }));
    setSession(selected);
    setMessages(threadSnapshots[id] ?? []);
    setEntryState("conversation");
    setScenario("");
  };
  const loadMoreLocalSessions = () => {
    const olderId = "local-history-older";
    const olderSession: Session = {
      id: olderId,
      threadId: olderId,
      name: "更早的本地示例历史",
      updatedAt: new Date(now - 86400000).toISOString(),
      channelType: "ui",
    };
    setSessions((items) =>
      items.some((item) => item.id === olderId)
        ? items
        : [...items, olderSession],
    );
    setThreadSnapshots((current) => ({
      ...current,
      [olderId]: [
        {
          id: `${olderId}-user`,
          role: "user",
          content: "显示更早的本地会话记录。",
          timestamp: now - 86400000,
        },
        {
          id: `${olderId}-assistant`,
          role: "assistant",
          content: "这条记录来自本地历史夹具。",
          status: "done",
          timestamp: now - 86400000 + 1000,
        },
      ],
    }));
    setSessionsHasMore(false);
    setNotice("Loaded the remaining local session fixture.");
  };
  const openFileFixture = async (
    path: string,
    kind: "file" | "citation" = "file",
  ) => {
    setDrawer(`${kind}:${path}`);
    setDrawerAssetUrl("");
    try {
      const response = await fetch(
        `/api/agents/demo-agent/workspace/download?path=${encodeURIComponent(
          path,
        )}`,
      );
      if (!response.ok)
        throw new Error(`Fixture route returned ${response.status}`);
      const blob = await response.blob();
      setDrawerAssetUrl(URL.createObjectURL(blob));
      if (blob.type.startsWith("text/") || blob.type.includes("json")) {
        const text = await blob.text();
        setFileContents((current) => ({ ...current, [path]: text }));
      }
    } catch {
      setNotice(
        `Local fixture route unavailable for ${path}; no remote fallback was attempted.`,
      );
    }
  };
  const openCitationPreview = (citation: KnowledgeCitation) => {
    setActiveCitation(citation);
    setDrawer(`citation:${citation.path ?? citation.filename}`);
    setNotice(`Opened local citation fixture: ${citation.filename}`);
  };
  const record = (action: string) => {
    setNotice(`${tx("interfacePreview.chatFlow.demo")}: ${action}`);
    setDrawer(action);
  };
  useEffect(() => {
    void registerBundledToolRenderers(
      {
        onPatchResult: (callId, nextData) => {
          setMessages((rows) =>
            rows.map((message) => {
              if (
                message.toolData?.callId !== callId ||
                !message.toolData.output
              )
                return message;
              try {
                const envelope = JSON.parse(message.toolData.output) as Record<
                  string,
                  unknown
                >;
                return {
                  ...message,
                  toolData: {
                    ...message.toolData,
                    output: JSON.stringify({ ...envelope, data: nextData }),
                  },
                };
              } catch {
                return message;
              }
            }),
          );
          setNotice(
            `Bundled UI updated local fixture ${callId}; no tool was run.`,
          );
        },
        onRequestBlocked: (path) => {
          setDrawer(`Bundled renderer request blocked: ${path}`);
          setNotice(`External bundled UI request blocked locally: ${path}`);
        },
        onOpenSidePanel: openLocalToolPanel,
        onCloseSidePanel: closeLocalToolPanel,
      },
      i18n.language,
    ).catch((error: unknown) => {
      setNotice(
        `Bundled UI setup failed: ${
          error instanceof Error ? error.message : String(error)
        }`,
      );
    });
  }, [i18n.language, openLocalToolPanel, closeLocalToolPanel]);
  const forkCurrent = () => {
    const branchId = `local-branch-${Date.now()}`;
    const branch = {
      ...session,
      id: branchId,
      threadId: branchId,
      name: `${session.name} (local branch)`,
      updatedAt: new Date().toISOString(),
    };
    const branchMessages = messages.map((message) => ({
      ...message,
      id: `${message.id}-branch`,
    }));
    setThreadSnapshots((current) => ({
      ...current,
      [session.id]: messages,
      [branchId]: branchMessages,
    }));
    setSession(branch);
    setScenario("");
    setSessions((items) => [...items, branch]);
    setMessages(branchMessages);
    setNotice("已复制为本地分支；没有创建服务端 thread。");
  };
  const handleHitlDecision = (
    decisions: Array<{ type: string; message?: string }>,
  ) => {
    const status = decisions.some((decision) => decision.type === "reject")
      ? "rejected"
      : "approved";
    setMessages((rows) =>
      rows.map((message) =>
        message.hitlData && !message.hitlData.status
          ? { ...message, hitlData: { ...message.hitlData, status } }
          : message,
      ),
    );
    setNotice(`本地审批决定：${JSON.stringify(decisions)}`);
  };
  const recordAcpChoice = (value: string) => {
    setMessages((rows) => [
      ...rows,
      {
        id: `acp-choice-${Date.now()}`,
        role: "assistant",
        content: `本地 ACP 权限选择已记录（${value}）；运行器未启动。`,
        status: "done",
        timestamp: Date.now(),
      },
    ]);
    setNotice(`ACP local receipt: ${value}`);
  };
  const appendUser = (text: string, attachments?: ChatAttachment[]) => {
    if (!text.trim() && !attachments?.length) return false;
    setMessages((rows) => [
      ...rows,
      {
        id: `typed-${Date.now()}`,
        role: "user",
        content: text,
        attachments,
        timestamp: Date.now(),
      },
    ]);
    setNotice(tx("interfacePreview.chatFlow.sendBoundary"));
    return true;
  };
  const enqueueLocal = (item: EnqueueChatItemInput) => {
    if (!item.text.trim() && !item.attachments?.length) return "empty" as const;
    if (queuedItems.length >= 10) return "full" as const;
    setQueuedItems((items) => [
      ...items,
      {
        ...item,
        id: `local-queue-${Date.now()}`,
        createdAt: Date.now(),
        remoteStatus: "queued",
      },
    ]);
    setNotice("消息已加入本地脚本队列。");
    return "ok" as const;
  };
  const resolveQueued = (
    item: QueuedChatItem,
    disposition: "steer" | "follow-up",
  ) => {
    setQueuedItems((items) => items.filter((queued) => queued.id !== item.id));
    setMessages((rows) => [
      ...rows,
      {
        id: `${disposition}-${item.id}`,
        role: "user",
        content: item.text,
        attachments: item.attachments,
        timestamp: Date.now(),
      },
    ]);
    setNotice(
      disposition === "steer"
        ? "已接管为本地当前回合消息。"
        : "已转为本地后续消息。",
    );
    return true;
  };
  const playReply = () => {
    setHistoryLoading(false);
    setEntryState("conversation");
    setMessages(
      Object.prototype.hasOwnProperty.call(messagesByScenario, scenario)
        ? messagesByScenario[scenario]
        : threadSnapshots[session.id] ?? messages,
    );
    setStreaming(scenario === "tools");
    setNotice(
      `${tx("interfacePreview.chatFlow.demo")}: ${
        scenario ? scenarioTitle(scenario, session.name) : session.name
      }`,
    );
  };
  const loadEarlier = () => {
    if (historyLoadPending.current) return false;
    historyLoadPending.current = true;
    setHistoryHasMore(true);
    setHistoryLoadingMore(true);
    setTimeout(() => {
      const before = Date.now() - 1000000;
      setMessages((rows) => [
        {
          id: `older-u-${before}`,
          role: "user",
          content: "更早的本地历史：请继续汇总会议记录。",
          timestamp: before,
        },
        {
          id: `older-a-${before}`,
          role: "assistant",
          content: "这是静态夹具中的历史回复，不是模型生成结果。",
          status: "done",
          timestamp: before + 1000,
        },
        ...rows,
      ]);
      setHistoryLoadingMore(false);
      setHistoryHasMore(false);
      historyLoadPending.current = false;
    }, 350);
    return true;
  };
  const refreshHistory = () => {
    setHistoryRefreshing(true);
    setTimeout(() => setHistoryRefreshing(false), 450);
  };
  const loadLongHistory = () => {
    setHistoryLoading(false);
    setEntryState("conversation");
    const base = Date.now() - 1000000;
    setMessages(
      Array.from({ length: 35 }, (_, index) => [
        {
          id: `long-u-${index}`,
          role: "user" as const,
          content: `历史任务 ${index + 1}：读取知识库条目。`,
          timestamp: base + index * 1000,
        },
        {
          id: `long-a-${index}`,
          role: "assistant" as const,
          content: `静态历史答复 ${index + 1}。`,
          status: "done" as const,
          timestamp: base + index * 1000 + 500,
        },
      ]).flat(),
    );
    setStreaming(false);
    setNotice("显示 35 个历史回合，进入 MessageList 虚拟化路径。");
  };
  const submitWorkflow = (
    submission: Parameters<
      ComponentProps<typeof WorkflowInputCard>["onRun"]
    >[0],
  ) => {
    setWorkflowRun({
      id: null,
      createdAt: Date.now() / 1000,
      inputs: submission.payload.inputs,
    });
    setNotice("本地 workflow 输入已保存；没有启动真实工作流。");
  };
  const interceptUnsafeControl = (event: MouseEvent<HTMLDivElement>) => {
    const button = (event.target as Element).closest("button");
    const icon = button?.querySelector("svg")?.getAttribute("class") ?? "";
    if (icon.includes("lucide-mic")) {
      event.preventDefault();
      event.stopPropagation();
      record("语音输入：硬件麦克风已隔离；请使用本地音频样例播放器。");
    } else if (/lucide-volume|lucide-audio-lines/.test(icon)) {
      event.preventDefault();
      event.stopPropagation();
      record("语音朗读：未调用 TTS 服务；请使用消息中的本地音频样例。");
    } else if (/lucide-wand/.test(icon)) {
      event.preventDefault();
      event.stopPropagation();
      record("文本润色需要模型服务，本地演示不会改写或发送内容。");
    }
  };
  const drawerPath = drawer.startsWith("file:")
    ? drawer.slice(5)
    : drawer.startsWith("citation:")
    ? drawer.slice(9)
    : drawer.startsWith("edit:")
    ? drawer.slice(5)
    : "";
  const dockedTool = messages.find(
    (message) => message.toolData?.callId === toolPanel?.callId,
  )?.toolData;

  return (
    <ChatFilePreviewProvider
      openFilePreview={(path) => {
        void openFileFixture(path);
      }}
      openKnowledgeCitation={openCitationPreview}
    >
      <ChatToolDockProvider
        dockOpen={drawer.startsWith("tool:")}
        openTabs={
          toolPanel
            ? [
                {
                  ...toolPanel,
                  kind: "toolUi",
                  id: dockTabIdForToolCall(toolPanel.callId),
                },
              ]
            : []
        }
        activeTabId={toolPanel ? dockTabIdForToolCall(toolPanel.callId) : null}
        openToolUiPanel={openLocalToolPanel}
        closeToolUiPanel={closeLocalToolPanel}
        focusToolUiPanel={(callId) => setDrawer(`tool:${callId}`)}
      >
        <div
          className={`chatFlowPreview ${detailClass} ${
            refined ? "chatFlowRefined" : ""
          }`}
          onClickCapture={interceptUnsafeControl}
        >
          <ChatSidebarPanel
            isMobile={isMobile}
            sidebarOpen={sidebarOpen}
            sidebarWidth={sidebarWidth}
            isSidebarResizing={isSidebarResizing}
            agents={fixtureAgents}
            sessions={sessions}
            activeThreadId={session.id}
            resolvedAgentId={selectedAgentId}
            sessionsHasMore={sessionsHasMore}
            onLoadMoreSessions={loadMoreLocalSessions}
            sessionsLoadingMore={false}
            onFetchAllSessions={loadMoreLocalSessions}
            onSelectSession={selectLocalSession}
            onAgentSelect={(agentId) => {
              setSelectedAgentId(agentId);
              setNotice(
                `Selected ${
                  fixtureAgents.find((agent) => agent.agent_id === agentId)
                    ?.name ?? agentId
                }; local fixture only.`,
              );
            }}
            onNewChatWithAgent={(agentId) => {
              setSelectedAgentId(agentId);
              setMessages([]);
              setStreaming(false);
              setEntryState("welcome");
            }}
            onDeleteSession={deleteLocalSession}
            onRenameSession={async (id, name) => {
              updateLocalSession(id, { name });
              return true;
            }}
            onPinSession={async (id, pinned) => {
              updateLocalSession(id, { pinned });
              return true;
            }}
            onForkSession={forkCurrent}
            onSidebarOpenChange={setSidebarOpen}
            onSidebarResizeStart={(event) => {
              event.preventDefault();
              resizeOrigin.current = { x: event.clientX, width: sidebarWidth };
              setIsSidebarResizing(true);
            }}
          />
          <main className="chatFlowMain">
            <div className="chatFlowToolbar">
              {isMobile && (
                <Button
                  size="small"
                  onClick={() => setSidebarOpen((open) => !open)}
                >
                  {tx("interfacePreview.chatFlow.mobileHistory")}
                </Button>
              )}
              <Tag color="blue">{tx("interfacePreview.chatFlow.demo")}</Tag>
              <span>{tx("interfacePreview.chatFlow.sendBoundary")}</span>
              <Segmented
                value={refined ? "refined" : "original"}
                onChange={(value) => setRefined(value === "refined")}
                options={[
                  {
                    label: tx("interfacePreview.chatFlow.original"),
                    value: "original",
                  },
                  {
                    label: tx("interfacePreview.chatFlow.refined"),
                    value: "refined",
                  },
                ]}
              />
            </div>
            <div
              className="chatFlowSessionPicker"
              aria-label={tx("interfacePreview.chatFlow.scenarios")}
            >
              {scenarios.map((entry) => (
                <Button
                  key={entry.id}
                  size="small"
                  type={scenario === entry.id ? "primary" : "default"}
                  onClick={() => chooseScenario(entry.id)}
                >
                  {scenarioTitle(entry.id, entry.title)}
                </Button>
              ))}
              <Button
                size="small"
                onClick={() => {
                  setEntryState("welcome");
                  setMessages([]);
                  setHistoryLoading(false);
                }}
              >
                {tx("interfacePreview.chatFlow.welcome")}
              </Button>
              <Button
                size="small"
                onClick={() => {
                  setEntryState("notReady");
                  setMessages([]);
                  setHistoryLoading(false);
                }}
              >
                {tx("interfacePreview.chatFlow.notReady")}
              </Button>
              <Button
                size="small"
                onClick={() => {
                  setEntryState("conversation");
                  setMessages([]);
                  setHistoryLoading(true);
                  setStreaming(false);
                }}
              >
                {tx("interfacePreview.chatFlow.loadingHistory")}
              </Button>
              <Button size="small" onClick={loadLongHistory}>
                {tx("interfacePreview.chatFlow.longHistory")}
              </Button>
              <Button
                size="small"
                onClick={loadEarlier}
                disabled={historyLoadingMore}
              >
                {tx("interfacePreview.chatFlow.loadMore")}
              </Button>
              <Button size="small" onClick={playReply}>
                ▶ {tx("interfacePreview.chatFlow.replayScenario")}
              </Button>
            </div>
            <ChatTitleBar
              session={session}
              title={session.name}
              onRename={(_, name) => updateLocalSession(session.id, { name })}
              onPin={(_, pinned) => updateLocalSession(session.id, { pinned })}
              onFork={forkCurrent}
              onDelete={() => deleteLocalSession(session.id)}
            />
            <div className="chatFlowStream">
              {entryState === "welcome" ? (
                <WelcomeScreen
                  agentName="示例助手"
                  quickCards={[
                    {
                      title: "查询知识库",
                      description: "使用本地搜索和引用夹具",
                      prompt: "查询知识库",
                      color: "#e5484d",
                    },
                  ]}
                  onPromptClick={(prompt) => {
                    appendUser(prompt);
                    setEntryState("conversation");
                  }}
                />
              ) : entryState === "notReady" ? (
                <AgentNotReadyScreen agent={null} noAgents />
              ) : (
                <MessageList
                  messages={messages}
                  sessionKey={session.id}
                  isStreaming={streaming}
                  historyHasMore={historyHasMore}
                  historyLoadingMore={historyLoadingMore}
                  historyRefreshing={historyRefreshing}
                  loading={historyLoading}
                  onLoadMoreHistory={loadEarlier}
                  onRefreshHistory={refreshHistory}
                  onCancel={() => {
                    setStreaming(false);
                    setNotice("本地演示已停止");
                  }}
                  onRegenerate={() => playReply()}
                  onEditUserMessage={(id, text) => {
                    setMessages((rows) =>
                      rows.map((message) =>
                        message.id === id
                          ? { ...message, content: text }
                          : message,
                      ),
                    );
                    setNotice("已在本地更新用户消息；没有重发");
                  }}
                  onForkAssistantMessage={forkCurrent}
                  onHitlDecision={handleHitlDecision}
                  onAcpPermissionSelect={recordAcpChoice}
                  onOpenBrowser={() => setDrawer("browser")}
                  onOpenFiles={() => setDrawer("files")}
                  onEditFile={() => setDrawer("edit:onboarding/guide.md")}
                  onRunShellCommand={(code) => setDrawer(`shell:${code}`)}
                  artifactFiles={
                    scenario === "files"
                      ? []
                      : [
                          {
                            producer_agent_id: "demo-agent",
                            path: "onboarding/guide.md",
                          },
                        ]
                  }
                />
              )}
            </div>
            {scenario === "document" && (
              <details className="chatWorkflowCards">
                <summary>
                  {tx("interfacePreview.chatFlow.workflowCards")}
                </summary>
                <div className="chatFlowPlan">
                  <PlanReadyCard
                    path="plans/onboarding.md"
                    onExecute={() => record("计划执行动作已留在本地演示")}
                    onKeepEditing={() => {
                      setDrawer("edit:plans/onboarding.md");
                    }}
                  />
                </div>
                <WorkflowInputCard
                  inputs={workflowInputs}
                  run={workflowRun}
                  agentId="demo-agent"
                  featureName="新员工入职指南"
                  stateKey="chat-flow-workflow-fixture"
                  busy={false}
                  onRun={submitWorkflow}
                />
                {workflowRun && (
                  <WorkflowOutputCard
                    agentId="demo-agent"
                    files={["onboarding/guide.md", "workflows/summary.md"]}
                    outputs={workflowOutputs}
                    featureName="新员工入职指南"
                    createdAt={workflowRun.createdAt}
                  />
                )}{" "}
                {workflowRun && (
                  <Button size="small" onClick={() => setWorkflowRun(null)}>
                    重置输入夹具
                  </Button>
                )}
              </details>
            )}
            <div className="chatFlowDock">
              {scenario === "ask" && (
                <section
                  className="chatPendingAsk"
                  data-pending={pendingAskStatus === "pending"}
                >
                  <strong>{tx("interfacePreview.chatFlow.pendingAsk")}</strong>
                  <p>{tx("interfacePreview.chatFlow.pendingDockNote")}</p>
                  <AskQuestionCard
                    questions={pendingQuestions}
                    status={pendingAskStatus}
                    onSubmit={(answer) => {
                      setPendingAskStatus("approved");
                      record(`本地回答：${answer}`);
                    }}
                    onDismiss={() => {
                      setPendingAskStatus("rejected");
                      record("本地关闭问题 dock");
                    }}
                  />
                  {pendingAskStatus !== "pending" && (
                    <Button
                      size="small"
                      onClick={() => setPendingAskStatus("pending")}
                    >
                      {tx("interfacePreview.chatFlow.againAsk")}
                    </Button>
                  )}
                </section>
              )}
              {streaming && (
                <section className="chatQueue">
                  <strong>{tx("interfacePreview.chatFlow.queue")}</strong>
                  <span>
                    {tx("interfacePreview.chatFlow.queueDescription")}
                  </span>
                  {queuedItems.map((item) => (
                    <div key={item.id} className="chatQueueItem">
                      <span>{item.text}</span>
                      <Button
                        size="small"
                        onClick={() => resolveQueued(item, "steer")}
                      >
                        {tx("interfacePreview.chatFlow.queueSteer")}
                      </Button>
                      <Button
                        size="small"
                        onClick={() => resolveQueued(item, "follow-up")}
                      >
                        {tx("interfacePreview.chatFlow.queueFollowUp")}
                      </Button>
                      <Button
                        size="small"
                        onClick={() =>
                          setQueuedItems((items) =>
                            items.filter((queued) => queued.id !== item.id),
                          )
                        }
                      >
                        {tx("interfacePreview.chatFlow.queueWithdraw")}
                      </Button>
                    </div>
                  ))}
                  <Button
                    size="small"
                    onClick={() => enqueueLocal({ text: "请核对引用来源" })}
                  >
                    {tx("interfacePreview.chatFlow.queueAdd")}
                  </Button>
                </section>
              )}
            </div>
            <ChatInput
              onSend={appendUser}
              onQueue={enqueueLocal}
              queuedItems={queuedItems}
              onRemoveQueued={(id) =>
                setQueuedItems((items) =>
                  items.filter((item) => item.id !== id),
                )
              }
              onReclaimQueued={(id) => {
                const item = queuedItems.find((queued) => queued.id === id);
                if (!item) return null;
                setQueuedItems((items) =>
                  items.filter((queued) => queued.id !== id),
                );
                return item;
              }}
              onSteerQueued={(item) => resolveQueued(item, "steer")}
              onFollowUpQueued={(item) => resolveQueued(item, "follow-up")}
              onWithdrawQueued={(item) => {
                setQueuedItems((items) =>
                  items.filter((queued) => queued.id !== item.id),
                );
                return true;
              }}
              onCancel={() => setStreaming(false)}
              onNewChat={() => {
                setMessages([]);
                setStreaming(false);
                setWorkflowRun(null);
                setEntryState("welcome");
              }}
              isStreaming={streaming}
              agentId={selectedAgentId}
              threadId={session.threadId}
              availableModels={fixtureModels}
              selectedModel={selectedModel}
              onModelChange={(model) => {
                setSelectedModel(model);
                setNotice(`Selected local model fixture: ${model ?? "none"}`);
              }}
              availableKnowledgeBases={fixtureKnowledgeBases}
              selectedKnowledgeBaseIds={selectedKbIds}
              onKnowledgeBaseIdsChange={setSelectedKbIds}
              availableSkills={fixtureSkills}
              availableAgents={fixtureAgents.map(
                ({ agent_id, name, icon_name, icon_url, color, state }) => ({
                  agent_id,
                  name: `${name} · ${state}`,
                  icon_name,
                  icon_url,
                  color,
                }),
              )}
              availableExperts={fixtureAgents
                .filter((agent) => agent.state === "running")
                .map(({ agent_id, name, icon_name, icon_url, color }) => ({
                  agent_id,
                  name,
                  icon_name,
                  icon_url,
                  color,
                }))}
              availableConnectors={fixtureConnectors}
              selectedConnectors={selectedConnectors}
              onConnectorsChange={setSelectedConnectors}
            />
            <ChatComposerChrome sessionUsageLabel="2,142 tokens · local fixture" />
            {notice && (
              <div className="chatFlowNotice" role="status">
                {notice}
                <Button type="link" size="small" onClick={() => setNotice("")}>
                  ×
                </Button>
              </div>
            )}
          </main>
          <Drawer
            title={
              drawer.startsWith("tool:")
                ? toolPanel?.title ??
                  toolPanel?.toolName ??
                  tx("interfacePreview.chatFlow.demo")
                : drawer.startsWith("citation:")
                ? tx("interfacePreview.chatFlow.citationTitle")
                : drawer.startsWith("file:")
                ? tx("interfacePreview.chatFlow.workspaceFileTitle")
                : drawer.startsWith("edit:")
                ? tx("interfacePreview.chatFlow.editFileTitle")
                : drawer === "files"
                ? tx("interfacePreview.chatFlow.localFilesTitle")
                : drawer === "browser"
                ? tx("interfacePreview.chatFlow.browserTitle")
                : tx("interfacePreview.chatFlow.notice")
            }
            open={Boolean(drawer)}
            onClose={() => setDrawer("")}
            width={isMobile ? "90%" : 480}
            destroyOnHidden
          >
            {drawer.startsWith("tool:") ? (
              dockedTool ? (
                <ToolDetailsInline
                  toolData={dockedTool}
                  isStreaming={false}
                  agentId="demo-agent"
                  forceInline
                />
              ) : (
                <ChatDockToolUiContent
                  threadId={null}
                  callId={toolPanel?.callId ?? ""}
                  agentId="demo-agent"
                  isStreamingTurn={false}
                />
              )
            ) : drawer === "files" ? (
              <div className="chatLocalFiles">
                {Object.keys(fileContents).map((path) => (
                  <p key={path}>
                    <Button
                      type="link"
                      onClick={() => {
                        void openFileFixture(path);
                      }}
                    >
                      {path}
                    </Button>
                  </p>
                ))}
              </div>
            ) : drawer.startsWith("citation:") && activeCitation ? (
              <div className="chatOriginalViewer">
                <div className="chatOriginalViewerActions">{viewerActions}</div>
                <KnowledgeCitationPanelContent
                  key={`${activeCitation.kbId}:${activeCitation.docId}`}
                  citation={activeCitation}
                  onActionsChange={setViewerActions}
                />
              </div>
            ) : (drawer.startsWith("file:") || drawer.startsWith("edit:")) &&
              /\.(md|txt|json|ts|py)$/i.test(drawerPath) ? (
              <div className="chatOriginalViewer">
                <div className="chatOriginalViewerActions">{viewerActions}</div>
                <FilePanelContent
                  key={drawerPath}
                  agentId="demo-agent"
                  filePath={drawerPath}
                  onActionsChange={setViewerActions}
                />
              </div>
            ) : drawer.startsWith("file:") || drawer.startsWith("citation:") ? (
              <section>
                <Tag color="blue">
                  {drawer.startsWith("citation:")
                    ? tx("interfacePreview.chatFlow.citationSource")
                    : tx("interfacePreview.chatFlow.artifactSource")}
                </Tag>
                <h3>{drawerPath}</h3>
                {drawerAssetUrl && /\.(png|jpe?g|svg)$/i.test(drawerPath) ? (
                  <img
                    className="chatLocalScreenshot"
                    src={drawerAssetUrl}
                    alt={`Local fixture ${drawerPath}`}
                  />
                ) : drawerAssetUrl && /\.(webm|mp4)$/i.test(drawerPath) ? (
                  <video controls src={drawerAssetUrl} />
                ) : drawerAssetUrl && /\.(wav|mp3|ogg)$/i.test(drawerPath) ? (
                  <audio controls src={drawerAssetUrl} />
                ) : (
                  <pre className="chatLocalFileContent">
                    {fileContents[drawerPath] ??
                      tx("interfacePreview.chatFlow.missingFixture")}
                  </pre>
                )}
              </section>
            ) : drawer.startsWith("edit:") ? (
              <section>
                <p>{tx("interfacePreview.chatFlow.editBoundary")}</p>
                <textarea
                  className="chatLocalFileEditor"
                  value={fileContents[drawerPath] ?? ""}
                  onChange={(event) => {
                    editedFixtureFiles.set(drawerPath, event.target.value);
                    setFileContents((current) => ({
                      ...current,
                      [drawerPath]: event.target.value,
                    }));
                  }}
                  aria-label={`Edit ${drawerPath}`}
                />
              </section>
            ) : drawer === "browser" ? (
              <section>
                <Tag color="blue">
                  {tx("interfacePreview.chatFlow.browserCaptureNote")}
                </Tag>
                <h3>Release notes · version 2.4</h3>
                <p>
                  Local preview content: the release notes page contains fixture
                  text and a locally embedded SVG screenshot.
                </p>
                <img src={img} alt="Local release note preview fixture" />
              </section>
            ) : drawer.startsWith("shell:") ? (
              <section>
                <Tag color="orange">
                  {tx("interfacePreview.chatFlow.notExecuted")}
                </Tag>
                <pre>{drawer.slice(6)}</pre>
                <p>{tx("interfacePreview.chatFlow.genericReceipt")}</p>
              </section>
            ) : drawer.startsWith("Bundled renderer request blocked:") ? (
              <section>
                <Tag color="red">
                  {tx("interfacePreview.chatFlow.externalRequestBlocked")}
                </Tag>
                <pre>{drawer}</pre>
                <p>{tx("interfacePreview.chatFlow.genericReceipt")}</p>
              </section>
            ) : (
              <section>
                <p>{drawer}</p>
                <p>{tx("interfacePreview.chatFlow.genericReceipt")}</p>
              </section>
            )}
          </Drawer>
        </div>
      </ChatToolDockProvider>
    </ChatFilePreviewProvider>
  );
}
