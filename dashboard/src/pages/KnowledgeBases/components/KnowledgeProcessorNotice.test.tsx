import { beforeEach, describe, expect, it, vi } from "vitest";
import { render, screen } from "@testing-library/react";

const getProcessors = vi.hoisted(() => vi.fn());
vi.mock("../../../api/modules/knowledgeBases", () => ({
  knowledgeBasesApi: { getProcessors },
}));

import KnowledgeProcessorNotice from "./KnowledgeProcessorNotice";

beforeEach(() => {
  getProcessors.mockReset();
});

describe("knowledge upload processor availability", () => {
  it("flags only files without an available processor, not supported PDF originals", async () => {
    getProcessors.mockResolvedValue({
      archive_only: true,
      processors: [
        { id: "built_in", available: true, extensions: [".pdf"] },
        { id: "libredwg", available: false, extensions: [".dwg"] },
      ],
    });
    render(
      <KnowledgeProcessorNotice
        files={[new File(["pdf"], "标准.pdf"), new File(["cad"], "图纸.dwg")]}
      />,
    );
    expect(await screen.findByText("图纸.dwg")).toBeInTheDocument();
    expect(screen.queryByText("标准.pdf")).not.toBeInTheDocument();
  });

  it("recognizes an enabled CAD processor regardless of filename extension case", async () => {
    getProcessors.mockResolvedValue({
      archive_only: true,
      processors: [{ id: "libredwg", available: true, extensions: [".dwg"] }],
    });
    render(
      <KnowledgeProcessorNotice files={[new File(["cad"], "图纸.DWG")]} />,
    );
    await screen.findByText("knowledgeBases.processingAvailableFormats");
    expect(screen.queryByText("图纸.DWG")).not.toBeInTheDocument();
    expect(
      screen.queryByText("knowledgeBases.processingArchiveFiles"),
    ).not.toBeInTheDocument();
  });

  it("does not guess that selected files are archive-only when capability lookup fails", async () => {
    getProcessors.mockRejectedValue(new Error("unavailable"));
    render(
      <KnowledgeProcessorNotice files={[new File(["data"], "资料.bin")]} />,
    );
    await screen.findByText("knowledgeBases.processingCapabilityFailed");
    expect(screen.queryByText("资料.bin")).not.toBeInTheDocument();
    expect(
      screen.queryByText("knowledgeBases.processingAvailableFormats"),
    ).not.toBeInTheDocument();
  });
});
