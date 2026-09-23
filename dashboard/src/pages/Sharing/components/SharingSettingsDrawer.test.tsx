import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { getAcl, changeAcl, listOrgUnits } = vi.hoisted(() => ({
  getAcl: vi.fn(),
  changeAcl: vi.fn(),
  listOrgUnits: vi.fn(),
}));

vi.mock("../../../api/modules/sharing", () => ({
  sharingApi: {
    getAcl,
    changeAcl,
    listOrgUnits,
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

import SharingSettingsDrawer from "./SharingSettingsDrawer";

function renderDrawer() {
  return render(
    <SharingSettingsDrawer
      open
      onClose={() => undefined}
      resourceType="knowledge_base"
      resourceId="kb-1"
      resourceName="Handbook"
    />,
  );
}

describe("<SharingSettingsDrawer />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    listOrgUnits.mockResolvedValue([]);
  });

  it("shows the ACL in force for the resource", async () => {
    getAcl.mockResolvedValue({
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: {
        resource_type: "knowledge_base",
        resource_id: "kb-1",
        owner_user_id: 3,
        visibility: "unit",
        unit_key: "engineering",
        version: 2,
        grants: [{ grantee_type: "role", grantee_id: "unit_admin" }],
      },
    });

    renderDrawer();

    await waitFor(() =>
      expect(getAcl).toHaveBeenCalledWith("knowledge_base", "kb-1"),
    );
    // The state in force: the snapshotted unit and the entry version. The
    // visibility labels themselves also render as radio options, so they are
    // asserted through the unit key that only the current state shows.
    expect(await screen.findByText("engineering")).toBeInTheDocument();
    expect(screen.getByText("sharing.settings.version")).toBeInTheDocument();
  });

  it("reads a parked org-wide change as pending, not as applied (it answers 200)", async () => {
    getAcl.mockResolvedValue({
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: null,
    });
    changeAcl.mockResolvedValue({
      change_id: "01H",
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      status: "pending_approval",
      impact_scope: "org",
      applied: false,
      entry: null,
    });

    renderDrawer();
    await waitFor(() => expect(getAcl).toHaveBeenCalledOnce());

    await userEvent.click(screen.getByText("sharing.visibility.public"));
    await userEvent.click(
      screen.getByRole("button", { name: "sharing.settings.submit" }),
    );

    await waitFor(() => expect(changeAcl).toHaveBeenCalledOnce());
    expect(changeAcl).toHaveBeenCalledWith("knowledge_base", "kb-1", {
      visibility: "public",
      unit_key: null,
      // The drawer always submits the level; untouched state keeps the default.
      permission: "read",
      grants: [],
      reason: null,
    });
    expect(
      await screen.findByText("sharing.settings.result.pending"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("sharing.settings.result.applied"),
    ).not.toBeInTheDocument();
  });

  it("reads an applied change as in effect", async () => {
    getAcl.mockResolvedValue({
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: null,
    });
    changeAcl.mockResolvedValue({
      change_id: "01J",
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      status: "applied",
      impact_scope: "unit",
      applied: true,
      entry: null,
    });

    renderDrawer();
    await waitFor(() => expect(getAcl).toHaveBeenCalledOnce());

    await userEvent.click(screen.getByText("sharing.visibility.unit"));
    await userEvent.click(
      screen.getByRole("button", { name: "sharing.settings.submit" }),
    );

    expect(
      await screen.findByText("sharing.settings.result.applied"),
    ).toBeInTheDocument();
    expect(
      screen.queryByText("sharing.settings.result.pending"),
    ).not.toBeInTheDocument();
  });

  it("warns that the whole organization is reached before the change is submitted", async () => {
    getAcl.mockResolvedValue({
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: null,
    });

    renderDrawer();
    await waitFor(() => expect(getAcl).toHaveBeenCalledOnce());
    expect(
      screen.queryByText("sharing.settings.orgNotice"),
    ).not.toBeInTheDocument();

    await userEvent.click(screen.getByText("sharing.visibility.public"));

    expect(
      await screen.findByText("sharing.settings.orgNotice"),
    ).toBeInTheDocument();
    expect(changeAcl).not.toHaveBeenCalled();
  });
});
