import type { DemoRow } from "./contract";
import type { PreviewCopy, PreviewDocument } from "../model";
const encoder = new TextEncoder();
export const DEFAULT_DEMO_BASE = "demo-base-enterprise";
export const INITIAL_DEMO_BASES: DemoRow[] = [
  {
    id: DEFAULT_DEMO_BASE,
    name: "企业知识库",
    description: "设备、质量与采购资料的演示知识库",
    enabled: true,
    values: { visibility: "enterprise" },
  },
];
export const INITIAL_DEMO_NOTES: DemoRow[] = [
  {
    id: "demo-note-reading",
    name: "阅读说明.md",
    description: "本地可编辑的知识资料示例",
    values: {
      baseId: DEFAULT_DEMO_BASE,
      folder: "示例资料",
      content:
        "# 阅读说明\n\n这份文本只存在于浏览器演示中。\n可编辑资料名称、目录、正文与可见范围。",
      scope: "enterprise",
    },
  },
];
export function noteDocument(
  note: DemoRow,
  copy: PreviewCopy,
): PreviewDocument {
  const scope = note.values?.scope;
  return {
    id: note.id,
    baseId: String(note.values?.baseId || DEFAULT_DEMO_BASE),
    name: note.name,
    kind: note.name.toLowerCase().endsWith(".md") ? "MD" : "TXT",
    source: "platform",
    status: "ready",
    scope:
      scope === "personal" || scope === "department" ? scope : "enterprise",
    owner: copy.sampleUser,
    sizeLabel: `${
      encoder.encode(String(note.values?.content || "")).byteLength
    } B`,
    updatedLabel: "2026.10.25",
    fingerprint: copy.browserLocalOnly,
    excerpt: String(note.values?.content || ""),
  };
}
