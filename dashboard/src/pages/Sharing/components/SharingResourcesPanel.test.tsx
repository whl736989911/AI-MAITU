import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { listBases, getAcl } = vi.hoisted(() => ({
  listBases: vi.fn(),
  getAcl: vi.fn(),
}));

vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: { list: listBases },
}));

vi.mock("../../../api/modules/sharing", () => ({
  sharingApi: {
    getAcl,
    changeAcl: vi.fn(),
    listOrgUnits: vi.fn().mockResolvedValue([]),
    listChanges: vi.fn(),
    approveChange: vi.fn(),
    rejectChange: vi.fn(),
    rollbackChange: vi.fn(),
  },
}));

vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import SharingResourcesPanel from "./SharingResourcesPanel";

describe("<SharingResourcesPanel />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listBases.mockResolvedValue([
      {
        id: "kb-1",
        knowledge_base_id: "kb-1",
        owner_user_id: 3,
        owner_username: "anna",
        name: "Handbook",
        description: "",
        default_open: false,
        shared: false,
        icon_name: "book",
        embedding_model: "m",
        embedding_dim: 384,
        doc_count: 2,
        max_documents: 100,
        created_at: 1,
        updated_at: 1,
      },
    ]);
    getAcl.mockResolvedValue({
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: null,
    });
  });

  it("lists knowledge bases first and opens their sharing settings", async () => {
    render(<SharingResourcesPanel />);

    await waitFor(() => expect(listBases).toHaveBeenCalledOnce());
    expect(await screen.findByText("Handbook")).toBeInTheDocument();

    await userEvent.click(
      screen.getByRole("button", { name: "sharing.settings.open" }),
    );

    await waitFor(() =>
      expect(getAcl).toHaveBeenCalledWith("knowledge_base", "kb-1"),
    );
    expect(
      await screen.findByText("sharing.settings.notShared"),
    ).toBeInTheDocument();
  });
});
