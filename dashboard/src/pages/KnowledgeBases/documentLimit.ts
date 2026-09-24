export function effectiveDocumentLimit(
  maxDocuments: number | null | undefined,
  fallback: number,
): number | null {
  const limit = maxDocuments ?? fallback;
  return limit === 0 ? null : limit;
}
