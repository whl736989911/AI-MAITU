/**
 * The composer loads the caller's connected accounts, and ``/connector-instances``
 * is ``connectors``-gated on the server. An account without the key has no
 * connected accounts to list, so the probe must not be sent at all — it used to
 * fire on every chat mount and come back 403.
 *
 * The gate is the permission, not a swallowed error: the same request still goes
 * out for an account that holds the key.
 */

import { act, renderHook } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { OctopUser } from "../../../api/modules/auth";
import type { ConnectorInstance } from "../../../api/modules/connectors";
import { useChatComposerResources } from "./useChatComposerResources";

/**
 * Endpoints the hook mounts alongside the one under test never settle, so their
 * callbacks cannot update state and the assertions stay deterministic — no
 * wall-clock wait, no un-acted update.
 */
function neverSettles<T>(): Promise<T> {
  return Promise.withResolvers<T>().promise;
}

const listInstances = vi.fn(async (): Promise<ConnectorInstance[]> => []);
const getKnowledgeCapability = vi.fn(() => neverSettles<never>());
const listKnowledgeBases = vi.fn(() => neverSettles<never[]>());

const held = vi.hoisted(() => ({ user: null as OctopUser | null }));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => held.user,
}));

vi.mock("../../../context/AgentContext", () => ({
  useAgent: () => ({
    agents: [],
    activeAgent: null,
    activeAgentId: null,
    loading: false,
  }),
}));

vi.mock("../../../api/modules/connectors", () => ({
  connectorsApi: { listInstances: () => listInstances() },
}));

vi.mock("../../../api/modules/provider", () => ({
  providerApi: { listResolvedModels: neverSettles },
}));

vi.mock("../../../api/modules/preferences", () => ({
  preferencesApi: { get: neverSettles },
}));

vi.mock("../../../api/modules/octopThreads", () => ({
  octopThreadsApi: { patch: neverSettles },
}));

vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: {
    getCapability: () => getKnowledgeCapability(),
    list: () => listKnowledgeBases(),
  },
}));

vi.mock("../../../api/request", () => ({
  request: neverSettles,
}));

vi.mock("./useSessions", () => ({
  isPendingThread: () => false,
}));

function user(permissions: string[]): OctopUser {
  return {
    id: 7,
    username: "tuser",
    role: "user",
    display_name: null,
    locale: "zh",
    permissions,
  };
}

describe("useChatComposerResources without the connectors permission", () => {
  beforeEach(() => {
    listInstances.mockClear();
    held.user = user([]);
  });

  it("never asks the server for connected accounts", () => {
    renderHook(() => useChatComposerResources("a1", null));

    expect(listInstances).not.toHaveBeenCalled();
  });

  it("still loads them for an account that holds the key", async () => {
    held.user = user(["connectors"]);
    renderHook(() => useChatComposerResources("a1", null));

    expect(listInstances).toHaveBeenCalledTimes(1);
    // Let the returned list land inside the act window instead of after the test.
    const [call] = listInstances.mock.results;
    await act(async () => {
      await call?.value;
    });
  });

  it("drops the picker and selected connector when access is revoked", async () => {
    held.user = user(["connectors"]);
    listInstances.mockResolvedValueOnce([
      {
        instance_id: "instance-1",
        kind: "feishu",
        display_name: "My Feishu",
        status: "active",
        mcp_server_name: "feishu-1",
        has_credentials: true,
        shared: false,
        owner_user_id: 7,
        can_manage: true,
        created_at: 1,
        updated_at: 1,
      },
    ]);
    const { result, rerender } = renderHook(() =>
      useChatComposerResources("a1", null),
    );
    await act(async () => {
      await listInstances.mock.results[0]?.value;
    });
    expect(
      result.current.chatConnectors?.map((item) => item.mcp_server_name),
    ).toEqual(["feishu-1"]);
    act(() => result.current.handleConnectorsChange?.(["feishu-1"]));
    expect(result.current.selectedConnectors).toEqual(["feishu-1"]);

    held.user = user([]);
    rerender();
    expect(result.current.chatConnectors).toBeUndefined();
    expect(result.current.selectedConnectors).toEqual([]);
    expect(result.current.handleConnectorsChange).toBeUndefined();
  });
});

describe("useChatComposerResources without a knowledge-base key", () => {
  beforeEach(() => {
    getKnowledgeCapability.mockClear();
    listKnowledgeBases.mockClear();
    held.user = user([]);
  });

  it("never probes the knowledge-base page endpoints", () => {
    renderHook(() => useChatComposerResources("a1", null));

    expect(getKnowledgeCapability).not.toHaveBeenCalled();
    expect(listKnowledgeBases).not.toHaveBeenCalled();
  });

  it("probes them for the settings key alone", () => {
    // The page — and so the composer's knowledge picker — is opened by either
    // key (``PERM.knowledgeBasesPage``), not by ``knowledge_bases`` alone.
    held.user = user(["knowledge_settings"]);
    renderHook(() => useChatComposerResources("a1", null));

    expect(getKnowledgeCapability).toHaveBeenCalledTimes(1);
  });
});
