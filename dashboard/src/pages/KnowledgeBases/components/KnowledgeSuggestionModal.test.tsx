import { act, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
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
  it.each(["apply", "reject"] as const)(
    "does not let an old %s response act on a newly opened document",
    async (action) => {
      const pending = deferred<KnowledgeDocumentSuggestion>();
      api.listSuggestions.mockImplementation(
        (_baseId: string, params: { documentId?: string }) =>
          Promise.resolve([
            suggestion(
              `sug-${params.documentId}`,
              params.documentId!,
              `summary for ${params.documentId}`,
            ),
          ]),
      );
      const mutation =
        action === "apply" ? api.applySuggestion : api.rejectSuggestion;
      mutation.mockReturnValue(pending.promise);
      const onClose = vi.fn();
      const onSaved = vi.fn();
      const props = { open: true, baseId: "kb", schema: [], onClose, onSaved };
      const user = userEvent.setup();
      const { rerender } = render(
        <KnowledgeSuggestionModal
          {...props}
          document={document("a", "a.pdf")}
        />,
      );
      await screen.findByText("summary for a");
      await user.click(
        screen.getByRole("button", {
          name: `knowledgeBases.suggestions.${action}`,
        }),
      );
      rerender(
        <KnowledgeSuggestionModal
          {...props}
          document={document("b", "b.pdf")}
        />,
      );
      await screen.findByText("summary for b");
      await act(async () => {
        pending.resolve(suggestion("sug-a", "a", "summary for a"));
        await pending.promise;
      });
      expect(screen.getByText("summary for b")).toBeInTheDocument();
      expect(onClose).not.toHaveBeenCalled();
      expect(onSaved).not.toHaveBeenCalled();
    },
  );

  it("allows generation on a new document while an old generation is pending", async () => {
    const oldGeneration = deferred<KnowledgeDocumentSuggestion>();
    api.listSuggestions.mockResolvedValue([]);
    api.enrichDocument.mockImplementation(
      (_baseId: string, documentId: string) =>
        documentId === "a"
          ? oldGeneration.promise
          : Promise.resolve(suggestion("sug-b", "b", "generated for b")),
    );
    const user = userEvent.setup();
    const props = { open: true, baseId: "kb", schema: [], onClose: vi.fn() };
    const { rerender } = render(
      <KnowledgeSuggestionModal {...props} document={document("a", "a.pdf")} />,
    );
    await user.click(
      await screen.findByRole("button", {
        name: /knowledgeBases\.suggestions\.generate/,
      }),
    );
    rerender(
      <KnowledgeSuggestionModal {...props} document={document("b", "b.pdf")} />,
    );
    await user.click(
      await screen.findByRole("button", {
        name: /knowledgeBases\.suggestions\.generate/,
      }),
    );
    await screen.findByText("generated for b");
    await act(async () => {
      oldGeneration.resolve(suggestion("sug-a", "a", "generated for a"));
      await oldGeneration.promise;
    });
    expect(screen.queryByText("generated for a")).not.toBeInTheDocument();
  });
});
