import { render, screen } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";
import type { MBTIType } from "../../../../api/types";

const mocks = vi.hoisted(() => ({
  listTypes: vi.fn(),
  current: vi.fn(),
  apply: vi.fn(),
}));

const translate = vi.hoisted(
  () => (key: string, options?: Record<string, unknown>) => {
    if (key === "personalization.mbti.listSummary") {
      return `当前有（${options?.total}）个人格，已选中「${options?.selected}」`;
    }
    if (key === "personalization.mbti.listSummaryUnset") {
      return `当前有（${options?.total}）个人格，尚未选择人格`;
    }
    return key;
  },
);
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: translate, i18n: { language: "zh" } }),
}));
vi.mock("react-router-dom", () => ({ useNavigate: () => vi.fn() }));
vi.mock("../../../../context/AgentContext", () => ({
  useAgent: () => ({ activeAgentId: "agent-1", refresh: vi.fn() }),
}));
vi.mock("../../../../api", () => ({
  default: {
    listMBTITypes: mocks.listTypes,
    getCurrentMBTI: mocks.current,
    applyMBTIType: mocks.apply,
  },
}));
vi.mock("./MBTITest", () => ({ default: () => null }));

import MBTISelector from "./MBTISelector";

const INFJ: MBTIType = {
  code: "INFJ",
  name_zh: "提倡者",
  name_en: "Advocate",
  nickname_zh: "提倡者",
  summary_zh: "理想主义者",
  summary_en: "Idealist",
  descriptors_zh: "安静",
  descriptors_en: "Quiet",
  dimensions: { ei: ["I", 70], sn: ["N", 60], tf: ["F", 55], jp: ["J", 50] },
  behavior: {
    answer_style: "",
    casual_chat: "",
    conflict: "",
    creativity: "",
    emotion: "",
    planning: "",
    answer_style_zh: "",
    casual_chat_zh: "",
    conflict_zh: "",
    creativity_zh: "",
    emotion_zh: "",
    planning_zh: "",
  },
  color: "#888",
  symbol: "S",
};

describe("MBTISelector current-personality summary", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.listTypes.mockResolvedValue([INFJ]);
  });

  it("shows an explicit unset state rather than empty selection quotes", async () => {
    mocks.current.mockResolvedValue({ code: "", configured: false });
    render(<MBTISelector agentId="agent-1" />);

    const summary = await screen.findByText("当前有（1）个人格，尚未选择人格");
    expect(summary.textContent).not.toContain("「」");
  });

  it("keeps the selected code and localized name for configured agents", async () => {
    mocks.current.mockResolvedValue({ code: "INFJ", configured: true });
    render(<MBTISelector agentId="agent-1" />);

    expect(
      await screen.findByText("当前有（1）个人格，已选中「INFJ 提倡者」"),
    ).toBeInTheDocument();
  });
});
