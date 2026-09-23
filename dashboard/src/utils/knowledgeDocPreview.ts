/**
 * Pure helpers for knowledge-document preview / download affordances.
 * Shared by Knowledge Bases and Chat citation preview (no page imports).
 */

export function isEditableKnowledgeDocument(doc: {
  is_dir?: boolean;
  content_type?: string;
  filename?: string;
}): boolean {
  if (doc.is_dir) return false;
  const ct = (doc.content_type || "").toLowerCase();
  if (ct === "text/plain" || ct === "text/markdown") return true;
  const name = (doc.filename || "").toLowerCase();
  return name.endsWith(".md") || name.endsWith(".txt");
}

/**
 * Show download when the on-disk file is still available (upload or
 * in-app note). Missing originals (deleted from disk) stay hidden.
 */
export function canDownloadKnowledgeOriginal(doc: {
  is_dir?: boolean;
  has_original?: boolean;
  content_type?: string;
  filename?: string;
}): boolean {
  if (doc.is_dir) return false;
  if (doc.has_original === false) return false;
  return true;
}

/** Markdown files get rendered preview (not a raw ``<pre>`` dump). */
export function isKnowledgeMarkdownDocument(doc: {
  is_dir?: boolean;
  content_type?: string;
  filename?: string;
}): boolean {
  if (doc.is_dir) return false;
  const ct = (doc.content_type || "").toLowerCase();
  if (ct === "text/markdown") return true;
  const name = (doc.filename || "").toLowerCase();
  return name.endsWith(".md") || name.endsWith(".markdown");
}

function knowledgeDocumentExt(filename: string | undefined): string {
  const name = (filename || "").toLowerCase();
  const dot = name.lastIndexOf(".");
  return dot >= 0 ? name.slice(dot + 1) : "";
}

/** Original-file rich viewers (DocumentPreviewCore), keyed by extension.
 *
 * Membership is asked with ``Object.hasOwn`` rather than a truthiness test:
 * the keys come from a filename, so ``report.constructor`` must not match a
 * prototype member.
 */
const RICH_PREVIEW_EXTS: Record<string, true> = {
  pdf: true,
  docx: true,
  pptx: true,
  xls: true,
  xlsx: true,
  xlsm: true,
};

/** UTF-8 / extracted-text preview (Markdown or ``<pre>``). */
const TEXT_PREVIEW_EXTS: Record<string, true> = {
  md: true,
  markdown: true,
  txt: true,
  rst: true,
  html: true,
  htm: true,
  json: true,
  jsonl: true,
  xml: true,
  yaml: true,
  yml: true,
  csv: true,
  tsv: true,
  // Legacy PowerPoint: no in-browser slide renderer — use extracted text.
  ppt: true,
  // Legacy Word is a binary container, not a ZIP, so docx-preview cannot read
  // it; the server converts and extracts it instead (§6.1).
  doc: true,
};

/** Whether the Eye action should be enabled for this knowledge document. */
export function canPreviewKnowledgeDocument(doc: {
  is_dir?: boolean;
  content_type?: string;
  filename?: string;
  has_original?: boolean;
}): boolean {
  if (doc.is_dir) return false;
  if (isKnowledgeMarkdownDocument(doc) || isEditableKnowledgeDocument(doc)) {
    return true;
  }
  const ext = knowledgeDocumentExt(doc.filename);
  if (Object.hasOwn(TEXT_PREVIEW_EXTS, ext)) return true;
  if (Object.hasOwn(RICH_PREVIEW_EXTS, ext)) return true;
  return false;
}

/**
 * Open the original file in DocumentPreviewCore (PDF / DOCX / PPTX / Excel).
 * When false, preview falls back to extracted / UTF-8 text.
 *
 * Omit ``has_original`` (or leave undefined) when unknown — e.g. chat
 * citations — so extension alone can attempt rich preview with a 404 fallback.
 */
export function canRichPreviewKnowledgeDocument(doc: {
  filename?: string;
  has_original?: boolean;
}): boolean {
  if (doc.has_original === false) return false;
  return Object.hasOwn(RICH_PREVIEW_EXTS, knowledgeDocumentExt(doc.filename));
}

/** Extension-only rich gate (unknown original); same set as rich preview. */
export function isRichPreviewFilename(filename: string | undefined): boolean {
  return Object.hasOwn(RICH_PREVIEW_EXTS, knowledgeDocumentExt(filename));
}
