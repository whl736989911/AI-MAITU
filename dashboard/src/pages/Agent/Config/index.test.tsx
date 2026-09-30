import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import { request } from "../../../api/request";
import { useAgent } from "../../../context/AgentContext";
import AgentConfigPage from "./index";

vi.mock("../../../api/request", () => ({ request: vi.fn() }));
vi.mock("../../../context/AgentContext", () => ({ useAgent: vi.fn() }));

describe("AgentConfigPage", () => {
  const refresh = vi.fn();

  beforeEach(() => {
    vi.clearAllMocks();
    refresh.mockResolvedValue(undefined);
    vi.mocked(request).mockResolvedValue({} as never);
    vi.mocked(useAgent).mockReturnValue({
      activeAgentId: "agent-1",
      activeAgent: {
        max_iters: 20,
        max_input_length: 32000,
        temperature: 0.2,
        top_p: 0.9,
        max_tokens: 2048,
      },
      refresh,
    } as never);
  });

  it("refreshes the active agent after runtime settings are saved", async () => {
    render(<AgentConfigPage />);
    const save = await screen.findByRole("button", { name: "common.save" });
    fireEvent.click(save);

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/agents/agent-1",
        expect.objectContaining({ method: "PATCH" }),
      );
      expect(refresh).toHaveBeenCalledWith({ silent: true, force: true });
    });
    expect(request.mock.invocationCallOrder[0]).toBeLessThan(
      refresh.mock.invocationCallOrder[0],
    );
  });

  it("does not refresh the active agent when the save fails", async () => {
    vi.mocked(request).mockRejectedValueOnce(new Error("save failed"));
    render(<AgentConfigPage />);
    fireEvent.click(await screen.findByRole("button", { name: "common.save" }));

    await waitFor(() => {
      expect(request).toHaveBeenCalledWith(
        "/agents/agent-1",
        expect.objectContaining({ method: "PATCH" }),
      );
    });
    expect(refresh).not.toHaveBeenCalled();
  });
});
