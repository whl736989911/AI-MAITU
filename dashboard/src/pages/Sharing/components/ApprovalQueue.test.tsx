import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";

const { listChanges, approveChange, rejectChange, request, currentUser } = vi.hoisted(() => ({
  listChanges: vi.fn(),
  approveChange: vi.fn(),
  rejectChange: vi.fn(),
  request: vi.fn(),
  currentUser: { value: null as null | { id: number; role: string } },
}));

vi.mock("../../../api/modules/sharing", () => ({
  sharingApi: {
    listChanges,
    approveChange,
    rejectChange,
    rollbackChange: vi.fn(),
    getAcl: vi.fn(),
    listOrgUnits: vi.fn(),
  },
}));

vi.mock("../../../api/request", () => ({ request }));
vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../../hooks/useCurrentUser", () => ({
  useCurrentUser: () => currentUser.value,
}));
vi.mock("@/utils/antdMessage", () => ({
  message: { error: vi.fn(), success: vi.fn(), warning: vi.fn(), info: vi.fn() },
}));

import { message } from "@/utils/antdMessage";
import ApprovalQueue from "./ApprovalQueue";

const ORG_CHANGE = {
  change_id: "01H",
  resource_type: "knowledge_base",
  resource_id: "kb-1",
  resource: { name: "Handbook" },
  actor_user_id: 7,
  status: "pending_approval",
  impact_scope: "org",
  applied: false,
  from_version: 1,
  to_version: 2,
  reason: "onboarding for everyone",
  created_at: 1_700_000_000,
  before: {
    resource_type: "knowledge_base",
    resource_id: "kb-1",
    owner_user_id: 7,
    visibility: "private",
    unit_key: null,
    version: 1,
    grants: [],
  },
  after: {
    resource_type: "knowledge_base",
    resource_id: "kb-1",
    owner_user_id: 7,
    visibility: "public",
    unit_key: null,
    version: 2,
    grants: [],
  },
};

describe("<ApprovalQueue />", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    currentUser.value = { id: 1, role: "admin" };
    listChanges.mockResolvedValue({
      status: "pending_approval",
      limit: 50,
      changes: [ORG_CHANGE],
    });
    request.mockResolvedValue([{ id: 7, username: "anna", display_name: "Anna" }]);
  });

  it("opens on pending changes and shows what an admin is judging", async () => {
    render(<ApprovalQueue />);

    await waitFor(() =>
      expect(listChanges).toHaveBeenCalledWith("pending_approval"),
    );
    expect(await screen.findByText("Handbook")).toBeInTheDocument();
    expect(screen.getByText("sharing.resourceType.knowledge_base")).toBeInTheDocument();
    expect(screen.getByText("Anna")).toBeInTheDocument();
    expect(screen.getByText("onboarding for everyone")).toBeInTheDocument();
    // Both sides of the change, so the visibility jump is visible.
    expect(screen.getByText("sharing.queue.before")).toBeInTheDocument();
    expect(screen.getByText("sharing.queue.after")).toBeInTheDocument();
    expect(screen.getByText("sharing.visibility.private")).toBeInTheDocument();
    expect(screen.getByText("sharing.visibility.public")).toBeInTheDocument();
    // The org-wide jump is called out, not left for the reviewer to spot.
    expect(screen.getByText("sharing.queue.orgWarningTitle")).toBeInTheDocument();
    expect(screen.getByText("sharing.impactShort.org")).toBeInTheDocument();
  });

  it("approves a parked change and reloads the queue", async () => {
    approveChange.mockResolvedValue({
      change_id: "01H",
      status: "applied",
      applied: true,
      impact_scope: "org",
      resource_type: "knowledge_base",
      resource_id: "kb-1",
      entry: null,
    });

    render(<ApprovalQueue />);
    await screen.findByText("Handbook");

    await userEvent.click(
      screen.getByRole("button", { name: "sharing.queue.approve" }),
    );

    await waitFor(() => expect(approveChange).toHaveBeenCalledWith("01H"));
    await waitFor(() => expect(listChanges).toHaveBeenCalledTimes(2));
  });

  it("refuses to reject without a reason for the requester", async () => {
    render(<ApprovalQueue />);
    await screen.findByText("Handbook");

    await userEvent.click(
      screen.getByRole("button", { name: "sharing.queue.reject" }),
    );

    // The modal's confirm button — the card's own reject button carries the
    // same label, so it is located through the footer instead.
    const confirm = document.querySelector(".ant-modal-footer .ant-btn-primary");
    expect(confirm).not.toBeNull();
    await userEvent.click(confirm as Element);

    // An empty reason never reaches the backend, which refuses it with a 422.
    expect(rejectChange).not.toHaveBeenCalled();
    expect(message.error).toHaveBeenCalledWith(
      "sharing.queue.rejectReasonRequired",
    );
  });

  it("leaves approve and reject to administrators", async () => {
    currentUser.value = { id: 9, role: "user" };

    render(<ApprovalQueue />);
    await screen.findByText("Handbook");

    expect(
      screen.queryByRole("button", { name: "sharing.queue.approve" }),
    ).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: "sharing.queue.reject" }),
    ).not.toBeInTheDocument();
    expect(screen.getByText("sharing.queue.adminOnlyHint")).toBeInTheDocument();
  });
});
