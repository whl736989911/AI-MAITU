/**
 * Token Usage draws its two per-kind views — 「按专家」 and 「按功能」 — one per
 * ``kind``, and only for the kinds this deployment holds at all. A deployment
 * with no features has no feature breakdown to offer, and drawing the option
 * anyway would promise a view that is empty by construction.
 *
 * The question is asked of the agents the deployment holds rather than of the
 * rows currently in scope: the date range and the user filter move, and a view
 * that came and went with them would be a different page on every filter. The
 * granularities the page actually asks the server for are asserted here too —
 * the option and the request are the same decision (``by_expert`` / ``by_feature``
 * in ``infra/db/repos/usage.py``).
 *
 * The assertions are about the copy a Chinese-locale reader sees, so ``t`` is
 * answered from the shipped ``zh.json``, as the Admin → Users agent-column test
 * does for the same rule.
 */

import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type * as ReactI18next from "react-i18next";
import type * as RequestApi from "../../../api/request";
import type { OctopAgent } from "../../../context/AgentContext";
import zh from "../../../locales/zh.json";

const { requestMock } = vi.hoisted(() => ({ requestMock: vi.fn() }));

vi.mock("../../../api/request", async (importOriginal) => {
  const actual = await importOriginal<typeof RequestApi>();
  return { ...actual, request: requestMock, requestBlob: vi.fn() };
});

vi.mock("@/utils/antdMessage", () => ({
  message: { success: vi.fn(), error: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

vi.mock("../../../hooks/useUserRole", () => ({ useUserRole: () => "user" }));

/** A caller who may read the deployment's own list (``agents.py``'s ``users``). */
const CALLER = { id: 5, username: "ops", role: "user", permissions: ["users"] };
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => CALLER,
}));

vi.mock("react-i18next", async (importOriginal) => {
  const actual = await importOriginal<typeof ReactI18next>();

  const lookup = (key: string): string | undefined => {
    let node: unknown = zh;
    for (const part of key.split(".")) {
      if (!node || typeof node !== "object") return undefined;
      node = (node as Record<string, unknown>)[part];
    }
    return typeof node === "string" ? node : undefined;
  };

  const api = {
    t: (key: string): string => lookup(key) ?? key,
    i18n: { language: "zh", changeLanguage: () => Promise.resolve() },
  };
  return { ...actual, useTranslation: () => api };
});

import TokenUsagePage from "./index";

function agent(agentId: string, kind: string): OctopAgent {
  return {
    id: 1,
    agent_id: agentId,
    kind,
    user_id: 5,
    name: agentId,
    description: null,
    persona_mbti: null,
    default_model: null,
    system_prompt: null,
    template_name: null,
    state: "running",
    last_error: null,
    icon: null,
    icon_name: "sparkles",
    icon_url: null,
    color: null,
    config: {},
    is_shared: false,
    is_owner: true,
  };
}

/** An empty roll-up: this test is about which views exist, not what they hold. */
function emptySummary(granularity: string): unknown {
  return {
    window: "last_30d",
    granularity,
    range_start: 0,
    range_end: 0,
    input_tokens: 0,
    uncached_input_tokens: 0,
    cache_read_tokens: 0,
    cache_write_tokens: 0,
    output_tokens: 0,
    reasoning_tokens: 0,
    total_tokens: 0,
    model_calls: 0,
    turns: 0,
    avg_per_turn: 0,
    cache_hit_percent: 0,
    buckets: [],
  };
}

/** What the deployment's own list holds; which of the two each test varies. */
let deployment: OctopAgent[] = [];

beforeEach(() => {
  requestMock.mockReset();
  requestMock.mockImplementation(async (url: string): Promise<unknown> => {
    if (url === "/agents?scope=all") return deployment;
    // The deployment's kinds, as ``/agents/kinds`` answers them — the whole
    // table, enabled rows, no ``users`` permission to hold. The presence hook is
    // moving to this reading; both are answered so this test holds on either
    // side of that move, and either way the kinds are drawn from the deployment
    // rather than from anything the page's filters are showing.
    if (url === "/agents/kinds") {
      return {
        kinds: ["agent", "feature"].filter((kind) =>
          deployment.some((a) => a.kind === kind),
        ),
      };
    }
    if (url.startsWith("/usage/summary")) {
      const params = new URLSearchParams(url.slice(url.indexOf("?") + 1));
      return emptySummary(params.get("granularity") ?? "");
    }
    throw new Error(`unexpected request: ${url}`);
  });
});

/** The granularities the page asked the server for. */
function askedGranularities(): Set<string> {
  return new Set(
    requestMock.mock.calls
      .map(([url]) => String(url))
      .filter((url) => url.startsWith("/usage/summary"))
      .map(
        (url) =>
          new URLSearchParams(url.slice(url.indexOf("?") + 1)).get(
            "granularity",
          ) ?? "",
      ),
  );
}

describe("Token Usage's per-kind views", () => {
  it("offers only the experts' view when the deployment has no features", async () => {
    deployment = [agent("assistant", "agent"), agent("reviewer", "agent")];
    render(<TokenUsagePage />);

    expect(await screen.findByText("按专家")).toBeInTheDocument();
    // The experts' donut is drawn, and the filter names the one kind there is.
    expect(await screen.findByText("专家分布")).toBeInTheDocument();
    expect(await screen.findByText("全部专家")).toBeInTheDocument();
    expect(screen.queryByText("按功能")).toBeNull();
    expect(screen.queryByText("功能分布")).toBeNull();
    expect(screen.queryByText("全部功能")).toBeNull();
    expect(screen.queryByText("全部专家与功能")).toBeNull();
    // And nothing was asked for a breakdown of a kind that does not exist.
    expect(askedGranularities()).not.toContain("by_feature");
  });

  it("offers only the features' view when the deployment has no experts", async () => {
    deployment = [agent("weekly-report", "feature")];
    render(<TokenUsagePage />);

    expect(await screen.findByText("按功能")).toBeInTheDocument();
    expect(await screen.findByText("功能分布")).toBeInTheDocument();
    expect(await screen.findByText("全部功能")).toBeInTheDocument();
    expect(screen.queryByText("按专家")).toBeNull();
    expect(screen.queryByText("专家分布")).toBeNull();
    expect(screen.queryByText("全部专家")).toBeNull();
    expect(screen.queryByText("全部专家与功能")).toBeNull();
    expect(askedGranularities()).not.toContain("by_expert");
  });

  it("offers both views, one donut each, when the deployment has both kinds", async () => {
    deployment = [agent("assistant", "agent"), agent("weekly-report", "feature")];
    render(<TokenUsagePage />);

    expect(await screen.findByText("按专家")).toBeInTheDocument();
    expect(await screen.findByText("按功能")).toBeInTheDocument();
    expect(await screen.findByText("专家分布")).toBeInTheDocument();
    expect(await screen.findByText("功能分布")).toBeInTheDocument();
    // The one label that names the whole list, which is now both kinds.
    expect(await screen.findByText("全部专家与功能")).toBeInTheDocument();
    // Each view is its own grouping, narrowed to its own kind by the server.
    expect(askedGranularities()).toEqual(
      new Set(["by_expert", "by_feature", "by_model", "by_day"]),
    );
  });

  it("offers neither view when the deployment holds neither kind", async () => {
    deployment = [];
    render(<TokenUsagePage />);

    // The kind-free views are the ones left, and they are still drawn.
    expect(await screen.findByText("按天")).toBeInTheDocument();
    expect(await screen.findByText("按模型")).toBeInTheDocument();
    expect(screen.queryByText("按专家")).toBeNull();
    expect(screen.queryByText("按功能")).toBeNull();
    expect(screen.queryByText("专家分布")).toBeNull();
    expect(screen.queryByText("功能分布")).toBeNull();
  });
});
