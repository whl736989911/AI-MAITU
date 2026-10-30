import "@testing-library/jest-dom/vitest";
import { describe, it, expect, vi, beforeEach } from "vitest";
import { render, screen, waitFor, fireEvent } from "@testing-library/react";

import {
  makeAtom,
  makeEntity,
  listAtomsResp,
  statsCountsFixture,
  terminalAtomResp,
  terminalEntityResp,
} from "../../../test/memoryFixtures";

vi.mock("../../../api/modules/memoryDashboard", () => ({
  memoryDashboardApi: {
    terminalAboutMe: vi.fn(),
    terminalCurrentFocus: vi.fn(),
    terminalThingsYouToldMe: vi.fn(),
    terminalEntities: vi.fn(),
    statsCounts: vi.fn(),
    listAtoms: vi.fn(),
  },
}));

import { memoryDashboardApi } from "../../../api/modules/memoryDashboard";
import ProfileOverview from "./ProfileOverview";

const api = vi.mocked(memoryDashboardApi, true);

beforeEach(() => {
  vi.clearAllMocks();
});

function stubProfile() {
  api.terminalAboutMe.mockResolvedValue(
    terminalAtomResp([makeAtom({ id: "a1", assertion: "你只喝美式咖啡。" })]),
  );
  api.terminalCurrentFocus.mockResolvedValue(
    terminalAtomResp([
      makeAtom({
        id: "a2",
        assertion: "你正在写记忆模块的设计文档。",
        kind: "Task",
      }),
    ]),
  );
  api.terminalThingsYouToldMe.mockResolvedValue(
    terminalAtomResp([
      makeAtom({ id: "a3", assertion: "你出生于1995年。", kind: "Fact" }),
    ]),
  );
  api.terminalEntities.mockResolvedValue(
    terminalEntityResp([
      makeEntity({
        id: "e1",
        canonical_name: "Bo5heng项目",
        entity_type: "Project",
      }),
    ]),
  );
  api.statsCounts.mockResolvedValue(
    statsCountsFixture({ atoms: 127, entities: 18 }),
  );
  api.listAtoms.mockResolvedValue(
    listAtomsResp([
      makeAtom({ id: "a1", confidence: "high" }),
      makeAtom({ id: "a2", confidence: "medium" }),
    ]),
  );
}

describe("<ProfileOverview />", () => {
  it("renders returned data in the independent profile sections", async () => {
    stubProfile();

    render(<ProfileOverview agentId="ZYWZTD" />);

    expect(await screen.findByText(/只喝美式咖啡/)).toBeInTheDocument();
    expect(screen.getByText(/记忆模块的设计文档/)).toBeInTheDocument();
    expect(screen.getByText(/出生于1995年/)).toBeInTheDocument();
    expect(screen.getByText("Bo5heng项目")).toBeInTheDocument();
  });

  it("keeps successful sections when another endpoint fails", async () => {
    api.terminalAboutMe.mockRejectedValue(new Error("about me boom"));
    api.terminalCurrentFocus.mockResolvedValue(
      terminalAtomResp([
        makeAtom({
          id: "focus-success",
          kind: "Task",
          assertion: "Review the synthetic release.",
        }),
      ]),
    );
    api.terminalThingsYouToldMe.mockResolvedValue(terminalAtomResp([]));
    api.terminalEntities.mockResolvedValue(
      terminalEntityResp([
        makeEntity({
          id: "project-success",
          canonical_name: "Synthetic project",
        }),
      ]),
    );
    api.statsCounts.mockResolvedValue(statsCountsFixture());
    api.listAtoms.mockResolvedValue(listAtomsResp([]));

    render(<ProfileOverview agentId="ZYWZTD" />);

    expect(
      await screen.findByText("Review the synthetic release."),
    ).toBeInTheDocument();
    expect(screen.getByText("Synthetic project")).toBeInTheDocument();
  });

  it("caps a section at 6 rows and shows 查看全部 that calls onViewAll", async () => {
    const many = Array.from({ length: 8 }, (_, i) =>
      makeAtom({ id: `m${i}`, assertion: `关于你条目${i + 1}` }),
    );
    api.terminalAboutMe.mockResolvedValue(terminalAtomResp(many));
    api.terminalCurrentFocus.mockResolvedValue(terminalAtomResp([]));
    api.terminalThingsYouToldMe.mockResolvedValue(terminalAtomResp([]));
    api.terminalEntities.mockResolvedValue(terminalEntityResp([]));
    api.statsCounts.mockResolvedValue(statsCountsFixture());
    api.listAtoms.mockResolvedValue(listAtomsResp([]));

    const onViewAll = vi.fn();
    render(<ProfileOverview agentId="ZYWZTD" onViewAll={onViewAll} />);

    await waitFor(() => {
      expect(screen.getByText("关于你条目1")).toBeInTheDocument();
    });
    // Visible rows are capped at 6: the 6th row exists, the 7th does not.
    expect(screen.getByText("关于你条目6")).toBeInTheDocument();
    expect(screen.queryByText("关于你条目7")).not.toBeInTheDocument();

    const viewAll = screen.getByRole("button", { name: /查看全部/ });
    fireEvent.click(viewAll);
    expect(onViewAll).toHaveBeenCalledTimes(1);
  });

  it("does not call endpoints when agentId is empty", () => {
    render(<ProfileOverview agentId="" />);
    expect(api.terminalAboutMe).not.toHaveBeenCalled();
    expect(api.statsCounts).not.toHaveBeenCalled();
  });

  it("replaces displayed profile data after refresh", async () => {
    stubProfile();
    api.terminalCurrentFocus
      .mockResolvedValueOnce(
        terminalAtomResp([
          makeAtom({ id: "focus-old", assertion: "Before refresh." }),
        ]),
      )
      .mockResolvedValueOnce(
        terminalAtomResp([
          makeAtom({ id: "focus-new", assertion: "After refresh." }),
        ]),
      );

    render(<ProfileOverview agentId="ZYWZTD" />);

    expect(await screen.findByText("Before refresh.")).toBeInTheDocument();

    const refreshBtn = screen.getByRole("button");
    fireEvent.click(refreshBtn);

    expect(await screen.findByText("After refresh.")).toBeInTheDocument();
    expect(screen.queryByText("Before refresh.")).not.toBeInTheDocument();
  });
});
