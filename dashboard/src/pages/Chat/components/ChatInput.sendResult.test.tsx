import { describe, expect, it, vi, beforeEach } from "vitest";
import { render, screen, fireEvent } from "@testing-library/react";

const attachmentsMock = vi.hoisted(() => ({
  attachments: [] as Array<Record<string, unknown>>,
  clearAttachments: vi.fn(),
}));

vi.mock("../../../hooks/useIsMobile", () => ({
  useIsMobile: () => false,
}));

vi.mock("../../../hooks/useSlashCommands", () => ({
  useSlashCommands: () => ({}),
}));

vi.mock("../../../hooks/useVoiceInput", () => ({
  useVoiceInput: () => ({
    recording: false,
    transcribing: false,
    toggle: vi.fn(),
  }),
}));

vi.mock("../../../hooks/useKeyboardOffset", () => ({
  useKeyboardOffset: () => undefined,
}));

vi.mock("../hooks/useChatAttachments", () => ({
  useChatAttachments: () => ({
    attachments: attachmentsMock.attachments,
    uploading: false,
    dragOver: false,
    fileInputRef: { current: null },
    handleFileSelect: vi.fn(),
    handleFileChange: vi.fn(),
    removeAttachment: vi.fn(),
    clearAttachments: attachmentsMock.clearAttachments,
    restoreAttachments: vi.fn(),
    handlePaste: vi.fn(),
    handleDragEnter: vi.fn(),
    handleDragLeave: vi.fn(),
    handleDragOver: vi.fn(),
    handleDrop: vi.fn(),
  }),
}));

const writeInputDraft = vi.hoisted(() => vi.fn());

vi.mock("../hooks/chatStore", () => ({
  consumePendingPrefillAttachments: () => [],
  readInputDraft: () => "",
  writeInputDraft,
}));

vi.mock("./ChatInputPreviewBar", () => ({
  default: () => null,
}));

vi.mock("./ChatInputActionsRow", () => ({
  default: ({
    onSubmit,
    canSend,
  }: {
    onSubmit: () => void;
    canSend: boolean;
  }) => (
    <button type="button" disabled={!canSend} onClick={onSubmit}>
      send
    </button>
  ),
}));

vi.mock("./ChatQueuedMessages", () => ({
  default: () => null,
}));

vi.mock("./SlashCommandMenu", () => ({
  default: () => null,
}));

vi.mock("./MentionPickerMenu", () => ({
  default: () => null,
  buildMentionItems: () => [],
}));

vi.mock("../hooks/useSlashMentionInput", () => ({
  useSlashMentionInput: ({
    text,
    setText,
    onSubmitRef,
  }: {
    text: string;
    setText: (v: string) => void;
    onSubmitRef: { current: () => void };
  }) => ({
    slashMenuOpen: false,
    slashMenuIndex: 0,
    setSlashMenuIndex: vi.fn(),
    mentionMenuOpen: false,
    mentionMenuIndex: 0,
    setMentionMenuIndex: vi.fn(),
    mentionQuery: "",
    mentionAgents: [],
    slashMenuFlat: [],
    slashMenuGroups: [],
    slashPickerGroups: [],
    slashMenuItems: [],
    mentionItems: [],
    filesLoading: false,
    filesError: null,
    runSlashCommand: vi.fn(),
    matchSlashCommand: () => null,
    handleMentionSelect: vi.fn(),
    handleSlashSelect: vi.fn(),
    handleTextChange: setText,
    handleKeyDown: (e: { key: string; preventDefault: () => void }) => {
      if (e.key === "Enter" && !e.preventDefault) return;
      void text;
      void onSubmitRef;
    },
  }),
}));

import ChatInput from "./ChatInput";

const ATTACHMENT = {
  url: "",
  filename: "notes.txt",
  workspacePath: "inbound/notes.txt",
  kind: "file",
};

function renderInput(onSend: (text: string, attachments?: unknown) => boolean) {
  render(
    <ChatInput
      onSend={onSend}
      onCancel={vi.fn()}
      onNewChat={vi.fn()}
      isStreaming={false}
      initialText=""
      agentId="agent-1"
      threadId="thread-1"
    />,
  );
  return screen.getByRole("textbox") as HTMLTextAreaElement;
}

beforeEach(() => {
  vi.clearAllMocks();
  attachmentsMock.attachments = [];
});

describe("ChatInput send accepted/rejected result", () => {
  it("keeps text, draft, and attachments when onSend rejects the send", () => {
    attachmentsMock.attachments = [ATTACHMENT];
    const textarea = renderInput(vi.fn(() => false));

    fireEvent.change(textarea, { target: { value: "需要重试的消息" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    // Rejected (HITL pause / refusal): the user retries without retyping.
    expect(textarea.value).toBe("需要重试的消息");
    expect(writeInputDraft).not.toHaveBeenCalledWith("agent-1", "thread-1", "");
    expect(attachmentsMock.clearAttachments).not.toHaveBeenCalled();
  });

  it("clears text, draft, and attachments only when onSend accepts", () => {
    attachmentsMock.attachments = [ATTACHMENT];
    let composerCleared = false;
    render(
      <ChatInput
        onSend={vi.fn(() => true)}
        onCancel={vi.fn()}
        onNewChat={vi.fn()}
        isStreaming={false}
        initialText=""
        onComposerCleared={() => {
          composerCleared = true;
        }}
        agentId="agent-1"
        threadId="thread-1"
      />,
    );
    const textarea = screen.getByRole("textbox") as HTMLTextAreaElement;

    fireEvent.change(textarea, { target: { value: "发送成功" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    expect(textarea.value).toBe("");
    expect(writeInputDraft).toHaveBeenCalledWith("agent-1", "thread-1", "");
    expect(attachmentsMock.clearAttachments).toHaveBeenCalledTimes(1);
    expect(composerCleared).toBe(true);
  });

  it("lets the user retry after a rejected send without retyping", () => {
    const onSend = vi.fn(() => false);
    const textarea = renderInput(onSend);

    fireEvent.change(textarea, { target: { value: "草稿" } });
    fireEvent.click(screen.getByRole("button", { name: "send" }));
    fireEvent.click(screen.getByRole("button", { name: "send" }));

    expect(onSend).toHaveBeenCalledTimes(2);
    expect(textarea.value).toBe("草稿");
  });
});
