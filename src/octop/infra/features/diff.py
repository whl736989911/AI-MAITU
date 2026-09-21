"""Paragraph-level diff between an AI draft and the human-approved final text.

Pure functions, no IO: called from the finalize endpoint and, in bulk, over a
feature's whole history when rules are induced from past corrections.

Segments are lines compared *after* trimming, and blank lines are ignored:
character-level diffs drown the real edit in "add a comma" noise, and a
whitespace-only change is formatting, not intent — it must not become learning
signal. That is also why a document that changed only in whitespace has no diff
at all (``[]``), the same as an untouched one: both are strong positive samples.
"""

from __future__ import annotations

# Cell budget for the LCS table. A real edit is a handful of segments in a long
# document, which the prefix/suffix trim already reduces to almost nothing; this
# guard only covers the pathological "every line changed" paste, where the full
# quadratic table would allocate megabytes per request.
_MAX_LCS_CELLS = 250_000


def diff_segments(draft: str, final: str) -> list[dict[str, str]]:
    """Return the paragraph-level edit script from *draft* to *final*.

    Ops are ``keep`` (``text``), ``remove`` (``text``), ``add`` (``text``) and
    ``replace`` (``draft``/``final``). When the two texts hold the same
    segments — including an untouched draft, or one whose only changes are
    whitespace — the result is ``[]``.

    The script is lossless in outline: applying it to *draft* yields *final*'s
    segments and vice versa. Segment text is trimmed, so the caller's stored
    ``final`` column stays the only byte-exact copy.
    """
    draft_lines = _segments(draft)
    final_lines = _segments(final)
    if draft_lines == final_lines:
        return []

    # Unchanged head/tail never needs the quadratic table: one edit in a long
    # document shrinks to the edited neighbourhood.
    prefix = 0
    while (
        prefix < len(draft_lines)
        and prefix < len(final_lines)
        and draft_lines[prefix] == final_lines[prefix]
    ):
        prefix += 1
    suffix = 0
    while (
        suffix < len(draft_lines) - prefix
        and suffix < len(final_lines) - prefix
        and draft_lines[-1 - suffix] == final_lines[-1 - suffix]
    ):
        suffix += 1

    draft_mid = draft_lines[prefix : len(draft_lines) - suffix]
    final_mid = final_lines[prefix : len(final_lines) - suffix]
    if len(draft_mid) * len(final_mid) > _MAX_LCS_CELLS:
        changes = _as_changes(draft_mid, final_mid)
    else:
        changes = _pair_replacements(_align(draft_mid, final_mid))

    return (
        [{"op": "keep", "text": text} for text in draft_lines[:prefix]]
        + changes
        + [{"op": "keep", "text": text} for text in draft_lines[len(draft_lines) - suffix :]]
    )


def _segments(text: str) -> list[str]:
    """Comparable segments: line breaks normalized, blank lines and edge blanks out."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    return [stripped for line in normalized.split("\n") if (stripped := line.strip())]


def _align(draft: list[str], final: list[str]) -> list[dict[str, str]]:
    """Longest-common-subsequence alignment as keep/remove/add ops."""
    rows = len(draft)
    cols = len(final)
    # table[i][j] = LCS length of draft[i:] and final[j:], filled from the end
    # so the walk below reads forward and keeps emit in document order.
    table = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows - 1, -1, -1):
        row = table[i]
        below = table[i + 1]
        for j in range(cols - 1, -1, -1):
            if draft[i] == final[j]:
                row[j] = below[j + 1] + 1
            else:
                row[j] = below[j] if below[j] >= row[j + 1] else row[j + 1]

    ops: list[dict[str, str]] = []
    i = j = 0
    while i < rows and j < cols:
        if draft[i] == final[j]:
            ops.append({"op": "keep", "text": final[j]})
            i += 1
            j += 1
        elif table[i + 1][j] >= table[i][j + 1]:
            ops.append({"op": "remove", "text": draft[i]})
            i += 1
        else:
            ops.append({"op": "add", "text": final[j]})
            j += 1
    ops.extend({"op": "remove", "text": text} for text in draft[i:])
    ops.extend({"op": "add", "text": text} for text in final[j:])
    return ops


def _as_changes(draft: list[str], final: list[str]) -> list[dict[str, str]]:
    """LCS-free fallback: everything on both sides, paired positionally."""
    ops = [{"op": "remove", "text": text} for text in draft]
    ops.extend({"op": "add", "text": text} for text in final)
    return _pair_replacements(ops)


def _pair_replacements(ops: list[dict[str, str]]) -> list[dict[str, str]]:
    """Fold a run of removes plus adds into ``replace`` pairs.

    A human edit reads as "this segment became that segment", which is the shape
    rule induction consumes; whatever cannot be paired stays remove/add.
    """
    out: list[dict[str, str]] = []
    removed: list[str] = []
    added: list[str] = []

    def flush() -> None:
        paired = min(len(removed), len(added))
        for index in range(paired):
            out.append({"op": "replace", "draft": removed[index], "final": added[index]})
        out.extend({"op": "remove", "text": text} for text in removed[paired:])
        out.extend({"op": "add", "text": text} for text in added[paired:])
        removed.clear()
        added.clear()

    for op in ops:
        kind = op["op"]
        if kind == "keep":
            flush()
            out.append(op)
        elif kind == "remove":
            removed.append(op["text"])
        else:
            added.append(op["text"])
    flush()
    return out
