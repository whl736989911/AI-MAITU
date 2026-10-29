import type { KnowledgeCitation } from "./parseKnowledgeCitations";

/** Markdown link scheme that marks an in-answer reference to a knowledge file. */
export const KNOWLEDGE_LINK_PREFIX = "octop-kb://";

export function knowledgeLinkHref(citation: KnowledgeCitation): string {
  return `${KNOWLEDGE_LINK_PREFIX}${encodeURIComponent(
    citation.kbId,
  )}/${encodeURIComponent(citation.docId)}`;
}

function escapeLinkLabel(name: string): string {
  return name.replace(/[[\]]/g, (bracket) => `\\${bracket}`);
}

/**
 * Turn file names the answer already mentions into links to the knowledge file.
 *
 * The answer text is authoritative: a cited document that the model never names
 * stays unlinked instead of gaining an invented mention. Names are swapped for
 * placeholders first, so a shorter name inside a longer one (套管四通 inside
 * 底层套管四通 202604042.dwg) can never split the longer link.
 */
export function linkifyKnowledgeCitations(
  text: string,
  citations: KnowledgeCitation[],
): string {
  if (!text || citations.length === 0) return text;

  const links: string[] = [];
  let out = text;
  const ordered = [...citations]
    .filter((citation) => citation.filename && citation.kbId && citation.docId)
    .sort((a, b) => b.filename.length - a.filename.length);

  for (const citation of ordered) {
    const href = knowledgeLinkHref(citation);
    if (out.includes(`](${href})`)) continue;
    const name = citation.filename;
    // Answers often quote file names as inline code; link the whole span so the
    // markdown is not left inside backticks, where it renders as literal text.
    const quoted = `\`${name}\``;
    const needle = out.includes(quoted)
      ? quoted
      : out.includes(name)
      ? name
      : null;
    if (!needle) continue;
    const token = `\uE000kb${links.length}\uE001`;
    out = out.split(needle).join(token);
    links.push(`[${escapeLinkLabel(name)}](${href})`);
  }

  if (links.length === 0) return text;
  return out.replace(/\uE000kb(\d+)\uE001/g, (_, index: string) => {
    return links[Number(index)] ?? "";
  });
}
