import { describe, expect, it } from "vitest";

import {
  canPreviewKnowledgeDocument,
  isEditableKnowledgeDocument,
} from "./knowledgeDocPreview";

describe("source-backed knowledge document actions", () => {
  it("allows original-backed previews without exposing platform text editing", () => {
    const sourceDocument = {
      filename: "source.md",
      content_type: "text/markdown",
      data_source_id: "source-1",
      has_original: true,
    };

    expect(canPreviewKnowledgeDocument(sourceDocument)).toBe(true);
    expect(isEditableKnowledgeDocument(sourceDocument)).toBe(false);
  });

  it("keeps source-path search hits previewable when original availability is unknown", () => {
    const searchHitWithoutLoadedDocument = {
      filename: "source.md",
      source_path: "nested/source.md",
    };

    expect(canPreviewKnowledgeDocument(searchHitWithoutLoadedDocument)).toBe(
      true,
    );
    expect(isEditableKnowledgeDocument(searchHitWithoutLoadedDocument)).toBe(
      false,
    );
  });

  it("does not offer preview when a source document has no readable original", () => {
    expect(
      canPreviewKnowledgeDocument({
        filename: "source.md",
        data_source_id: "source-1",
        has_original: false,
      }),
    ).toBe(false);
  });
});
