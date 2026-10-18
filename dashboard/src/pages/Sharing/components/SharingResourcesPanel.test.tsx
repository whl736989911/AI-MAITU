import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";
import type { OctopUser } from "../../../api/modules/auth";

const { getAcl } = vi.hoisted(() => ({ getAcl: vi.fn() }));
const held = vi.hoisted(() => ({ user: null as OctopUser | null }));

vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => held.user,
}));

vi.mock("../../../api/request", () => ({
  request: vi.fn().mockResolvedValue([]),
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
  });

  it("keeps knowledge-base ACL configuration out of shared settings", async () => {
    render(<SharingResourcesPanel />);

    expect(await screen.findByText("sharing.resourceType.agent")).toBeInTheDocument();
    expect(screen.queryByText("sharing.resourceType.knowledge_base")).toBeNull();
    expect(screen.queryByText("sharing.resourceType.knowledge_document")).toBeNull();
  });

  it("offers only catalogs this account may list", async () => {
    held.user = user(["users"]);
    render(<SharingResourcesPanel />);

    expect(await screen.findByText("sharing.resourceType.agent")).toBeInTheDocument();
    expect(screen.queryByText("sharing.resourceType.knowledge_base")).toBeNull();
    expect(screen.queryByText("sharing.resourceType.knowledge_document")).toBeNull();
    expect(screen.queryByText("sharing.resourceType.connector")).toBeNull();
  });

  it("keeps the connector settings tab for connector managers", async () => {
    held.user = user(["connectors"]);
    render(<SharingResourcesPanel />);

    expect(await screen.findByText("sharing.resourceType.connector")).toBeInTheDocument();
    expect(screen.queryByText("sharing.resourceType.knowledge_base")).toBeNull();
    expect(screen.queryByText("sharing.resourceType.knowledge_document")).toBeNull();
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
