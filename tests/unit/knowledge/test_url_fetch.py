"""Unit tests for turning a fetched HTTP response into a knowledge document.

No network here: :func:`document_from_response` is the pure half of the URL
ingest path (type mapping, encoding, filename), so a served page can be
checked against the type table uploads already use.
"""

from __future__ import annotations

import pytest

from octop.infra.knowledge.url_fetch import UrlFetchError, document_from_response

_PAGE = "<html><body><h1>Refund policy</h1><p>Five business days.</p></body></html>"


def test_html_response_is_stored_as_an_html_document() -> None:
    fetched = document_from_response(
        url="https://example.com/handbook",
        name="Handbook",
        content_type_header="text/html; charset=utf-8",
        body=_PAGE.encode("utf-8"),
    )

    assert fetched.filename == "Handbook.html"
    assert fetched.content_type == "text/html"
    assert b"Refund policy" in fetched.content
    assert fetched.final_url == "https://example.com/handbook"


def test_declared_charset_is_transcoded_to_utf8() -> None:
    """A GBK page must land in the base as UTF-8, not as mojibake."""
    fetched = document_from_response(
        url="https://example.com/zh",
        name="Zh",
        content_type_header="text/html; charset=gbk",
        body="<p>知识库退款</p>".encode("gbk"),
    )

    assert fetched.content == "<p>知识库退款</p>".encode()


def test_meta_charset_is_used_when_the_header_declares_none() -> None:
    body = '<html><head><meta charset="gbk"></head><body><p>知识库</p></body></html>'

    fetched = document_from_response(
        url="https://example.com/zh",
        name="Zh",
        content_type_header="text/html",
        body=body.encode("gbk"),
    )

    assert fetched.content.decode("utf-8") == body


def test_octet_stream_falls_back_to_the_url_suffix() -> None:
    fetched = document_from_response(
        url="https://example.com/docs/notes.md",
        name="Notes",
        content_type_header="application/octet-stream",
        body=b"# Notes\n",
    )

    assert fetched.filename == "Notes.md"
    assert fetched.content_type == "text/markdown"


def test_pdf_bytes_are_stored_verbatim() -> None:
    body = b"%PDF-1.7\n\xff\xfe\x00binary"

    fetched = document_from_response(
        url="https://example.com/paper.pdf",
        name="Paper",
        content_type_header="application/pdf",
        body=body,
    )

    assert fetched.filename == "Paper.pdf"
    assert fetched.content_type == "application/pdf"
    assert fetched.content == body


@pytest.mark.parametrize(
    ("content_type_header", "url"),
    [
        ("application/zip", "https://example.com/archive.zip"),
        ("image/svg+xml", "https://example.com/logo.svg"),
        ("", "https://example.com/"),
        ("application/octet-stream", "https://example.com/"),
    ],
)
def test_types_the_knowledge_base_cannot_ingest_are_refused(
    content_type_header: str, url: str
) -> None:
    with pytest.raises(UrlFetchError) as raised:
        document_from_response(
            url=url,
            name="X",
            content_type_header=content_type_header,
            body=b"x",
        )

    assert raised.value.args[0]


def test_the_source_name_becomes_one_safe_filename() -> None:
    """A name with separators must not turn into nested folders."""
    fetched = document_from_response(
        url="https://example.com/",
        name="../ACME/Handbook",
        content_type_header="text/html",
        body=_PAGE.encode("utf-8"),
    )

    assert "/" not in fetched.filename
    assert "\\" not in fetched.filename
    assert fetched.filename.endswith(".html")
