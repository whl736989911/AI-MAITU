import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import type { OctopUser } from "../../../api/modules/auth";

const { listBases, getAcl } = vi.hoisted(() => ({
  listBases: vi.fn(),
  getAcl: vi.fn(),
}));

/** ``null`` — AuthGuard's ``/auth/me`` has not landed, which is the no-provider case. */
const held = vi.hoisted(() => ({ user: null as OctopUser | null }));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => held.user,
}));

vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue([]),
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
  message: {
    error: vi.fn(),
    success: vi.fn(),
    warning: vi.fn(),
    info: vi.fn(),
  },
}));

import SharingResourcesPanel from "./SharingResourcesPanel";

describe("<SharingResourcesPanel />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    held.user = null;
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

  it("offers only the catalogs this account may list", async () => {
    // ``users`` alone: the knowledge-base list is gated on the knowledge page
    // keys and ``/connector-instances`` on ``connectors``, so both tabs could
    // only have reported a refusal.
    held.user = user(["users"]);
    render(<SharingResourcesPanel />);

    expect(
      await screen.findByText("sharing.resourceType.agent"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("sharing.resourceType.knowledge_base"),
    ).toBeNull();
    expect(screen.queryByText("sharing.resourceType.connector")).toBeNull();
    expect(listBases).not.toHaveBeenCalled();
  });

  it("offers the knowledge catalog to an account that holds its key", async () => {
    held.user = user(["users", "knowledge_settings"]);
    render(<SharingResourcesPanel />);

    await waitFor(() => expect(listBases).toHaveBeenCalledOnce());
    expect(
      screen.getByText("sharing.resourceType.knowledge_base"),
    ).toBeInTheDocument();
  });
});

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
