import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { App, ConfigProvider } from "antd";
import { useState } from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listCollections: vi.fn(),
  createCollections: vi.fn(),
  updateCollection: vi.fn(),
  generateCollection: vi.fn(),
  getCollectionDocuments: vi.fn(),
  deleteCollection: vi.fn(),
}));

vi.mock("../../../api/modules/knowledgeBases", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/knowledgeBases")
  >();
  return {
    ...actual,
    knowledgeBasesApi: { ...actual.knowledgeBasesApi, ...api },
  };
});

import type {
  KnowledgeCollection,
  KnowledgeCollectionDocuments,
  KnowledgeDocument,
} from "../../../api/modules/knowledgeBases";
import KnowledgeCollections from "./KnowledgeCollections";

function document(id: string): KnowledgeDocument {
  return {
    id,
    kb_id: "base-a",
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

function collection(id: string, path: string): KnowledgeCollection {
  return {
    id,
    kb_id: id,
    target_document_id: `doc-${id}`,
    target_path: path,
    target_kind: "file",
    name: path,
    prompt: "prompt",
    created_by: 1,
    status: "ready",
    coverage: { total: 1, read: 1, generated: 1, failed: 0, pending: 0 },
    generation: {
      state: "ready",
      error: null,
      generated_at: null,
      model_ref: null,
    },
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

function Scenario() {
  const [baseId, setBaseId] = useState("A");
  return (
    <ConfigProvider>
      <App>
        <button onClick={() => setBaseId("B")}>Switch base</button>
        <KnowledgeCollections
          baseId={baseId}
          documents={[]}
          permissionsByDocument={new Map()}
          onOpenTarget={() => undefined}
          onOpenCitation={() => undefined}
        />
      </App>
    </ConfigProvider>
  );
}

function SelectionScenario() {
  const [baseId, setBaseId] = useState("A");
  const [documents, setDocuments] = useState([document("doc-a")]);
  return (
    <ConfigProvider>
      <App>
        <button onClick={() => setBaseId("B")}>Switch base</button>
        <button onClick={() => setDocuments([])}>Remove document</button>
        <KnowledgeCollections
          baseId={baseId}
          documents={documents}
          permissionsByDocument={new Map()}
          onOpenTarget={() => undefined}
          onOpenCitation={() => undefined}
        />
      </App>
    </ConfigProvider>
  );
}

function CreateScenario() {
  const [baseId, setBaseId] = useState("A");
  const [documents, setDocuments] = useState([document("doc-A")]);
  return (
    <ConfigProvider>
      <App>
        <button
          onClick={() => {
            setBaseId("B");
            setDocuments([document("doc-B")]);
          }}
        >
          Switch base
        </button>
        <KnowledgeCollections
          baseId={baseId}
          documents={documents}
          permissionsByDocument={new Map()}
          onOpenTarget={() => undefined}
          onOpenCitation={() => undefined}
        />
      </App>
    </ConfigProvider>
  );
}

beforeEach(() => {
  Object.values(api).forEach((method) => method.mockReset());
});

describe("<KnowledgeCollections />", () => {
  it("does not replace the new base collections with an older pending response", async () => {
    const responseA = deferred<KnowledgeCollection[]>();
    const responseB = deferred<KnowledgeCollection[]>();
    api.listCollections.mockImplementation((baseId: string) =>
      baseId === "A" ? responseA.promise : responseB.promise,
    );
    const user = userEvent.setup();
    render(<Scenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledWith("A"));
    await user.click(screen.getByRole("button", { name: "Switch base" }));
    await waitFor(() => expect(api.listCollections).toHaveBeenCalledWith("B"));

    await act(async () => {
      responseB.resolve([collection("B", "base-b.md")]);
      await responseB.promise;
    });
    expect(await screen.findByText("base-b.md")).toBeInTheDocument();

    await act(async () => {
      responseA.resolve([collection("A", "base-a.md")]);
      await responseA.promise;
    });

    expect(screen.getByText("base-b.md")).toBeInTheDocument();
    expect(screen.queryByText("base-a.md")).not.toBeInTheDocument();
  });
  it("clears selected document IDs when the base changes before documents reload", async () => {
    const user = userEvent.setup();
    render(<SelectionScenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    const checkbox = await screen.findByRole("checkbox", {
      name: "Select tree node",
    });
    await user.click(checkbox);
    expect(
      screen.getByRole("button", { name: "knowledgeBases.collections.create" }),
    ).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Switch base" }));

    expect(checkbox).not.toBeChecked();
    expect(
      screen.getByRole("button", { name: "knowledgeBases.collections.create" }),
    ).toBeDisabled();
  });

  it("removes selections for documents that disappear from the tree", async () => {
    const user = userEvent.setup();
    render(<SelectionScenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    await user.click(
      await screen.findByRole("checkbox", { name: "Select tree node" }),
    );
    expect(
      screen.getByRole("button", { name: "knowledgeBases.collections.create" }),
    ).toBeEnabled();

    await user.click(screen.getByRole("button", { name: "Remove document" }));

    expect(
      screen.getByRole("button", { name: "knowledgeBases.collections.create" }),
    ).toBeDisabled();
  });
  it("does not carry the collection prompt across bases", async () => {
    api.listCollections.mockResolvedValue([]);
    const user = userEvent.setup();
    render(<SelectionScenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    const prompt = screen.getByRole("textbox", {
      name: "knowledgeBases.collections.prompt",
    });
    await user.type(prompt, "prompt from A");
    await user.click(screen.getByRole("button", { name: "Switch base" }));

    expect(prompt).toHaveValue("");
  });

  it("ignores an inspection response from the previously selected base", async () => {
    const details = deferred<KnowledgeCollectionDocuments>();
    api.listCollections.mockImplementation((baseId: string) =>
      Promise.resolve(baseId === "A" ? [collection("A", "base-a.md")] : []),
    );
    api.getCollectionDocuments.mockReturnValue(details.promise);
    const user = userEvent.setup();
    render(<Scenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    await user.click(
      await screen.findByRole("button", {
        name: "knowledgeBases.collections.inspect",
      }),
    );
    await user.click(screen.getByRole("button", { name: "Switch base" }));

    await act(async () => {
      details.resolve({
        collection: collection("A", "OLD A INSPECTION"),
        topic_document: "old topic",
        reading_document: "old reading",
        storage_path: "a",
      });
      await details.promise;
    });

    expect(screen.queryByText("OLD A INSPECTION")).not.toBeInTheDocument();
  });

  it("does not let an old create completion clear the new base form", async () => {
    api.listCollections.mockResolvedValue([]);
    const createA = deferred<KnowledgeCollection[]>();
    const createB = deferred<KnowledgeCollection[]>();
    api.createCollections.mockImplementation((baseId: string) =>
      baseId === "A" ? createA.promise : createB.promise,
    );
    const user = userEvent.setup();
    render(<CreateScenario />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.open" }),
    );
    await user.click(
      await screen.findByRole("checkbox", { name: "Select tree node" }),
    );
    await user.type(
      screen.getByRole("textbox", {
        name: "knowledgeBases.collections.prompt",
      }),
      "prompt A",
    );
    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.collections.create" }),
    );
    await waitFor(() =>
      expect(api.createCollections).toHaveBeenCalledWith(
        "A",
        ["doc-A"],
        "prompt A",
      ),
    );

    await user.click(screen.getByRole("button", { name: "Switch base" }));
    const checkboxB = await screen.findByRole("checkbox", {
      name: "Select tree node",
    });
    await user.click(checkboxB);
    const promptB = screen.getByRole("textbox", {
      name: "knowledgeBases.collections.prompt",
    });
    await user.type(promptB, "prompt B");
    const createButtonB = screen.getByRole("button", {
      name: "knowledgeBases.collections.create",
    });
    await user.click(createButtonB);
    await waitFor(() =>
      expect(api.createCollections).toHaveBeenCalledWith(
        "B",
        ["doc-B"],
        "prompt B",
      ),
    );

    await act(async () => {
      createA.resolve([]);
      await createA.promise;
    });

    expect(promptB).toHaveValue("prompt B");
    expect(checkboxB).toBeChecked();
    await user.click(createButtonB);
    expect(api.createCollections).toHaveBeenCalledTimes(2);

    await act(async () => {
      createB.resolve([]);
      await createB.promise;
    });
  });
});
