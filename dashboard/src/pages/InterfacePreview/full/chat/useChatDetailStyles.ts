import { createStyles } from "antd-style";
import chat from "../../../Chat/index.module.less";
import markdown from "../../../../components/Markdown/index.module.less";
import ask from "../../../Chat/components/AskQuestionCard.module.less";
import approval from "../../../Chat/components/HitlApprovalCard.module.less";
import plan from "../../../Chat/components/PlanReadyCard.module.less";
import workflowInput from "../../../Chat/components/WorkflowInputCard.module.less";
import workflowOutput from "../../../Chat/components/WorkflowOutputCard.module.less";
import todo from "../../../../components/TodoProgressPanel.module.less";

// Target the original modules' exported classes, never hash substrings or global page CSS.
const useStyles = createStyles(({ css }) => ({
  refined: css`
    & .${chat.messageListInner} {
      --chat-msg-avatar-slot: 32px;
    }
    & .${chat.messageBubble} {
      margin-bottom: 10px;
      column-gap: 0;
    }
    & .${chat.assistantTurn} {
      gap: 6px;
      margin-bottom: 12px;
    }
    & .${chat.assistantTurn} .${chat.messageBubble} {
      margin-bottom: 0;
    }
    & .${chat.hitlSegment} {
      gap: 6px;
    }
    & .${chat.msgUserAvatar} {
      width: 28px;
      height: 28px;
      border-radius: 8px;
      background: #34443a;
      font-size: 11px;
    }
    & .${chat.userText} {
      padding: 7px 11px;
      color: var(--fn-text-primary);
      background: #f1f3ef;
      border: 0;
      border-radius: 8px;
      font-size: 14px;
      line-height: 1.55;
    }
    & .${chat.assistantText} {
      padding: 0;
      background: transparent;
      border: 0;
      border-radius: 0;
      font-size: 14px;
      line-height: 1.6;
      box-shadow: none;
    }
    & .${chat.msgMetaRow} {
      gap: 6px;
      margin-top: 3px;
      padding-top: 0;
      color: #92998f;
      font-size: 10px;
      font-variant-numeric: tabular-nums;
    }
    & .${chat.msgCopyBtn}, & .${chat.msgActionBtn} {
      border-radius: 4px;
      box-shadow: none;
    }
    & .${chat.msgCopyBtn} {
      opacity: 0.65;
      transition:
        color 120ms ease,
        background 120ms ease;
    }
    & .${chat.msgCopyBtn}:hover, & .${chat.msgCopyBtn}:focus-visible {
      opacity: 1;
      color: var(--fn-text-primary);
      background: var(--fn-bg-hover);
    }
    & .${chat.processSummary} {
      padding: 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      box-shadow: none;
    }
    & .${chat.processSummaryToggle} {
      align-self: flex-start;
      width: auto;
      max-width: 100%;
      min-height: 24px;
      padding: 3px 0;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: #7b8578;
      font-size: 12px;
    }
    & .${chat.processSummaryToggle}:hover {
      color: var(--fn-text-primary);
    }
    & .${chat.processSummaryBody} {
      gap: 2px;
      margin: 3px 0 3px 5px;
      padding: 0 0 0 12px;
      border-top: 0;
      border-left: 1px solid #dce3d9;
    }
    & .${chat.processThinkingItem} {
      padding: 2px 0 6px;
      border: 0;
      border-radius: 0;
      background: transparent;
      color: #7b8578;
      font-size: 12px;
      line-height: 1.55;
    }
    & .${chat.inlineToolBlock} {
      margin-top: 2px;
    }
    & .${chat.inlineToolSummary} {
      min-height: 28px;
      gap: 6px;
      padding: 4px 2px;
      border: 0;
      border-radius: 4px;
      background: transparent;
      box-shadow: none;
    }
    & .${chat.inlineToolSummary}:hover {
      background: #f4f6f1;
    }
    & .${chat.inlineToolName} {
      font-family: inherit;
      font-size: 12px;
      line-height: 1.5;
      font-weight: 500;
    }
    & .${chat.inlineToolLabel} {
      padding: 0;
      border-radius: 0;
      background: transparent;
      color: #8b9588;
      font-size: 10px;
      font-weight: 400;
      letter-spacing: 0;
    }
    & .${chat.inlineToolStatus} {
      font-size: 11px;
      font-variant-numeric: tabular-nums;
    }
    & .${chat.inlineToolDetails} {
      gap: 4px;
      margin-top: 2px;
      padding: 6px 8px;
      border: 0;
      border-radius: 4px;
      background: #f5f7f2;
    }
    & .${chat.pinnedToolResults} {
      gap: 6px;
      margin-top: 4px;
    }
    & .${chat.knowledgeCitations} {
      gap: 4px;
    }
    & .${chat.knowledgeCitationChip} {
      padding: 2px 6px;
      border: 1px solid #e0e5dd;
      border-radius: 4px;
      background: #f7f9f4;
      box-shadow: none;
      color: #4c5f48;
      font-size: 11px;
    }
    & .${chat.knowledgeCitationChip}:hover {
      border-color: #a4b99c;
      background: #f0f5eb;
    }
    & .${chat.knowledgeCitationKb} {
      color: #85927d;
    }
    & .${chat.openBrowserPrompt} {
      width: fit-content;
      min-height: 28px;
      gap: 6px;
      padding: 4px 8px;
      border: 1px solid #dfe5da;
      border-radius: 5px;
      background: #f7f9f4;
      box-shadow: none;
      color: #485843;
      font-size: 12px;
      font-weight: 500;
    }
    & .${chat.openBrowserPromptIcon} {
      color: #66825b;
    }
    & .${chat.toolMediaImage}, & .${chat.toolMediaVideo} {
      border: 1px solid var(--fn-border-primary);
      border-radius: 8px;
    }
    & .${chat.chatInput} {
      padding: 6px 16px 8px;
    }
    &:has(.chatPendingAsk[data-pending="true"]) .${chat.chatInput} {
      display: none;
    }
    & .${chat.inputWrapper} {
      padding: 7px 10px;
      border: 1px solid #dce2d7;
      border-radius: 10px;
      background: #fcfdfb;
      box-shadow: none;
    }
    & .${chat.inputWrapper}:focus-within {
      border-color: #a2b59a;
      box-shadow: 0 0 0 2px #7892670d;
    }
    & .${chat.textarea} {
      min-height: 36px !important;
      font-size: 14px;
      line-height: 1.5;
    }
    @supports (field-sizing: content) {
      & .${chat.textarea} {
        /* Replace the original inline height only where native autogrow is supported. */
        field-sizing: content;
        height: auto !important;
        max-height: 160px;
        min-width: 0;
        overflow-y: auto;
      }
    }
    & .${chat.actionsRow} {
      margin-top: 3px;
      padding-top: 3px;
      border-top: 0;
    }
    & .${chat.contextChip} {
      border-color: #e1e6dc;
      border-radius: 4px;
      background: #f3f6ee;
      color: #64715c;
    }
    & .${chat.contextChipCompact} {
      height: 22px;
      padding: 0 6px;
      font-size: 11px;
    }
    & .${chat.sendBtn} {
      border-radius: 7px;
      background: #354b38;
      box-shadow: none;
    }
    & .${chat.aiDisclaimer} {
      margin-top: 4px;
    }
    & .${chat.errorMessageBox} {
      padding: 10px 12px;
      border-radius: 6px;
      box-shadow: none;
    }
    & .${ask.card}, & .${approval.card}, & .${plan.card} {
      padding: 10px 12px;
      border-radius: 7px;
      box-shadow: none;
      background: var(--fn-bg-primary);
      border-color: #e0e4dc;
    }
    & .${ask.title}, & .${approval.title} {
      font-size: 13px;
      font-weight: 600;
    }
    & .${ask.titleRow}, & .${approval.titleRow} {
      margin-bottom: 7px;
      padding-bottom: 6px;
      border-bottom: 1px solid #eef0e9;
    }
    & .${ask.option} {
      min-height: 36px;
      padding: 6px 9px;
      border-radius: 5px;
      box-shadow: none;
      transform: none;
    }
    & .${ask.option}:hover:not(:disabled) {
      transform: none;
      box-shadow: none;
    }
    & .${ask.selected} {
      border-color: #a0b493;
      background: #f1f6eb;
    }
    & .${approval.card} {
      border-left: 2px solid var(--fn-color-warning, #d48806);
    }
    & .${approval.iconWrap} {
      width: 22px;
      height: 22px;
      border-radius: 5px;
    }
    & .${approval.toolName} {
      color: #6b765f;
      font-size: 12px;
    }
    & .${approval.actionButton} {
      height: 30px;
      padding-inline: 11px;
      border-radius: 5px;
      font-size: 12px;
      box-shadow: none;
    }
    &
      .${approval.primaryAction},
      &
      .${approval.primaryAction}:hover:not(:disabled) {
      border-color: #3e5540;
      background: #3e5540;
      box-shadow: none;
    }
    & .${todo.todoListBlock} {
      margin-top: 4px;
      margin-bottom: 4px;
      padding: 8px 10px;
      border: 0;
      border-left: 2px solid #cedac5;
      border-radius: 0;
      background: #f8faf5;
      box-shadow: none;
    }
    & .${todo.todoListHeader} {
      margin-bottom: 5px;
    }
    & .${todo.todoListItems} {
      gap: 4px;
    }
    & .${workflowInput.card} {
      border-color: #e0e4dc;
      border-radius: 5px;
      background: #f8faf5;
    }
    & .${workflowInput.summaryButton} {
      min-height: 26px;
      font-size: 12px;
    }
    & .${workflowOutput.result} {
      border-left-color: #cedac5;
    }
    & .${workflowOutput.fileRow} {
      padding-block: 5px;
      border-bottom: 1px solid #eef0e9;
    }
    & .${workflowOutput.fileLabel} {
      font-weight: 500;
    }
    & .${markdown.markdownBody} {
      font-size: 14px;
      line-height: 1.6;
      overflow-wrap: anywhere;
    }
    & .${markdown.markdownBody} p {
      margin: 5px 0;
    }
    & .${markdown.markdownBody} h1,
    & .${markdown.markdownBody} h2,
    & .${markdown.markdownBody} h3 {
      margin: 12px 0 6px;
      border-bottom: 0;
      color: #303b2d;
      font-weight: 600;
      letter-spacing: -0.01em;
    }
    & .${markdown.markdownBody} h1 {
      font-size: 20px;
    }
    & .${markdown.markdownBody} h2 {
      font-size: 17px;
    }
    & .${markdown.markdownBody} h3 {
      font-size: 15px;
    }
    & .${markdown.markdownBody} ul,
    & .${markdown.markdownBody} ol {
      margin: 6px 0;
      padding-left: 20px;
    }
    & .${markdown.markdownBody} li {
      margin: 2px 0;
    }
    & .${markdown.markdownBody} a {
      text-decoration-thickness: 1px;
      text-underline-offset: 3px;
    }
    & .${markdown.markdownBody} blockquote {
      margin-block: 6px;
      padding: 4px 10px;
      border-left: 2px solid #d7e1ce;
      border-radius: 0;
      color: #6e7b64;
      background: #f8faf5;
    }
    & .${markdown.codeBlockWrapper} {
      margin-block: 8px;
      border-color: #e0e5dc;
      border-radius: 6px;
    }
    & .${markdown.codeBlockHeader} {
      padding: 4px 10px;
      background: #f5f7f2;
      color: #7f8c75;
      font-size: 11px;
    }
    & .${markdown.markdownBody} th {
      background: #f5f7f2;
      font-weight: 500;
    }
    & .${markdown.markdownBody} td,
    & .${markdown.markdownBody} th {
      padding: 6px 10px;
      border-color: #e8ece3;
      font-size: 12px;
    }
    & .${markdown.markdownBody} tbody tr:hover td {
      background: #f8faf5;
    }
    & [data-octop-plugin-ui],
    & .octop-builtin-ui-fallback {
      border-radius: 8px;
      box-shadow: none;
      overflow-wrap: anywhere;
    }
    & [data-octop-plugin-ui] > :first-child {
      border-radius: 8px !important;
      box-shadow: none !important;
    }
    @media (max-width: 767px) {
      & .${chat.chatInput} {
        padding-inline: 10px;
      }
      & .${chat.userText}, & .${markdown.markdownBody} {
        font-size: 15px;
      }
      & .${ask.option} {
        min-height: 40px;
      }
      & .${approval.actionButton} {
        height: 36px;
      }
    }
    @media (prefers-reduced-motion: reduce) {
      & .${chat.msgCopyBtn} {
        transition: none;
      }
    }
  `,
}));

export function useChatDetailStyles(refined: boolean): string {
  const { styles } = useStyles();
  return refined ? styles.refined : "";
}
