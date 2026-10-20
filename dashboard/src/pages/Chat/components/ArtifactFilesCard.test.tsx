import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options?.defaultValue ?? key,
  }),
}));

vi.mock("./MessageFileCard", () => ({
  MessageFileCard: ({
    url,
    filename,
    workspacePath,
  }: {
    url: string;
    filename?: string;
    workspacePath?: string;
  }) => (
    <div data-testid="message-file">
      {filename}|{workspacePath}|{url}
    </div>
  ),
}));

import ArtifactFilesCard from "./ArtifactFilesCard";

describe("ArtifactFilesCard", () => {
  it("renders a direct download card for every produced file", () => {
    render(
      <ArtifactFilesCard
        agentId="main"
        files={["/work/out/report.docx", "/work/out/checklist.md"]}
      />,
    );

    expect(screen.getByTestId("turn-artifact-files")).toBeInTheDocument();
    expect(screen.getAllByTestId("message-file")).toHaveLength(2);
    expect(screen.getByText(/report\.docx/)).toBeInTheDocument();
    expect(screen.getByText(/checklist\.md/)).toBeInTheDocument();
  });

  it("opens the complete file list when requested", async () => {
    const user = userEvent.setup();
    const onOpenFiles = vi.fn();
    render(
      <ArtifactFilesCard
        agentId="main"
        files={["/work/out/report.docx"]}
        onOpenFiles={onOpenFiles}
      />,
    );

    await user.click(
      screen.getByRole("button", {
        name: /chat\.artifactFilesOpenAll|在文件变更中查看|file changes/i,
      }),
    );
    expect(onOpenFiles).toHaveBeenCalledTimes(1);
  });
});
