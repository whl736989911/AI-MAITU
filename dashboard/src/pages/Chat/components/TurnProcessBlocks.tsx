import { useMemo } from "react";
import type { ChatMessage } from "../hooks/useChat";
import type { AssistantTurnSplit } from "../utils/messageContent";
import { countProcessStats } from "../utils/messageContent";
import { partitionPinnedTools } from "../../../plugins/toolRenderers/isPinnedToolUi";
import { useToolRendererVersion } from "../../../plugins/toolRenderers";
import AssistantProcessSummary from "./AssistantProcessSummary";
import { ToolDetailsInline } from "./MessageBubble";
import { ExpertMessageAvatar } from "./MessageSender";
import styles from "../index.module.less";
import { useAgent } from "../../../context/AgentContext";

interface TurnProcessBlocksProps {
  split: AssistantTurnSplit;
  isStreaming: boolean;
  onAcpPermissionSelect?: (message: string) => void;
  hideToolMedia: boolean;
  agentId: string | null;
  /** Show the turn's one assistant avatar on this process block. */
  showIdentity?: boolean;
}

function hasFoldContent(split: AssistantTurnSplit): boolean {
  const { toolCount, thinkingCount } = countProcessStats(split);
  return toolCount > 0 || thinkingCount > 0;
}

/** Process summary (fold) and rich tool UIs as **sibling** blocks — not nested. */
export function TurnProcessBlocks({
  split,
  isStreaming,
  onAcpPermissionSelect,
  hideToolMedia,
  agentId,
  showIdentity = false,
}: TurnProcessBlocksProps) {
  const { agents, activeAgent } = useAgent();
  const speakerAgentId =
    split.answerMessage?.speakerAgentId ||
    split.tools.find((message) => message.speakerAgentId)?.speakerAgentId ||
    agentId;
  const expert =
    (speakerAgentId &&
      agents.find((item) => item.agent_id === speakerAgentId)) ||
    activeAgent;
  const rendererVersion = useToolRendererVersion();
  const { pinned, folded } = useMemo(
    () => partitionPinnedTools(split),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [split, rendererVersion],
  );
  const showFold = hasFoldContent(folded);

  if (!showFold && pinned.length === 0) return null;

  return (
    <>
      {showFold ? (
        <div className={styles.processSummaryRow}>
          {showIdentity && expert ? (
            <div className={styles.avatarCol}>
              <ExpertMessageAvatar
                name={expert.name}
                color={expert.color}
                iconName={expert.icon_name}
                iconUrl={expert.icon_url}
              />
            </div>
          ) : null}
          <AssistantProcessSummary
            split={folded}
            statsSplit={split}
            isStreaming={isStreaming}
            onAcpPermissionSelect={onAcpPermissionSelect}
            hideToolMedia={hideToolMedia}
            agentId={agentId}
          />
        </div>
      ) : showIdentity && expert ? (
        <div className={styles.processSummaryRow}>
          <div className={styles.avatarCol}>
            <ExpertMessageAvatar
              name={expert.name}
              color={expert.color}
              iconName={expert.icon_name}
              iconUrl={expert.icon_url}
            />
          </div>
        </div>
      ) : null}
      {pinned.length > 0 ? (
        <div className={styles.turnInset}>
          <div className={styles.pinnedToolResults} data-octop-pinned-tools="">
            {pinned.map((message: ChatMessage) =>
              message.toolData ? (
                <div key={message.id} className={styles.pinnedToolResultItem}>
                  <ToolDetailsInline
                    toolData={message.toolData}
                    isStreaming={message.status === "streaming" && isStreaming}
                    onAcpPermissionSelect={onAcpPermissionSelect}
                    hideMediaPreview={hideToolMedia}
                    agentId={agentId}
                  />
                </div>
              ) : null,
            )}
          </div>
        </div>
      ) : null}
    </>
  );
}

export function turnHasVisibleProcess(split: AssistantTurnSplit): boolean {
  const { pinned, folded } = partitionPinnedTools(split);
  return hasFoldContent(folded) || pinned.length > 0;
}
