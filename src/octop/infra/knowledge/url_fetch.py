"""A fetched URL as a knowledge document — guarded download, type mapping, encoding.

The URL ingest path is deliberately thin: it turns one HTTP response into the
byte-plus-type pair :meth:`KnowledgeService.upload_document` already accepts,
then hands off to ``knowledge.jobs.process_document`` for parse → chunk → embed
→ index. Nothing here re-implements that pipeline.

Two decisions shape the module:

- **The served type decides the stored document, the table decides the types.**
  ``Content-Type`` is mapped through the same extension table uploads use, so a
  URL source can never admit a document type an upload would reject. A response
  with no usable type falls back to the URL's suffix; anything else is refused
  by name instead of being stored as an unparsable blob.
- **Text is stored as UTF-8.** A page's declared charset (HTTP header, else the
  document's own ``<meta>``) is honoured before the bytes reach the parser,
  because the parser reads UTF-8 — a GBK page would otherwise land in the index
  as mojibake that looks like content.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse

from octop.infra.knowledge.service import (
    knowledge_content_type,
    knowledge_suffix_for_content_type,
)
from octop.infra.utils.ssrf_guard import safe_get

# Types served as text are transcoded to UTF-8; everything else is stored as
# the bytes that arrived (PDF, Office, images).
_TEXTUAL_CONTENT_TYPES = frozenset(
    {
        "text/plain",
        "text/markdown",
        "text/html",
        "text/x-rst",
        "text/csv",
        "text/tab-separated-values",
        "application/json",
        "application/jsonl",
        "application/yaml",
    }
)
# A server that declares none of these leaves the type to the URL's suffix.
_UNDECLARED_CONTENT_TYPES = frozenset({"", "application/octet-stream", "binary/octet-stream"})
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-:.]+)", re.IGNORECASE)
_META_SCAN_BYTES = 4096
_MAX_STEM_CHARS = 120
_ILLEGAL_STEM_CHARS = re.compile(r'[\\/:*?"<>|\x00-\x1f]')


class UrlFetchError(RuntimeError):
    """The URL was fetched, and what it served cannot become a document."""


@dataclass(frozen=True)
class FetchedDocument:
    """One URL ready to be stored as a knowledge document."""

    filename: str
    content_type: str
    content: bytes
    final_url: str


def fetch_document(
    url: str,
    *,
    name: str,
    max_bytes: int,
    timeout: float = 30.0,
) -> FetchedDocument:
    """Fetch *url* through the SSRF guard and describe it as a document.

    Raises :class:`~octop.infra.utils.ssrf_guard.UnsafeOutboundUrl` when a hop
    is not a public https target, :class:`~octop.infra.utils.ssrf_guard.
    OutboundFetchError` when the fetch itself failed, and :class:`UrlFetchError`
    when the response is not a knowledge document.
    """
    response = safe_get(
        url,
        max_bytes=max_bytes,
        timeout=timeout,
        headers={
            "user-agent": "MAITU Smart Manufacturing-Knowledge/1.0 (knowledge-base data source)",
            "accept": "text/html,application/xhtml+xml,text/plain,application/pdf;q=0.9,*/*;q=0.8",
        },
    )
    return document_from_response(
        url=response.final_url,
        name=name,
        content_type_header=response.content_type,
        body=response.content,
    )


def document_from_response(
    *,
    url: str,
    name: str,
    content_type_header: str,
    body: bytes,
) -> FetchedDocument:
    """Map one response onto the filename, type, and bytes to store."""
    served = (content_type_header or "").split(";")[0].strip().lower()
    suffix = knowledge_suffix_for_content_type(served)
    if suffix is None and served in _UNDECLARED_CONTENT_TYPES:
        # No usable header: only a URL that names a supported type is stored.
        url_suffix = Path(urlparse(url).path).suffix.lower()
        if knowledge_content_type(url_suffix) is not None:
            suffix = url_suffix
    if suffix is None:
        raise UrlFetchError(
            f"{url} served {served or 'no content type'}, which a knowledge base cannot ingest"
        )
    content_type = knowledge_content_type(suffix)
    if content_type is None:  # pragma: no cover - the table cannot disagree with itself
        raise UrlFetchError(f"{url} served {served}, which a knowledge base cannot ingest")
    content = (
        _to_utf8(body, content_type_header=content_type_header)
        if content_type in _TEXTUAL_CONTENT_TYPES
        else body
    )
    return FetchedDocument(
        filename=f"{_safe_stem(name)}{suffix}",
        content_type=content_type,
        content=content,
        final_url=url,
    )


def _to_utf8(body: bytes, *, content_type_header: str) -> bytes:
    """Re-encode text with its declared charset, so the parser reads UTF-8."""
    charset = _declared_charset(content_type_header) or _meta_charset(body) or "utf-8"
    try:
        return body.decode(charset, errors="replace").encode("utf-8")
    except LookupError:
        # An unreadable charset name is not a reason to drop the page.
        return body.decode("utf-8", errors="replace").encode("utf-8")


def _declared_charset(content_type_header: str) -> str | None:
    match = _CHARSET_RE.search(content_type_header or "")
    return match.group(1) if match else None


def _meta_charset(body: bytes) -> str | None:
    """The charset a text document declares about itself (``<meta charset=…>``)."""
    # latin-1 never fails, and a charset name in a <meta> tag is ASCII.
    match = _CHARSET_RE.search(body[:_META_SCAN_BYTES].decode("latin-1", errors="replace"))
    return match.group(1) if match else None


def _safe_stem(name: str) -> str:
    """One filename-safe stem from a data source name."""
    stem = _ILLEGAL_STEM_CHARS.sub("-", (name or "").strip()).strip(". ")
    return (stem or "Source")[:_MAX_STEM_CHARS]
