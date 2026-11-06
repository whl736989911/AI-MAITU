import { render, screen, waitFor } from "@testing-library/react";
import { fireEvent } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({
  putMetadataSchema: vi.fn(),
  updateDocumentMetadata: vi.fn(),
}));

vi.mock("../../../api/modules/knowledgeBases", async (importOriginal) => {
  const actual = await importOriginal<
    typeof import("../../../api/modules/knowledgeBases")
  >();
  return { ...actual, knowledgeBasesApi: api };
});

import type { KnowledgeDocument } from "../../../api/modules/knowledgeBases";
import KnowledgeMetadataModal from "./KnowledgeMetadataModal";

beforeEach(() => {
  api.putMetadataSchema.mockReset();
  api.updateDocumentMetadata.mockReset();
});
function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => {
    resolve = done;
  });
  return { promise, resolve };
}

describe("<KnowledgeMetadataModal />", () => {
  it("keeps edited enum options when the field key changes", async () => {
    api.putMetadataSchema.mockResolvedValue({
      fields: [
        {
          key: "drawing_no",
          label: "Drawing",
          type: "enum",
          options: ["B1"],
          required: false,
          searchable: true,
        },
      ],
    });
    const user = userEvent.setup();

    render(
      <KnowledgeMetadataModal
        open
        baseId="kb"
        schema={[
          {
            key: "drawing_no",
            label: "Drawing",
            type: "enum",
            options: ["A0"],
            required: false,
            searchable: true,
          },
        ]}
        onClose={() => undefined}
      />,
    );

    const options = screen.getByPlaceholderText(
      "knowledgeBases.metadata.options",
    );
    await user.clear(options);
    await user.type(options, "B1");

    const key = screen.getByPlaceholderText("knowledgeBases.metadata.key");
    await user.clear(key);
    await user.type(key, "renamed");

    expect(
      screen.getByPlaceholderText("knowledgeBases.metadata.options"),
    ).toHaveValue("B1");

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.metadata.save" }),
    );
    await waitFor(() => expect(api.putMetadataSchema).toHaveBeenCalledTimes(1));
    expect(api.putMetadataSchema.mock.calls[0][1][0].options).toEqual(["B1"]);
  });
  it("keeps each row's edited options when keys temporarily collide", () => {
    render(
      <KnowledgeMetadataModal
        open
        baseId="kb"
        schema={[
          {
            key: "alpha",
            label: "Alpha",
            type: "enum",
            options: ["A"],
            required: false,
            searchable: true,
          },
          {
            key: "beta",
            label: "Beta",
            type: "enum",
            options: ["B"],
            required: false,
            searchable: true,
          },
        ]}
        onClose={() => undefined}
      />,
    );
    const inputs = screen.getAllByPlaceholderText(
      "knowledgeBases.metadata.options",
    );
    fireEvent.change(inputs[0], { target: { value: "A edited" } });
    fireEvent.change(inputs[1], { target: { value: "B edited" } });
    const key = screen.getAllByPlaceholderText(
      "knowledgeBases.metadata.key",
    )[0];
    fireEvent.change(key, { target: { value: "beta" } });
    fireEvent.change(key, { target: { value: "gamma" } });
    const options = screen.getAllByPlaceholderText(
      "knowledgeBases.metadata.options",
    );
    expect(options[0]).toHaveValue("A edited");
    expect(options[1]).toHaveValue("B edited");
  });
  it("does not close the replacement document after an earlier save finishes", async () => {
    const pending = deferred<{ metadata: Record<string, unknown> }>();
    api.updateDocumentMetadata.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    const onClose = vi.fn();
    const props = {
      open: true,
      baseId: "kb",
      schema: [
        {
          key: "serial",
          label: "Serial",
          type: "text" as const,
          options: [],
          required: false,
          searchable: true,
        },
      ],
      onClose,
    };
    const document = (id: string) =>
      ({
        id,
        filename: `${id}.pdf`,
        path: `${id}.pdf`,
        is_dir: false,
        metadata: {},
      }) as KnowledgeDocument;
    const { rerender } = render(
      <KnowledgeMetadataModal {...props} document={document("first")} />,
    );

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.metadata.save" }),
    );
    await waitFor(() =>
      expect(api.updateDocumentMetadata).toHaveBeenCalledWith(
        "kb",
        "first",
        {},
      ),
    );
    rerender(
      <KnowledgeMetadataModal {...props} document={document("second")} />,
    );
    pending.resolve({ metadata: {} });
    await waitFor(() => expect(onClose).not.toHaveBeenCalled());
  });
  it("ignores a schema save after the modal closes and reopens for the same base", async () => {
    const pending = deferred<{ fields: [] }>();
    api.putMetadataSchema.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSchemaSaved = vi.fn();
    const props = {
      open: true,
      baseId: "kb",
      schema: [],
      onClose,
      onSchemaSaved,
    };
    const { rerender } = render(<KnowledgeMetadataModal {...props} />);

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.metadata.save" }),
    );
    await waitFor(() => expect(api.putMetadataSchema).toHaveBeenCalledTimes(1));
    rerender(<KnowledgeMetadataModal {...props} open={false} />);
    rerender(<KnowledgeMetadataModal {...props} />);
    pending.resolve({ fields: [] });

    await waitFor(() => expect(onSchemaSaved).not.toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });

  it("ignores a schema save after switching knowledge bases", async () => {
    const pending = deferred<{ fields: [] }>();
    api.putMetadataSchema.mockReturnValue(pending.promise);
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onSchemaSaved = vi.fn();
    const props = { open: true, schema: [], onClose, onSchemaSaved };
    const { rerender } = render(
      <KnowledgeMetadataModal {...props} baseId="kb-first" />,
    );

    await user.click(
      screen.getByRole("button", { name: "knowledgeBases.metadata.save" }),
    );
    await waitFor(() => expect(api.putMetadataSchema).toHaveBeenCalledTimes(1));
    rerender(<KnowledgeMetadataModal {...props} baseId="kb-second" />);
    pending.resolve({ fields: [] });

    await waitFor(() => expect(onSchemaSaved).not.toHaveBeenCalled());
    expect(onClose).not.toHaveBeenCalled();
  });
});
