import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";
import MinimalAgentSessionNav from "./MinimalAgentSessionNav";
import type { OctopAgent } from "../../../context/AgentContext";

const listMock = vi.fn();
const patchMock = vi.fn();
const renameMock = vi.fn();
const errorToastMock = vi.fn();

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: {
    list: (...args: unknown[]) => listMock(...args),
    patch: (...args: unknown[]) => patchMock(...args),
    rename: (...args: unknown[]) => renameMock(...args),
    delete: vi.fn(),
    create: vi.fn(),
    fork: vi.fn(),
  },
}));

vi.mock("../../../utils/antdMessage", () => ({
  message: {
    error: (...args: unknown[]) => errorToastMock(...args),
    success: vi.fn(),
  },
}));

const ACTIVE_AGENT = "A_ACTIVE";
const OTHER_AGENT = "A_OTHER";

function agent(agentId: string, id: number, name: string): OctopAgent {
  return {
    id,
    agent_id: agentId,
    name,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: null,
    icon_url: null,
    color: null,
    config: {},
  };
}

function threadRow(threadId: string, title: string) {
  return {
    thread_id: threadId,
    title,
    last_active: 1_700_000_000,
    created_at: 1_700_000_000,
    channel_type: "dashboard",
    has_messages: true,
    pinned: false,
  };
}

/** Same shape ``request()`` throws for a rejected PATCH on a stale thread id. */
function threadNotFoundError() {
  return new Error(
    'Request failed: 404 Not Found - {"error":{"code":"NOT_FOUND","message":"thread not found"}}',
  );
}

async function mountNav(
  overrides: Partial<React.ComponentProps<typeof MinimalAgentSessionNav>> = {},
) {
  const props = {
    agents: [agent(ACTIVE_AGENT, 1, "Expert One"), agent(OTHER_AGENT, 2, "Expert Two")],
    activeId: null,
    activeAgentId: ACTIVE_AGENT,
    activeSessions: [],
    onSelect: vi.fn(),
    onAgentSelect: vi.fn(),
    onNewChat: vi.fn(),
    onDeleteActive: vi.fn(),
    onRenameActive: vi.fn().mockResolvedValue(true),
    onPinActive: vi.fn().mockResolvedValue(true),
    onFork: vi.fn(),
    ...overrides,
  };
  render(
    <MemoryRouter>
      <MinimalAgentSessionNav {...props} />
    </MemoryRouter>,
  );
  await screen.findByText("Alpha chat");
  await screen.findByText("Beta chat");
  return props;
}

/** Open the row menu for ``title`` and click the item labelled ``label``. */
async function clickRowMenuItem(title: string, label: string) {
  const row = screen.getByText(title).closest('div[class*="sessionRow"]');
  expect(row).not.toBeNull();
  // ``t("common.more", "More")`` — the i18n test mock resolves to the fallback.
  const more = row?.querySelector('button[aria-label="More"]');
  expect(more).not.toBeNull();
  fireEvent.click(more as HTMLElement);
  const item = await screen.findByText(label);
  fireEvent.click(item);
  return row as HTMLElement;
}

describe("MinimalAgentSessionNav rename / pin against a rejected server write", () => {
  beforeEach(() => {
    localStorage.clear();
    listMock.mockReset();
    patchMock.mockReset();
    renameMock.mockReset();
    errorToastMock.mockReset();
    listMock.mockImplementation((agentId: string) =>
      Promise.resolve(
        agentId === ACTIVE_AGENT
          ? [threadRow("thr_active", "Alpha chat")]
          : [threadRow("thr_other", "Beta chat")],
      ),
    );
  });

  it("keeps a foreign expert's rejected rename out of its preview list", async () => {
    renameMock.mockRejectedValue(threadNotFoundError());
    await mountNav();

    await clickRowMenuItem("Beta chat", "common.rename");
    const input = screen.getByDisplayValue("Beta chat");
    fireEvent.change(input, { target: { value: "Renamed elsewhere" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(errorToastMock).toHaveBeenCalledTimes(1);
    });
    expect(renameMock).toHaveBeenCalledWith(OTHER_AGENT, "thr_other", "Renamed elsewhere");
    expect(screen.getByText("Beta chat")).toBeInTheDocument();
    expect(screen.queryByText("Renamed elsewhere")).not.toBeInTheDocument();
  });

  it("keeps a foreign expert's rejected pin out of its preview list", async () => {
    patchMock.mockRejectedValue(threadNotFoundError());
    await mountNav();

    await clickRowMenuItem("Beta chat", "置顶");

    await waitFor(() => {
      expect(errorToastMock).toHaveBeenCalledTimes(1);
    });
    expect(patchMock).toHaveBeenCalledWith(OTHER_AGENT, "thr_other", { pinned: true });
    // The row's pin badge is title={t("chat.unpin")}; the i18n test mock
    // resolves that key to itself, so match on the key.
    expect(document.querySelector('span[title="chat.unpin"]')).toBeNull();
  });

  it("keeps the active expert's rejected rename out of the preview list", async () => {
    const onRenameActive = vi.fn().mockResolvedValue(false);
    await mountNav({ onRenameActive });

    await clickRowMenuItem("Alpha chat", "common.rename");
    const input = screen.getByDisplayValue("Alpha chat");
    fireEvent.change(input, { target: { value: "Renamed locally" } });
    fireEvent.keyDown(input, { key: "Enter" });

    await waitFor(() => {
      expect(onRenameActive).toHaveBeenCalledWith("thr_active", "Renamed locally");
    });
    // The active path owns its own toast (host handler) and never writes the
    // foreign-agent endpoint; the preview must not show an unstored title.
    expect(renameMock).not.toHaveBeenCalled();
    expect(screen.getByText("Alpha chat")).toBeInTheDocument();
    expect(screen.queryByText("Renamed locally")).not.toBeInTheDocument();
  });

  it("keeps the active expert's rejected pin out of the preview list", async () => {
    const onPinActive = vi.fn().mockResolvedValue(false);
    await mountNav({ onPinActive });

    await clickRowMenuItem("Alpha chat", "置顶");

    await waitFor(() => {
      expect(onPinActive).toHaveBeenCalledWith("thr_active", true);
    });
    expect(patchMock).not.toHaveBeenCalled();
    // The row's pin badge is title={t("chat.unpin")}; the i18n test mock
    // resolves that key to itself, so match on the key.
    expect(document.querySelector('span[title="chat.unpin"]')).toBeNull();
  });
});
