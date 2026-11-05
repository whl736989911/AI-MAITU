import { act, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  listSuggestions: vi.fn(),
  enrichDocument: vi.fn(),
  applySuggestion: vi.fn(),
  rejectSuggestion: vi.fn(),
}));

vi.mock("../../../api/modules/knowledgeBases", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/knowledgeBases")
  >();
  return { ...actual, knowledgeBasesApi: api };
});

import type {
  KnowledgeDocument,
  KnowledgeDocumentSuggestion,
} from "../../../api/modules/knowledgeBases";
import KnowledgeSuggestionModal from "./KnowledgeSuggestionModal";

function document(id: string, filename: string): KnowledgeDocument {
  return {
    id,
    filename,
    path: filename,
    is_dir: false,
  } as KnowledgeDocument;
}

function suggestion(
  id: string,
  documentId: string,
  summary: string,
): KnowledgeDocumentSuggestion {
  return {
    suggestion_id: id,
    document_id: documentId,
    kb_id: "kb",
    content_hash: "",
    status: "pending",
    summary,
    keywords: [],
    fields: {},
    model: "test/model",
    error: null,
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
  api.listSuggestions.mockReset();
  api.enrichDocument.mockReset();
  api.applySuggestion.mockReset();
  api.rejectSuggestion.mockReset();
});

describe("<KnowledgeSuggestionModal />", () => {
  it("ignores a pending response after the dialog switches documents", async () => {
    const first = deferred<KnowledgeDocumentSuggestion[]>();
    api.listSuggestions.mockImplementation(
      (_baseId: string, params: { documentId?: string }) =>
        params.documentId === "doc-a"
          ? first.promise
          : Promise.resolve([suggestion("sug-b", "doc-b", "summary for B")]),
    );

    const props = {
      open: true,
      baseId: "kb",
      schema: [],
      onClose: () => undefined,
    };
    const { rerender } = render(
      <KnowledgeSuggestionModal
        {...props}
        document={document("doc-a", "a.pdf")}
      />,
    );
    rerender(
      <KnowledgeSuggestionModal
        {...props}
        document={document("doc-b", "b.pdf")}
      />,
    );

    await waitFor(() =>
      expect(screen.getByText("summary for B")).toBeInTheDocument(),
    );

    await act(async () => {
      first.resolve([suggestion("sug-a", "doc-a", "summary for A")]);
      await first.promise;
    });

    expect(screen.queryByText("summary for A")).toBeNull();
    expect(screen.getByText("summary for B")).toBeInTheDocument();
  });
});
