/**
 * Human label for a knowledge preview block's source locator.
 *
 * Locators come from the parser and are intentionally format-specific:
 * `{page}`, `{line}`, `{slide}`, `{sheet,row}`, `{table,row}`. A block whose
 * parser could not map a position (`{scope:"document"}`) gets no label rather
 * than an invented one.
 */
export type KnowledgeLocator = Record<string, unknown>;

type Translate = (key: string, options?: Record<string, unknown>) => string;

function positiveInt(value: unknown): number | null {
  const parsed = typeof value === "number" ? value : Number(value);
  return Number.isInteger(parsed) && parsed > 0 ? parsed : null;
}

export function knowledgeLocatorLabel(
  locator: KnowledgeLocator | undefined,
  t: Translate,
): string {
  if (!locator) return "";
  const line = positiveInt(locator.line);
  const page = positiveInt(locator.page);
  const slide = positiveInt(locator.slide);
  const row = positiveInt(locator.row);
  const table = positiveInt(locator.table);
  const paragraph = positiveInt(locator.paragraph);
  const entity =
    typeof locator.entity === "string" ? locator.entity.trim() : "";
  const handle =
    typeof locator.handle === "string" || typeof locator.handle === "number"
      ? String(locator.handle).trim()
      : "";
  const sheet = typeof locator.sheet === "string" ? locator.sheet.trim() : "";

  if (entity) {
    return handle
      ? t("chat.citationLocator.cadEntity", { entity, handle })
      : entity;
  }
  if (page !== null) {
    const label = t("chat.citationLocator.page", { n: page });
    return locator.image_ocr ? `${label} · OCR` : label;
  }
  if (sheet && row !== null) {
    return t("chat.citationLocator.sheetRow", { sheet, n: row });
  }
  if (table !== null && row !== null) {
    return t("chat.citationLocator.tableRow", { table, n: row });
  }
  if (slide !== null) return t("chat.citationLocator.slide", { n: slide });
  if (paragraph !== null)
    return t("chat.citationLocator.paragraph", { n: paragraph });
  if (line !== null) return t("chat.citationLocator.line", { n: line });
  if (row !== null) return t("chat.citationLocator.row", { n: row });
  return "";
}
