"""Text extraction for the document types accepted by knowledge bases."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.knowledge.legacy_office import LEGACY_OFFICE_SUFFIXES, converted_copy
from octop.infra.knowledge.ocr import OCR_IMAGE_SUFFIXES

if TYPE_CHECKING:
    from octop.infra.knowledge.ocr import OcrExtractor

_PLAIN_TEXT_SUFFIXES = {
    ".md",
    ".markdown",
    ".txt",
    ".rst",
    ".yaml",
    ".yml",
    ".jsonl",
}
_OOXML_SUFFIXES = frozenset({".docx", ".xlsx", ".xlsm", ".pptx"})
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DOCX_FALLBACK_TAG = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_DOCX_HTML_TYPES = {"application/xhtml+xml", "text/html"}
_DOCX_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_COMMENTS_PART = "word/comments.xml"


class PasswordRequiredError(ValueError):
    """The document is encrypted and cannot be read without a password."""


def failure_status(exc: BaseException) -> str:
    """The document status a failed parse earns (design §6.1, §8.2).

    One mapping for both index paths — an uploaded file and a file found in a
    source — so a locked document reads the same whichever way it arrived, and
    ``password_required`` exists as a state instead of being flattened into
    ``failed`` (which §14 asks to keep distinguishable).
    """
    return "password_required" if isinstance(exc, PasswordRequiredError) else "failed"


def parse_document(path: Path, *, ocr: OcrExtractor | None = None) -> str:
    """Extract searchable text from a supported local document.

    A password-protected document raises :class:`PasswordRequiredError`: that is
    a state of the file rather than a failure of the platform, and an
    administrator can act on it once it is named.
    """
    suffix = path.suffix.lower()
    if _needs_password(path, suffix):
        # Deliberately no file name in the message: a source's file is parsed
        # from a staged copy, so ``path.name`` is a temporary name, and the row
        # this reason lands on already shows which file it is.
        raise PasswordRequiredError("the file is password-protected")
    if suffix in LEGACY_OFFICE_SUFFIXES:
        # design §6.1: convert to the XML format first, in a temporary directory,
        # and parse that. The original file is never modified.
        with converted_copy(path) as converted:
            return parse_document(converted, ocr=ocr)
    if suffix in OCR_IMAGE_SUFFIXES:
        if ocr is None:
            raise RuntimeError("knowledge OCR is not enabled")
        return ocr(path)
    if suffix in _PLAIN_TEXT_SUFFIXES:
        return _read_text(path)
    if suffix == ".json":
        return _parse_json(path)
    if suffix == ".xml":
        return _parse_xml(path)
    if suffix in {".html", ".htm"}:
        return _parse_html(path)
    if suffix == ".csv":
        return _parse_delimited(path, delimiter=",")
    if suffix == ".tsv":
        return _parse_delimited(path, delimiter="\t")
    if suffix == ".pdf":
        from pypdf import PdfReader

        text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages)
        if text.strip() or ocr is None:
            return text
        return ocr(path)
    if suffix == ".docx":
        return _parse_docx(path)
    if suffix == ".pptx":
        return _parse_pptx(path)
    if suffix in {".xlsx", ".xlsm"}:
        return _parse_xlsx(path)
    if suffix == ".xls":
        return _parse_xls(path)
    raise ValueError(f"unsupported knowledge document extension: {suffix or '(none)'}")


def _needs_password(path: Path, suffix: str) -> bool:
    """Whether a file is encrypted, read from the format's own marker.

    An encrypted OOXML document is not a ZIP at all — the container is an OLE
    compound file — so the magic bytes answer exactly. A PDF states it in its
    trailer, which ``pypdf`` reads.

    The binary formats (``.doc``/``.ppt``) are OLE compound files whether or not
    they are encrypted, so their magic says nothing here; there the conversion
    fails and LibreOffice's own reason is what an administrator reads. Reaching
    for a heuristic that guesses would put a wrong state on the row, which is
    worse than the honest failure.
    """
    if suffix in _OOXML_SUFFIXES:
        try:
            with path.open("rb") as handle:
                return handle.read(len(_CFB_MAGIC)) == _CFB_MAGIC
        except OSError:
            return False
    if suffix == ".pdf":
        from pypdf import PdfReader

        try:
            return bool(PdfReader(path).is_encrypted)
        except Exception:
            # A damaged PDF is not a locked one: let the parser report that.
            return False
    return False


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig", errors="replace")


def _parse_json(path: Path) -> str:
    raw = _read_text(path)
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    return json.dumps(parsed, ensure_ascii=False, indent=2)


def _xml_root(source: Path | bytes) -> Any | None:
    """The parsed root element, or ``None`` when the XML cannot be read."""
    import xml.etree.ElementTree as ElementTree

    try:
        if isinstance(source, Path):
            return ElementTree.parse(source).getroot()
        return ElementTree.fromstring(source)
    except ElementTree.ParseError:
        return None


def _parse_xml(path: Path) -> str:
    """XML keeps its field paths (design §6): ``/order/item@sku = A-1``.

    The design asks for "field paths and values" rather than the markup itself,
    so a search for a field name and a search for its value both land while the
    tags and namespace prefixes a raw dump would match on do not. Markup this
    cannot parse falls back to the raw text, because a file that is XML-shaped
    but not well-formed is still worth indexing.
    """
    root = _xml_root(path)
    if root is None:
        return _read_text(path)
    lines: list[str] = []
    _walk_xml(root, f"/{_local_name(root.tag)}", lines)
    return "\n".join(lines)


def _walk_xml(element: Any, path: str, lines: list[str]) -> None:
    for key, value in element.attrib.items():
        lines.append(f"{path}@{_local_name(key)} = {value.strip()}")
    text = " ".join((element.text or "").split())
    if text:
        lines.append(f"{path} = {text}")
    for child in element:
        _walk_xml(child, f"{path}/{_local_name(child.tag)}", lines)


def _local_name(tag: object) -> str:
    """A tag's local name, dropping the ``{namespace}`` ElementTree keeps."""
    name = str(tag)
    if name.startswith("{"):
        return name.partition("}")[2]
    return name


def _parse_docx(path: Path) -> str:
    from docx import Document

    document = Document(str(path))
    body = _docx_body_text(document.element.body, document.part)
    comments = _docx_comment_text(path)
    return body if not comments else f"{body}\n# Comments\n{comments}"


def _docx_comment_text(path: Path) -> str:
    """The text of every ``w:comment``, which ``python-docx`` does not expose.

    design §6 lists a DOCX's comments ("备注") as part of what it yields. They
    live in their own package part, so they are read from the archive rather
    than through the object model.
    """
    import zipfile

    try:
        with zipfile.ZipFile(path) as archive:
            raw = archive.read(_DOCX_COMMENTS_PART)
    except (KeyError, OSError, zipfile.BadZipFile):
        return ""
    root = _xml_root(raw)
    if root is None:
        return ""
    from docx.oxml.ns import qn

    lines: list[str] = []
    for comment in root.iter(qn("w:comment")):
        text = "".join(node.text or "" for node in comment.iter(qn("w:t"))).strip()
        if text:
            lines.append(text)
    return "\n".join(lines)


def _parse_pptx(path: Path) -> str:
    """Slide text, table cells and speaker notes (design §6), one block per slide.

    ``slide.notes_slide`` *creates* a notes part when the file has none, which
    would edit the presentation being read; ``has_notes_slide`` answers without
    that side effect, so a deck without notes is left exactly as it was.
    """
    from pptx import Presentation

    parts: list[str] = []
    for index, slide in enumerate(Presentation(str(path)).slides, start=1):
        lines: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                rows = _pptx_table_text(shape.table)
                if rows:
                    lines.append(rows)
            elif getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
        notes = _pptx_notes(slide)
        if notes:
            lines.append(f"# Notes\n{notes}")
        if lines:
            parts.append(f"# Slide {index}\n" + "\n".join(lines))
    return "\n\n".join(parts)


def _pptx_table_text(table: Any) -> str:
    rows = [[_stringify_cell(cell.text) for cell in row.cells] for row in table.rows]
    return "\n".join("\t".join(cells) for cells in map(_trim_row, rows) if cells)


def _pptx_notes(slide: Any) -> str:
    if not slide.has_notes_slide:
        return ""
    return str(slide.notes_slide.notes_text_frame.text).strip()


def _docx_body_text(body: Any, part: Any) -> str:
    """Collect every ``w:p`` and ``w:altChunk`` in a body, in document order.

    ``Document.paragraphs`` only lists ``w:p`` children of ``w:body``, so text inside
    tables, content controls (``w:sdt``), revision wrappers (``w:ins``), and text boxes
    is silently dropped. ``mc:Fallback`` duplicates its ``mc:Choice`` sibling, so its
    paragraphs are skipped.
    """
    from docx.oxml.ns import qn

    alt_chunk_tag = qn("w:altChunk")
    lines: list[str] = []
    for element in body.iter(qn("w:p"), alt_chunk_tag):
        if next(element.iterancestors(_DOCX_FALLBACK_TAG), None) is not None:
            continue
        if element.tag == alt_chunk_tag:
            lines.append(_docx_alt_chunk_text(element, part))
        else:
            lines.append(element.text)
    return "\n".join(lines)


def _docx_alt_chunk_text(element: Any, part: Any) -> str:
    """Expand a ``w:altChunk`` reference the way Word does when opening the file.

    Converters keep the bulk of the text in an embedded HTML or Word part and leave
    only a stub in ``document.xml``; unexpanded, that body is lost.
    """
    from docx.oxml.ns import qn

    chunk = part.related_parts.get(element.get(qn("r:id")) or "")
    if chunk is None:
        return ""
    content_type = str(chunk.content_type)
    blob: bytes = chunk.blob
    if content_type in _DOCX_HTML_TYPES:
        return _html_text(blob.decode("utf-8-sig", errors="replace"))
    if content_type == "text/plain":
        return blob.decode("utf-8-sig", errors="replace")
    if content_type == _DOCX_MAIN_TYPE:
        from docx import Document

        nested = Document(io.BytesIO(blob))
        return _docx_body_text(nested.element.body, nested.part)
    return ""


def _parse_html(path: Path) -> str:
    return _html_text(_read_text(path))


def _html_text(raw: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(raw)
    parser.close()
    lines = [" ".join(line.split()) for line in parser.text().splitlines()]
    return "\n".join(line for line in lines if line)


class _HTMLTextParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template"})
    _BLOCK = frozenset(
        {
            "p",
            "div",
            "br",
            "li",
            "tr",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "article",
            "section",
            "header",
            "footer",
            "blockquote",
            "pre",
            "table",
            "ul",
            "ol",
        }
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
            return
        if not self._skip and tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            if self._skip:
                self._skip -= 1
            return
        if not self._skip and tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip and data:
            self._parts.append(data)

    def text(self) -> str:
        return "".join(self._parts)


def _stringify_cell(value: object) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, float) and value.is_integer():
        return str(int(value))
    return str(value).replace("\t", " ").replace("\n", " ").strip()


def _trim_row(cells: list[str]) -> list[str]:
    while cells and cells[-1] == "":
        cells.pop()
    return cells


def _sheet_text(title: str, rows: Iterable[Iterable[object]]) -> str:
    lines = [f"# {title}"]
    has_cells = False
    for raw in rows:
        cells = _trim_row([_stringify_cell(cell) for cell in raw])
        if not cells:
            continue
        has_cells = True
        lines.append("\t".join(cells))
    if not has_cells:
        return ""
    return "\n".join(lines)


def _parse_delimited(path: Path, *, delimiter: str) -> str:
    reader = csv.reader(_read_text(path).splitlines(), delimiter=delimiter)
    return _sheet_text(path.stem, reader)


def _parse_xlsx(path: Path) -> str:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        parts: list[str] = []
        for sheet in workbook.worksheets:
            text = _sheet_text(str(sheet.title), sheet.iter_rows(values_only=True))
            if text:
                parts.append(text)
        return "\n\n".join(parts)
    finally:
        workbook.close()


def _parse_xls(path: Path) -> str:
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), formatting_info=False)
    except Exception as exc:
        if _mentions_password(exc):
            raise PasswordRequiredError("the file is password-protected") from exc
        raise
    parts: list[str] = []
    for sheet in book.sheets():
        rows: list[list[object]] = []
        for row_idx in range(sheet.nrows):
            rows.append(
                [
                    _xlrd_cell_value(book, sheet.cell(row_idx, col_idx))
                    for col_idx in range(sheet.ncols)
                ]
            )
        text = _sheet_text(sheet.name, rows)
        if text:
            parts.append(text)
    return "\n\n".join(parts)


def _mentions_password(exc: BaseException) -> bool:
    """Whether a reader's complaint is about encryption rather than damage.

    ``xlrd`` is the one reader that reports an encrypted legacy workbook at all
    (``Workbook is encrypted``), and for that format its message is the only
    signal there is, so the text is what decides. Wrapping the exception rather
    than swallowing it keeps the reader's own words as the cause.
    """
    text = str(exc).lower()
    return "password" in text or "encrypt" in text


def _xlrd_cell_value(book: object, cell: object) -> object:
    import xlrd

    ctype = getattr(cell, "ctype", xlrd.XL_CELL_EMPTY)
    value = getattr(cell, "value", None)
    if ctype in {xlrd.XL_CELL_EMPTY, xlrd.XL_CELL_BLANK, xlrd.XL_CELL_ERROR}:
        return None
    if ctype == xlrd.XL_CELL_DATE:
        datemode = int(getattr(book, "datemode", 0))
        try:
            return xlrd.xldate_as_datetime(value, datemode).isoformat(sep=" ", timespec="seconds")
        except (OSError, OverflowError, TypeError, ValueError):
            return value
    return value
