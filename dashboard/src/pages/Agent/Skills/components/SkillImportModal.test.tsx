import { describe, expect, it, vi } from "vitest";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import JSZip from "jszip";

import { SkillImportModal } from "./SkillImportModal";

async function makeZipFile(
  tree: Record<string, string>,
  filename = "skills.zip",
): Promise<File> {
  const zip = new JSZip();
  for (const [path, content] of Object.entries(tree)) {
    zip.file(path, content);
  }
  const blob = await zip.generateAsync({ type: "blob" });
  return new File([blob], filename, { type: "application/zip" });
}

function setInputFiles(input: HTMLInputElement, files: File[]) {
  Object.defineProperty(input, "files", {
    configurable: true,
    value: files,
  });
  fireEvent.change(input);
}

async function switchToZipMode(user: ReturnType<typeof userEvent.setup>) {
  // antd Segmented radios use pointer-events:none on the input; click the label text.
  await user.click(screen.getByText("skills.importFromZip"));
}

describe("<SkillImportModal /> local zip import", () => {
  it("imports a selected zip with overwrite flag", async () => {
    const user = userEvent.setup();
    const onClose = vi.fn();
    const onImportZip = vi.fn().mockResolvedValue({
      imported: 1,
      skipped: 0,
      failed: 0,
    });
    const zipFile = await makeZipFile({
      "demo/SKILL.md": "---\nname: demo\n---\n中文技能内容\n",
    });

    render(
      <SkillImportModal
        open
        importing={false}
        onClose={onClose}
        onImportUrl={vi.fn()}
        onImportZip={onImportZip}
      />,
    );

    await switchToZipMode(user);
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    setInputFiles(fileInput, [zipFile]);

    expect(screen.getByText("skills.zip")).toBeInTheDocument();

    await user.click(screen.getByText("skills.overwriteExisting"));
    await user.click(
      screen.getByRole("button", { name: "skills.importSkills" }),
    );

    await waitFor(() => {
      expect(onImportZip).toHaveBeenCalledTimes(1);
    });
    const [archiveBase64, options] = onImportZip.mock.calls[0];
    const imported = await JSZip.loadAsync(archiveBase64, { base64: true });
    expect(await imported.file("demo/SKILL.md")?.async("string")).toBe(
      "---\nname: demo\n---\n中文技能内容\n",
    );
    expect(options).toEqual({ overwrite: true });
    expect(onClose).toHaveBeenCalled();
  });

  it("rejects non-zip files before parsing", async () => {
    const user = userEvent.setup();
    const onImportZip = vi.fn();
    const txt = new File(["hello"], "notes.txt", { type: "text/plain" });

    render(
      <SkillImportModal
        open
        importing={false}
        onClose={vi.fn()}
        onImportUrl={vi.fn()}
        onImportZip={onImportZip}
      />,
    );

    await switchToZipMode(user);
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    setInputFiles(fileInput, [txt]);

    expect(screen.getByText("skills.zipOnly")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "skills.importSkills" }),
    ).toBeDisabled();
    expect(onImportZip).not.toHaveBeenCalled();
  });

  it("requires a zip before confirming", async () => {
    const user = userEvent.setup();
    const onImportZip = vi.fn();

    render(
      <SkillImportModal
        open
        importing={false}
        onClose={vi.fn()}
        onImportUrl={vi.fn()}
        onImportZip={onImportZip}
      />,
    );

    await switchToZipMode(user);
    expect(
      screen.getByRole("button", { name: "skills.importSkills" }),
    ).toBeDisabled();

    const zipFile = await makeZipFile({
      "demo/SKILL.md": "---\nname: demo\n---\n",
    });
    const fileInput = document.querySelector(
      'input[type="file"]',
    ) as HTMLInputElement;
    setInputFiles(fileInput, [zipFile]);
    const removeButtons = screen.getAllByText("skills.removeZip");
    // Click either remove button
    await user.click(removeButtons[0]);
    expect(
      screen.getByRole("button", { name: "skills.importSkills" }),
    ).toBeDisabled();
    expect(onImportZip).not.toHaveBeenCalled();
  });
});
