import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import MemoryBackups from "./MemoryBackups";

vi.mock("../../../hooks/useServerTimezone", () => ({
  useServerTimezone: () => "UTC",
}));
vi.mock("../../../api/modules/memoryBackups", () => ({
  memoryBackupsApi: {
    list: vi.fn().mockResolvedValue([
      {
        backup_id: "owned-snapshot.zip",
        created_at: 1700000000,
        size_bytes: 512,
        backend: "sqlite",
        total_rows: 3,
      },
    ]),
  },
}));

describe("Readonly feature memory backups", () => {
  it("allows caller-owned archive management without offering namespace restoration", async () => {
    render(<MemoryBackups agentId="feature-a" scope="shared" readonly />);
    await screen.findByText("512 B");
    for (const label of [/^创建备份$/, /^下\s*载$/, /^删\s*除$/]) {
      const control = screen.getByText(label).closest("button");
      expect(control).toBeVisible();
      expect(control).toBeEnabled();
    }
    expect(screen.queryByText(/^恢\s*复$/)).not.toBeInTheDocument();
  });
});
