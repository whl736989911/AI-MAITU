import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("../../../api/modules/connectors", () => ({
  connectorsApi: { listInstances: vi.fn().mockResolvedValue([]) },
}));
vi.mock("../../../api/modules/provider", () => ({
  providerApi: { listResolvedModels: vi.fn().mockResolvedValue([]) },
}));
vi.mock("../../../api/modules/preferences", () => ({
  preferencesApi: { get: vi.fn().mockResolvedValue(null) },
}));
vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { patch: vi.fn().mockResolvedValue({}) },
}));
vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue(null),
}));
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    getCapability: vi.fn().mockResolvedValue({ usable: false }),
    list: vi.fn().mockResolvedValue([]),
  },
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => ({
    id: 7,
    permissions: ["connectors", "knowledge_bases"],
  }),
}));
const { agentState } = vi.hoisted(() => ({
  agentState: {
    agents: [{ agent_id: "expertA", default_model: null }] as Array<
      Record<string, unknown>
    >,
  },
}));
vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({ agents: agentState.agents }),
}));

import { providerApi } from "../../../api/modules/provider";
import { preferencesApi } from "../../../api/modules/preferences";
import { octopThreadsApi } from "../../../api/modules/octopThreads";
import type { ResolvedModel } from "../../../api/types";
import { useChatComposerResources } from "./useChatComposerResources";

const model = (name: string): ResolvedModel => ({
  provider_id: 1,
  provider_name: "prov",
  provider_kind: "openai",
  model: name,
  name,
});

beforeEach(() => {
  vi.clearAllMocks();
  agentState.agents = [{ agent_id: "expertA", default_model: null }];
  vi.mocked(providerApi.listResolvedModels).mockResolvedValue([
    model("available"),
  ]);
  vi.mocked(preferencesApi.get).mockResolvedValue(null as never);
});

describe("useChatComposerResources model catalog races", () => {
  it("persists Auto when a successful refresh removes the selected thread model", async () => {
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", "thread-existing", "prov/removed"),
    );
    await waitFor(() => expect(result.current.modelsReady).toBe(true));
    await waitFor(() => expect(result.current.selectedModel).toBeNull());
    expect(octopThreadsApi.patch).toHaveBeenCalledWith(
      "expertA",
      "thread-existing",
      { model_ref: null, reasoning_mode: "auto", reasoning_effort: null },
    );
  });

  it("keeps the last choice when the catalog request fails", async () => {
    vi.mocked(providerApi.listResolvedModels).mockRejectedValue(
      new Error("offline"),
    );
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", "thread-existing", "prov/removed"),
    );
    await waitFor(() =>
      expect(providerApi.listResolvedModels).toHaveBeenCalled(),
    );
    expect(result.current.modelsReady).toBe(false);
    expect(result.current.selectedModel).toBe("prov/removed");
    expect(octopThreadsApi.patch).not.toHaveBeenCalled();
  });

  it("ignores an older catalog response after a newer focus refresh", async () => {
    let resolveOlder!: (models: ResolvedModel[]) => void;
    vi.mocked(providerApi.listResolvedModels).mockReturnValueOnce(
      new Promise((resolve) => {
        resolveOlder = resolve;
      }),
    );
    const { result } = renderHook(() =>
      useChatComposerResources("expertA", "thread-existing", "prov/removed"),
    );
    vi.mocked(providerApi.listResolvedModels).mockResolvedValue([
      model("available"),
    ]);
    act(() => window.dispatchEvent(new Event("focus")));
    await waitFor(() => expect(result.current.modelsReady).toBe(true));
    await act(async () => resolveOlder([model("removed")]));
    expect(result.current.availableModels.map((item) => item.model)).toEqual([
      "available",
    ]);
    expect(result.current.selectedModel).toBeNull();
  });

  it("keeps model catalog ownership with the expert across an agent switch", async () => {
    let resolveExpertA!: (models: ResolvedModel[]) => void;
    vi.mocked(providerApi.listResolvedModels)
      .mockReturnValueOnce(
        new Promise((resolve) => {
          resolveExpertA = resolve;
        }),
      )
      .mockResolvedValueOnce([model("expertB-model")]);
    const { result, rerender } = renderHook(
      ({ agentId }: { agentId: string }) =>
        useChatComposerResources(agentId, "thread-existing"),
      { initialProps: { agentId: "expertA" } },
    );
    agentState.agents = [
      { agent_id: "expertA", default_model: "prov/expertA-model" },
      { agent_id: "expertB", default_model: "prov/expertB-model" },
    ];
    rerender({ agentId: "expertB" });
    await waitFor(() => expect(result.current.modelsReady).toBe(true));
    await act(async () => resolveExpertA([model("expertA-model")]));
    expect(result.current.availableModels.map((item) => item.model)).toEqual([
      "expertB-model",
    ]);
    expect(result.current.selectedModel).toBe("prov/expertB-model");
  });

  it("does not apply a local preferred model to a remote expert with Auto", async () => {
    agentState.agents = [{ agent_id: "bridge:cid:peer", default_model: null }];
    vi.mocked(preferencesApi.get).mockResolvedValue({
      locale: "zh",
      remote_browser_bookmarks: [],
      preferred_model: "prov/available",
      model_reasoning: {},
    } as never);
    const { result } = renderHook(() =>
      useChatComposerResources("bridge:cid:peer", null),
    );
    await waitFor(() => expect(result.current.modelsReady).toBe(true));
    await waitFor(() => expect(preferencesApi.get).toHaveBeenCalled());
    expect(result.current.selectedModel).toBeNull();
  });
});
