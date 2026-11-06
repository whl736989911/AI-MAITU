import { act, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  getCapability: vi.fn(),
  listDocuments: vi.fn(),
  coverage: vi.fn(),
}));
const permissionsApi = vi.hoisted(() => ({ list: vi.fn() }));
const dataSourcesApi = vi.hoisted(() => ({ list: vi.fn() }));

vi.mock("../../api/modules/knowledgeBases", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/knowledgeBases")
  >();
  return {
    ...actual,
    knowledgeBasesApi: { ...actual.knowledgeBasesApi, ...api },
  };
});
vi.mock("../../api/modules/knowledgePermissions", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/knowledgePermissions")
  >();
  return {
    ...actual,
    knowledgePermissionsApi: {
      ...actual.knowledgePermissionsApi,
      ...permissionsApi,
    },
  };
});
vi.mock("../../api/modules/dataSources", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../api/modules/dataSources")
  >();
  return {
    ...actual,
    dataSourcesApi: { ...actual.dataSourcesApi, ...dataSourcesApi },
  };
});

import type {
  KnowledgeBase,
  KnowledgeDocument,
} from "../../api/modules/knowledgeBases";
import KnowledgeBasesPage from "./index";

function base(id: string): KnowledgeBase {
  return {
    id,
    owner_user_id: 1,
    name: `Base ${id}`,
    description: "",
    default_open: false,
    shared: false,
    is_enterprise: false,
    icon_name: "book",
    embedding_model: "",
    embedding_dim: 0,
    doc_count: 1,
    max_documents: 100,
    metadata_schema: [
      {
        key: "tag",
        label: "Tag",
        type: "text",
        required: false,
        searchable: true,
        options: [],
      },
    ],
    created_at: 0,
    updated_at: 0,
  };
}

function document(id: string, baseId: string): KnowledgeDocument {
  return {
    id,
    kb_id: baseId,
    filename: `${id}.md`,
    path: `${id}.md`,
    content_type: "text/markdown",
    byte_size: 1,
    content_hash: "",
    status: "ready",
    error_message: "",
    chunk_count: 1,
    created_at: 0,
    updated_at: 0,
  };
}

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

beforeEach(() => {
  Object.values(api).forEach((method) => method.mockReset());
  Object.values(permissionsApi).forEach((method) => method.mockReset());
  Object.values(dataSourcesApi).forEach((method) => method.mockReset());
  api.list.mockResolvedValue([base("A"), base("B")]);
  api.get.mockImplementation(async (id: string) => base(id));
  api.getCapability.mockResolvedValue({
    feature_enabled: false,
    backend: "onnx",
    selected_model: "",
    provider_id: "",
    prerequisites_ok: true,
    usable: true,
    checks: {
      model_selected: true,
      model_downloaded: true,
      deps_available: true,
      provider_ready: true,
    },
    ocr: {
      enabled: false,
      backend: "onnx",
      model: "",
      provider_id: "",
      prerequisites_ok: true,
      usable: true,
      checks: { deps_available: true, provider_ready: true },
    },
  });
  api.coverage.mockResolvedValue({
    total: 1,
    searchable: 1,
    pending: 0,
    indexing: 0,
    failed: 0,
    unsupported: 0,
  });
  api.listDocuments.mockImplementation((id: string) =>
    Promise.resolve([document(`doc-${id}`, id)]),
  );
  permissionsApi.list.mockImplementation(async (id: string) => ({
    knowledge_base: {
      resource_id: id,
      can_manage: false,
      effective: { read: true, write: true, upload: true },
    },
    documents: [],
  }));
  dataSourcesApi.list.mockResolvedValue([]);
});

describe("KnowledgeBases metadata filtering", () => {
  it("does not let an old base's filter response replace the newly selected base documents", async () => {
    const filtered = deferred<KnowledgeDocument[]>();
    api.listDocuments.mockImplementation(
      (
        id: string,
        _prefix: string | undefined,
        metadata?: Record<string, unknown>,
      ) =>
        metadata
          ? filtered.promise
          : Promise.resolve([document(`doc-${id}`, id)]),
    );
    const user = userEvent.setup();
    render(
      <ConfigProvider>
        <App>
          <MemoryRouter>
            <KnowledgeBasesPage />
          </MemoryRouter>
        </App>
      </ConfigProvider>,
    );

    await screen.findByText("doc-A.md");
    await user.click(screen.getByRole("combobox"));
    await user.click(await screen.findByText("Tag"));
    await user.type(
      screen.getByPlaceholderText("knowledgeBases.metadata.filterValue"),
      "yes",
    );
    await user.click(
      screen.getByRole("button", {
        name: "knowledgeBases.metadata.applyFilter",
      }),
    );
    await waitFor(() =>
      expect(api.listDocuments).toHaveBeenCalledWith("A", undefined, {
        tag: "yes",
      }),
    );

    await user.click(
      within(screen.getAllByRole("listitem")[1]).getByText("Base B"),
    );
    await screen.findByText("doc-B.md");
    await act(async () => {
      filtered.resolve([document("stale-filter-A", "A")]);
      await filtered.promise;
    });

    expect(screen.getByText("doc-B.md")).toBeInTheDocument();
    expect(screen.queryByText("stale-filter-A.md")).not.toBeInTheDocument();
  });
});
