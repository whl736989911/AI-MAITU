import { Blob as SourceBlob } from "node:buffer";
import type { OctopUser } from "../../api/modules/auth";
import { CurrentUserProvider } from "../../hooks/useCurrentUser";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { App, ConfigProvider } from "antd";
import { MemoryRouter } from "react-router-dom";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  list: vi.fn(),
  get: vi.fn(),
  getCapability: vi.fn(),
  listDocuments: vi.fn(),
  coverage: vi.fn(),
  getTextDocument: vi.fn(),
  fetchDocumentFile: vi.fn(),
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

type TextDocumentPayload = {
  id: string;
  filename: string;
  content_type: string;
  title: string;
  text: string;
};

function base(id: string, name: string): KnowledgeBase {
  return {
    id,
    owner_user_id: 1,
    name,
    description: "",
    default_open: false,
    shared: false,
    is_enterprise: false,
    icon_name: "book",
    embedding_model: "",
    embedding_dim: 0,
    doc_count: 1,
    max_documents: 100,
    metadata_schema: [],
    created_at: 0,
    updated_at: 0,
  };
}

function document(
  id: string,
  baseId: string,
  provenance: Partial<KnowledgeDocument> = {},
): KnowledgeDocument {
  return {
    id,
    kb_id: baseId,
    filename: "source.md",
    path: "source.md",
    is_dir: false,
    content_type: "text/markdown",
    byte_size: 1,
    content_hash: "fixture-hash",
    status: "ready",
    error_message: "",
    chunk_count: 1,
    title: "Fixture document",
    created_at: 0,
    updated_at: 0,
    ...provenance,
  };
}

function permission(
  resourceId: string,
  resourceType: "knowledge_base" | "knowledge_document",
) {
  return {
    resource_type: resourceType,
    resource_id: resourceId,
    path: "",
    access_scope: "personal" as const,
    department_name: null,
    inherited_from: null,
    permission: "write" as const,
    effective: { read: true, upload: true, write: true },
    can_manage: true,
    can_delegate: true,
    version: 1,
    entry: null,
    pending: null,
  };
}

const ADMIN: OctopUser = {
  id: 99,
  username: "root",
  role: "admin",
  display_name: null,
  locale: "zh",
  permissions: ["users"],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

function renderPage() {
  return render(
    <CurrentUserProvider user={ADMIN} setUser={() => undefined}>
      <ConfigProvider>
        <App>
          <MemoryRouter>
            <KnowledgeBasesPage />
          </MemoryRouter>
        </App>
      </ConfigProvider>
    </CurrentUserProvider>,
  );
}

function previewAction() {
  return screen.getByRole("button", { name: "knowledgeBases.previewDocument" });
}

function selectBase(name: string) {
  const item = screen
    .getAllByRole("listitem")
    .find((row) => within(row).queryByText(name));
  expect(item).toBeTruthy();
  fireEvent.click(within(item!).getByText(name));
}

const sourceBase = base("source", "Source base");
const secondBase = base("second", "Second base");
const sourceDocument = document("source-document", sourceBase.id, {
  data_source_id: "folder-source",
  source_path: "source.md",
  has_original: true,
});
const platformDocument = document("platform-document", sourceBase.id, {
  filename: "platform.md",
  path: "platform.md",
  title: "Platform note",
});

beforeEach(() => {
  Object.values(api).forEach((method) => method.mockReset());
  Object.values(permissionsApi).forEach((method) => method.mockReset());
  Object.values(dataSourcesApi).forEach((method) => method.mockReset());
  window.localStorage.setItem("octop:knowledge-bases-docs-view", "card");

  api.list.mockResolvedValue([sourceBase, secondBase]);
  api.get.mockImplementation(async (id: string) =>
    id === sourceBase.id ? sourceBase : secondBase,
  );
  api.listDocuments.mockImplementation(async (id: string) =>
    id === sourceBase.id ? [sourceDocument] : [],
  );
  api.getCapability.mockResolvedValue({
    feature_enabled: true,
    backend: "remote",
    selected_model: "fixture-model",
    provider_id: "fixture-provider",
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
      backend: "remote",
      model: "",
      provider_id: "",
      prerequisites_ok: true,
      usable: true,
      checks: { deps_available: true, provider_ready: true },
    },
    limits: { max_docs_per_kb: 100, max_document_bytes: 1000000 },
  });
  api.coverage.mockResolvedValue({
    total: 1,
    searchable: 1,
    pending: 0,
    indexing: 0,
    failed: 0,
    unsupported: 0,
  });
  api.getTextDocument.mockRejectedValue(
    new Error("platform text content is forbidden for source documents"),
  );
  api.fetchDocumentFile.mockResolvedValue(
    new SourceBlob(["Source original text visible in the preview."], {
      type: "text/markdown",
    }),
  );
  permissionsApi.list.mockImplementation(async (id: string) => ({
    knowledge_base: permission(id, "knowledge_base"),
    documents:
      id === sourceBase.id
        ? [permission(sourceDocument.id, "knowledge_document")]
        : [],
  }));
  dataSourcesApi.list.mockResolvedValue([]);
});

afterEach(() => {
  cleanup();
  window.localStorage.removeItem("octop:knowledge-bases-docs-view");
});

describe("KnowledgeBases source document preview", () => {
  it("shows original Markdown from the file endpoint without platform content", async () => {
    renderPage();

    await screen.findAllByText(sourceBase.name);
    selectBase(sourceBase.name);
    await screen.findByText("source.md");
    fireEvent.click(previewAction());

    expect(
      await screen.findByText("Source original text visible in the preview."),
    ).toBeInTheDocument();
    expect(api.fetchDocumentFile).toHaveBeenCalledWith(
      sourceBase.id,
      sourceDocument.id,
      undefined,
      "inline",
      undefined,
      expect.any(AbortSignal),
    );
    expect(api.getTextDocument).not.toHaveBeenCalled();
  });

  it("closes a pending preview on base switch and ignores its late response", async () => {
    const pendingText = deferred<TextDocumentPayload>();
    const staleText = "Late text from the previous knowledge base";
    api.listDocuments.mockImplementation(async (id: string) =>
      id === sourceBase.id ? [platformDocument] : [],
    );
    api.getTextDocument.mockReturnValue(pendingText.promise);

    renderPage();
    await screen.findAllByText(sourceBase.name);
    selectBase(sourceBase.name);
    await screen.findByText("platform.md");
    fireEvent.click(previewAction());
    await waitFor(() =>
      expect(api.getTextDocument).toHaveBeenCalledWith(
        sourceBase.id,
        platformDocument.id,
      ),
    );

    selectBase(secondBase.name);

    await waitFor(() => expect(api.get).toHaveBeenCalledWith(secondBase.id));
    await waitFor(() =>
      expect(screen.queryByText("platform.md")).not.toBeInTheDocument(),
    );
    await waitFor(() =>
      expect(screen.queryByRole("dialog")).not.toBeInTheDocument(),
    );

    await act(async () => {
      pendingText.resolve({
        id: platformDocument.id,
        filename: platformDocument.filename,
        content_type: platformDocument.content_type,
        title: platformDocument.title ?? "",
        text: staleText,
      });
    });

    expect(screen.queryByText(staleText)).not.toBeInTheDocument();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
});
