import { App, ConfigProvider } from "antd";
import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  getDocument: vi.fn(),
  updateDocument: vi.fn(),
  principals: vi.fn(),
  delegations: vi.fn(),
  addDelegation: vi.fn(),
  revokeDelegation: vi.fn(),
}));
vi.mock("../../../api/modules/knowledgePermissions", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/knowledgePermissions")
  >();
  return {
    ...actual,
    knowledgePermissionsApi: { ...actual.knowledgePermissionsApi, ...api },
  };
});

import type { KnowledgePermissionMetadata } from "../../../api/modules/knowledgePermissions";
import KnowledgePermissionsDrawer from "./KnowledgePermissionsDrawer";

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function metadata(id: string, canDelegate = false) {
  return {
    resource_type: "knowledge_document",
    resource_id: id,
    path: `${id}.pdf`,
    access_scope: "personal",
    department_name: null,
    inherited_from: null,
    permission: "read",
    effective: { read: true, upload: false, write: false },
    can_manage: true,
    can_delegate: canDelegate,
    entry: {
      visibility: "private",
      unit_key: null,
      permission: "read",
      grants: [],
      version: 1,
    },
    version: 1,
    pending: null,
  } as KnowledgePermissionMetadata;
}

beforeEach(() => {
  Object.values(api).forEach((method) => method.mockReset());
  api.principals.mockResolvedValue({ users: [], departments: [], roles: [] });
  api.delegations.mockResolvedValue({
    principals: [{ id: 3, username: "delegate", label: "Delegate" }],
    grants: [],
  });
});

describe("<KnowledgePermissionsDrawer />", () => {
  it("does not replace the selected resource after an earlier permission save resolves", async () => {
    const pending = deferred<{ permission: KnowledgePermissionMetadata }>();
    api.getDocument.mockImplementation((_baseId: string, id: string) =>
      Promise.resolve(metadata(id)),
    );
    api.updateDocument.mockReturnValue(pending.promise);
    const onSaved = vi.fn();
    const props = {
      baseId: "kb",
      open: true,
      isDirectory: false,
      onClose: vi.fn(),
      onSaved,
    };
    const { rerender } = render(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer {...props} metadata={metadata("first")} />
        </App>
      </ConfigProvider>,
    );

    await screen.findByText("first.pdf");
    await userEvent.click(
      screen.getByRole("button", { name: "knowledgeBases.permissions.save" }),
    );
    await waitFor(() => expect(api.updateDocument).toHaveBeenCalledTimes(1));
    rerender(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            metadata={metadata("second")}
          />
        </App>
      </ConfigProvider>,
    );
    await screen.findByText("second.pdf");
    await act(async () => {
      pending.resolve({ permission: metadata("first") });
      await pending.promise;
    });
    expect(onSaved).not.toHaveBeenCalled();
  });
  it("ignores a permission save after closing and reopening the same resource", async () => {
    const pending = deferred<{ permission: KnowledgePermissionMetadata }>();
    api.getDocument.mockResolvedValue(metadata("first"));
    api.updateDocument.mockReturnValue(pending.promise);
    const onSaved = vi.fn();
    const props = {
      baseId: "kb",
      isDirectory: false,
      onClose: vi.fn(),
      onSaved,
    };
    const { rerender } = render(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            open
            metadata={metadata("first")}
          />
        </App>
      </ConfigProvider>,
    );

    await screen.findByText("first.pdf");
    await userEvent.click(
      screen.getByRole("button", { name: "knowledgeBases.permissions.save" }),
    );
    await waitFor(() => expect(api.updateDocument).toHaveBeenCalledTimes(1));
    rerender(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            open={false}
            metadata={metadata("first")}
          />
        </App>
      </ConfigProvider>,
    );
    rerender(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            open
            metadata={metadata("first")}
          />
        </App>
      </ConfigProvider>,
    );
    await screen.findByText("first.pdf");
    await act(async () => {
      pending.resolve({ permission: metadata("first") });
      await pending.promise;
    });

    expect(onSaved).not.toHaveBeenCalled();
  });

  it("does not render a completed delegation for a different selected document", async () => {
    const pending = deferred<{
      principals: { id: number; username: string; label: string }[];
      grants: {
        id: string;
        grantor_user_id: number;
        grantee_user_id: number;
        grantee_label: string;
        can_delegate: boolean;
        can_revoke: boolean;
      }[];
    }>();
    api.getDocument.mockImplementation((_baseId: string, id: string) =>
      Promise.resolve(metadata(id, true)),
    );
    api.addDelegation.mockReturnValue(pending.promise);
    const props = {
      baseId: "kb",
      open: true,
      isDirectory: false,
      onClose: vi.fn(),
      onSaved: vi.fn(),
    };
    const { rerender } = render(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            metadata={metadata("first", true)}
          />
        </App>
      </ConfigProvider>,
    );
    await screen.findByText("first.pdf");
    const user = userEvent.setup();
    await user.click(await screen.findByRole("combobox"));
    await user.click(await screen.findByText("Delegate"));
    await user.click(
      screen.getByRole("button", {
        name: "knowledgeBases.permissions.grantDelegation",
      }),
    );
    await waitFor(() =>
      expect(api.addDelegation).toHaveBeenCalledWith("kb", "first", {
        grantee_user_id: 3,
        can_delegate: false,
      }),
    );
    rerender(
      <ConfigProvider>
        <App>
          <KnowledgePermissionsDrawer
            {...props}
            metadata={metadata("second", true)}
          />
        </App>
      </ConfigProvider>,
    );
    await screen.findByText("second.pdf");
    await act(async () => {
      pending.resolve({
        principals: [{ id: 9, username: "stale", label: "Stale principal" }],
        grants: [
          {
            id: "stale-grant",
            grantor_user_id: 1,
            grantee_user_id: 9,
            grantee_label: "Stale grant",
            can_delegate: false,
            can_revoke: true,
          },
        ],
      });
      await pending.promise;
    });
    expect(screen.queryByText("Stale grant")).not.toBeInTheDocument();
  });
});
