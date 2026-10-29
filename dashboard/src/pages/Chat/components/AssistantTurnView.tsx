import type { ThreadArtifact } from "../../../api/modules/octopThreads";
import { useCallback, useMemo } from "react";
import { ChevronRight, FilePen, Globe } from "lucide-react";
import { useNavigate } from "react-router-dom";
import { useTranslation } from "react-i18next";
import type { ChatMessage } from "../hooks/useChat";
import {
  splitAssistantTurn,
  toAnswerOnlyMessage,
  turnUsedBrowserTool,
  turnUsedFileTool,
} from "../utils/messageContent";
import { isWriteToolName } from "../constants";
import { collectChatFilePaths } from "../hooks/useChatFileDetection";
import { layoutAssistantTurnHitl } from "../utils/layoutAssistantTurnHitl";
import { useAgent } from "../../../context/AgentContext";
import { TodoProgressPanel } from "../../../components/TodoProgressPanel";
import {
  collectWriteTodosFromMessages,
  isWriteTodosToolName,
} from "../../../utils/parseWriteTodos";
import MessageBubble from "./MessageBubble";
import { ToolMediaStrip } from "./ToolMediaStrip";
import ArtifactFilesCard from "./ArtifactFilesCard";
import {
  TurnProcessBlocks,
  turnHasProcessSummary,
  turnHasVisibleProcess,
} from "./TurnProcessBlocks";
import { collectTurnToolMedia } from "../../../utils/collectTurnToolMedia";
import { ChatKnowledgeCitationsProvider } from "../ChatKnowledgeCitationsContext";
import { collectTurnKnowledgeCitations } from "../../../utils/collectTurnKnowledgeCitations";
import type { KnowledgeCitation } from "../../../utils/parseKnowledgeCitations";
import WorkflowChangeCard, {
  collectWorkflowChanges,
} from "./WorkflowChangeCard";
import styles from "../index.module.less";

interface AssistantTurnViewProps {
  messages: ChatMessage[];
  agentId?: string | null;
  isStreaming?: boolean;
  /** True while this assistant turn is still being generated (incl. between tool calls). */
  isTurnInProgress?: boolean;
  onRegenerate?: (messageId: string) => void;
  onEditUserMessage?: (messageId: string, newText: string) => void;
  onForkAssistantMessage?: (messageId: string) => void;
  forkDisabled?: boolean;
  forkDisabledHint?: string;
  onAcpPermissionSelect?: (message: string) => void;
  onHitlDecision?: (
    decisions: Array<{ type: string; message?: string }>,
  ) => void;
  onOpenBrowser?: () => void;
  onEditFile?: () => void;
  onOpenFiles?: () => void;
  /** Thread files written during the turn (including via scripts/shell). */
  artifactFiles?: readonly ThreadArtifact[];
  onRunShellCommand?: (code: string) => void;
  shellCommandDisabled?: boolean;
  shellCommandDisabledTitle?: string;
  compactProcess?: boolean;
  /**
   * Citations collected from the whole thread. Replayed history keeps tool
   * messages outside this turn's `messages`, so the turn cannot find them.
   */
  threadKnowledgeCitations?: KnowledgeCitation[];
}

function hasProcessContent(
  split: ReturnType<typeof splitAssistantTurn>,
): boolean {
  return turnHasVisibleProcess(split);
}

export default function AssistantTurnView({
  messages,
  agentId: agentIdProp,
  isStreaming = false,
  isTurnInProgress = false,
  onRegenerate,
  onEditUserMessage,
  onForkAssistantMessage,
  forkDisabled,
  forkDisabledHint,
  onAcpPermissionSelect,
  onHitlDecision,
  onOpenBrowser,
  onEditFile,
  onOpenFiles,
  artifactFiles = [],
  onRunShellCommand,
  shellCommandDisabled,
  shellCommandDisabledTitle,
  compactProcess = false,
  threadKnowledgeCitations,
}: AssistantTurnViewProps) {
  const { t } = useTranslation();
  const { activeAgentId, agents } = useAgent();
  const agentId = agentIdProp ?? activeAgentId;

  const hitlLayout = useMemo(
    () => layoutAssistantTurnHitl(messages),
    [messages],
  );
  const hasPendingHitl = messages.some((m) => m.hitlData?.status === "pending");

  const segmentProcess = useMemo(
    () =>
      hitlLayout.segments.map((seg) => ({
        split: splitAssistantTurn(seg.processMessages),
        hitl: seg.hitlMessage,
      })),
    [hitlLayout],
  );
  const trailingSplit = useMemo(
    () => splitAssistantTurn(hitlLayout.trailingMessages),
    [hitlLayout],
  );

  const fullSplit = useMemo(() => splitAssistantTurn(messages), [messages]);

  const toolMedia = useMemo(
    () => collectTurnToolMedia(fullSplit, agentId),
    [fullSplit, agentId],
  );
  const knowledgeCitations = useMemo(() => {
    const own = collectTurnKnowledgeCitations(fullSplit);
    if (own.length > 0) return own;
    // Replayed history drops standalone tool rows and attaches their result to
    // the assistant message instead, so scan the turn's messages as well.
    const replayed = collectTurnKnowledgeCitations({
      tools: messages,
      thinkings: [],
      processSteps: [],
      answerMessage: null,
    });
    if (replayed.length > 0) return replayed;
    return threadKnowledgeCitations ?? [];
  }, [fullSplit, messages, threadKnowledgeCitations]);
  const navigate = useNavigate();
  const openKnowledgeDocument = useCallback(
    (kbId: string, docId: string) => {
      const params = new URLSearchParams({ kb: kbId, doc: docId });
      navigate(`/knowledge-bases?${params.toString()}`);
    },
    [navigate],
  );

  const turnStreaming =
    isTurnInProgress ||
    (isStreaming && messages.some((m) => m.status === "streaming"));
  const usedBrowser = turnUsedBrowserTool(fullSplit);
  const showOpenBrowser = usedBrowser && !!onOpenBrowser;
  const usedFileTool = turnUsedFileTool(fullSplit);
  const showArtifacts =
    !turnStreaming && Boolean(agentId) && artifactFiles.length > 0;
  const showEditFile = !showArtifacts && usedFileTool && !!onEditFile;
  const turnFileCount = useMemo(() => {
    const fromPaths = collectChatFilePaths(messages, agentId).length;
    if (fromPaths > 0) return fromPaths;
    return (fullSplit.tools ?? []).filter((msg) =>
      isWriteToolName(msg.toolData?.name),
    ).length;
  }, [messages, agentId, fullSplit.tools]);
  const hasToolMedia =
    toolMedia.images.length > 0 ||
    toolMedia.videos.length > 0 ||
    toolMedia.files.length > 0;

  const todoItems = useMemo(
    () => collectWriteTodosFromMessages(messages),
    [messages],
  );
  const workflowChanges = useMemo(
    () => collectWorkflowChanges(messages),
    [messages],
  );
  const canUndoWorkflow = agents.some(
    (agent) => agent.agent_id === agentId && agent.is_owner === true,
  );
  const todoStreaming =
    turnStreaming &&
    messages.some(
      (m) => m.status === "streaming" && isWriteTodosToolName(m.toolData?.name),
    );

  const firstProcessSegmentIdx = compactProcess
    ? -1
    : segmentProcess.findIndex(({ split }) => hasProcessContent(split));
  const firstSummaryIdx = compactProcess
    ? -1
    : segmentProcess.findIndex(({ split }) => turnHasProcessSummary(split));
  const showTrailingProcess =
    !compactProcess && hasProcessContent(trailingSplit);
  const trailingHasSummary =
    !compactProcess && turnHasProcessSummary(trailingSplit);
  const anyProcessShown = firstProcessSegmentIdx >= 0 || showTrailingProcess;

  const todoPanel =
    todoItems.length > 0 ? (
      <TodoProgressPanel
        items={todoItems}
        isStreaming={todoStreaming}
        followingProcessSummary={anyProcessShown}
      />
    ) : null;
  const todoAtTop = todoPanel && !anyProcessShown ? todoPanel : null;

  return (
    <ChatKnowledgeCitationsProvider value={knowledgeCitations}>
      <div className={styles.assistantTurn}>
        {todoAtTop ? <div className={styles.turnInset}>{todoAtTop}</div> : null}
        {segmentProcess.map(({ split, hitl }, idx) => {
          const showProcess = !compactProcess && hasProcessContent(split);
          // Freeze process spinner while a pending approval card is open.
          const processStreaming =
            turnStreaming &&
            !hasPendingHitl &&
            idx === segmentProcess.length - 1 &&
            hitlLayout.trailingMessages.length === 0;
          return (
            <div key={hitl.id} className={styles.hitlSegment}>
              {showProcess ? (
                <>
                  <TurnProcessBlocks
                    split={split}
                    isStreaming={processStreaming}
                    onAcpPermissionSelect={onAcpPermissionSelect}
                    hideToolMedia={hasToolMedia}
                    agentId={agentId}
                    showAvatar={idx === firstSummaryIdx}
                  />
                  {todoPanel && idx === firstProcessSegmentIdx ? (
                    <div className={styles.turnInset}>{todoPanel}</div>
                  ) : null}
                </>
              ) : null}
              <MessageBubble
                message={hitl}
                agentId={agentId}
                onHitlDecision={onHitlDecision}
                groupPosition="only"
              />
            </div>
          );
        })}
        {showTrailingProcess ? (
          <>
            <TurnProcessBlocks
              split={trailingSplit}
              isStreaming={turnStreaming && !hasPendingHitl}
              onAcpPermissionSelect={onAcpPermissionSelect}
              hideToolMedia={hasToolMedia}
              agentId={agentId}
              showAvatar={firstSummaryIdx < 0 && trailingHasSummary}
            />
            {todoPanel && firstProcessSegmentIdx < 0 ? (
              <div className={styles.turnInset}>{todoPanel}</div>
            ) : null}
          </>
        ) : null}
        {hasToolMedia && (
          <div className={styles.turnInset}>
            <ToolMediaStrip
              images={toolMedia.images}
              videos={toolMedia.videos}
              files={toolMedia.files}
              agentId={agentId}
            />
          </div>
        )}
        {trailingSplit.answerMessage ? (
          <div className={styles.assistantTurnAnswer}>
            <MessageBubble
              message={toAnswerOnlyMessage(trailingSplit.answerMessage)}
              agentId={agentId}
              showAvatar={firstSummaryIdx < 0 && !trailingHasSummary}
              onRegenerate={onRegenerate}
              onEditUserMessage={onEditUserMessage}
              onForkAssistantMessage={onForkAssistantMessage}
              forkDisabled={forkDisabled}
              forkDisabledHint={forkDisabledHint}
              groupPosition="only"
              onRunShellCommand={onRunShellCommand}
              shellCommandDisabled={shellCommandDisabled}
              shellCommandDisabledTitle={shellCommandDisabledTitle}
              knowledgeCitations={knowledgeCitations}
              onKnowledgeLink={openKnowledgeDocument}
            />
          </div>
        ) : null}
        {showArtifacts && agentId ? (
          <div className={styles.turnInset}>
            <ArtifactFilesCard
              agentId={agentId}
              files={artifactFiles}
              onOpenFiles={onOpenFiles}
            />
          </div>
        ) : null}
        {agentId && workflowChanges.length > 0 && (
          <div className={styles.turnInset}>
            {workflowChanges.map((change) => (
              <WorkflowChangeCard
                key={change.change_id}
                agentId={agentId}
                change={change}
                canUndo={canUndoWorkflow || change.target === "overlay"}
              />
            ))}
          </div>
        )}
        {showOpenBrowser && (
          <div className={styles.turnInset}>
            <button
              type="button"
              className={`${styles.openBrowserPrompt} ${
                turnStreaming ? styles.openBrowserPromptActive : ""
              }`}
              onClick={onOpenBrowser}
              aria-label={t("chat.openBrowser")}
            >
              <Globe
                size={16}
                strokeWidth={2}
                className={styles.openBrowserPromptIcon}
                aria-hidden="true"
              />
              <span>{t("chat.openBrowser")}</span>
              <ChevronRight
                size={14}
                className={styles.openBrowserPromptArrow}
                aria-hidden="true"
              />
            </button>
          </div>
        )}
        {showEditFile && (
          <div className={styles.turnInset}>
            <button
              type="button"
              className={`${styles.openBrowserPrompt} ${
                turnStreaming ? styles.openBrowserPromptActive : ""
              }`}
              onClick={onEditFile}
              aria-label={t("chat.editFileCard", {
                count: Math.max(turnFileCount, 1),
                defaultValue: "编辑了{{count}}个文件",
              })}
            >
              <FilePen
                size={16}
                strokeWidth={2}
                className={styles.openBrowserPromptIcon}
                aria-hidden="true"
              />
              <span>
                {t("chat.editFileCard", {
                  count: Math.max(turnFileCount, 1),
                  defaultValue: "编辑了{{count}}个文件",
                })}
              </span>
              <ChevronRight
                size={14}
                className={styles.openBrowserPromptArrow}
                aria-hidden="true"
              />
            </button>
          </div>
        )}
      </div>
    </ChatKnowledgeCitationsProvider>
  );
}
