"""Text extraction for the document types accepted by knowledge bases."""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import TYPE_CHECKING, Any

from octop.infra.knowledge.legacy_office import LEGACY_OFFICE_SUFFIXES, converted_copy
from octop.infra.knowledge.ocr import OCR_IMAGE_SUFFIXES
from octop.infra.knowledge.relpath import path_basename

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
_MARKDOWN_SUFFIXES = frozenset({".md", ".markdown"})
_CFB_MAGIC = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"
_DOCX_FALLBACK_TAG = "{http://schemas.openxmlformats.org/markup-compatibility/2006}Fallback"
_DOCX_HTML_TYPES = {"application/xhtml+xml", "text/html"}
_DOCX_MAIN_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
_DOCX_COMMENTS_PART = "word/comments.xml"


class PasswordRequiredError(ValueError):
    """The document is encrypted and cannot be read without a password."""


PARSE_VERSION = "1"
"""Bumped when a parser's *output* changes (design §3.3 asks for it).

Stored with the derived content so a later build can tell which documents were
parsed by which parser, and therefore which ones a change invalidates.
"""


@dataclass(frozen=True)
class ParsedTable:
    """One table with its origin, so a row can be traced back (design §3.3).

    ``location`` is what the design's "保留表头、行列关系和工作表位置" asks for:
    the sheet a spreadsheet table came from, the slide a deck's table sits on,
    or a positional label for a Word table. ``header`` is the first row because
    that is what a reader assumes a table's first row is, and it is kept
    separate so a row can be read as a row rather than as a line of text.
    """

    location: str
    header: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True)
class ParsedDocument:
    """The unified document structure a parser yields (design §6.2).

    Every format lands here, so nothing downstream has to know which file it
    came from. ``text`` stays the flattened, searchable form — it is what gets
    chunked and embedded — while the other fields carry what the text cannot
    express: where a table came from, which headings the document declares, how
    many pages it has, what its own title is.

    A field a format cannot answer stays empty rather than guessed: ``pages`` is
    ``None`` for a Word file because its page count only exists once something
    lays it out, and an invented number would be worse than none.
    """

    text: str
    title: str = ""
    sections: tuple[str, ...] = ()
    tables: tuple[ParsedTable, ...] = ()
    pages: int | None = None
    source_path: str = ""
    filename: str = ""
    modified_at: str = ""
    parser_version: str = PARSE_VERSION

    def derived(self) -> dict[str, Any]:
        """The structure as it is stored on the row (design §3.4).

        ``text`` is deliberately absent: the chunk table already holds it, and
        this payload is the part the text cannot carry. Storing the text twice
        would double every document's footprint for no reader.
        """
        return {
            "parser_version": self.parser_version,
            "title": self.title,
            "sections": list(self.sections),
            "tables": [
                {
                    "location": t.location,
                    "header": list(t.header),
                    "rows": [list(r) for r in t.rows],
                }
                for t in self.tables
            ],
            "pages": self.pages,
            "source_path": self.source_path,
            "filename": self.filename,
            "modified_at": self.modified_at,
        }


def failure_status(exc: BaseException) -> str:
    """The document status a failed parse earns (design §6.1, §8.2).

    One mapping for both index paths — an uploaded file and a file found in a
    source — so a locked document reads the same whichever way it arrived, and
    ``password_required`` exists as a state instead of being flattened into
    ``failed`` (which §14 asks to keep distinguishable).
    """
    return "password_required" if isinstance(exc, PasswordRequiredError) else "failed"


def parse_document(
    path: Path, *, ocr: OcrExtractor | None = None, source_path: str = ""
) -> ParsedDocument:
    """Extract the unified document structure from a supported local file.

    *source_path* is the file's path as its source sees it. A scan parses a copy
    staged in a temporary directory, so without it the structure would name a
    temporary file; when it is absent the local path is the honest answer.

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
            return parse_document(converted, ocr=ocr, source_path=source_path)
    if suffix in OCR_IMAGE_SUFFIXES:
        if ocr is None:
            raise RuntimeError("knowledge OCR is not enabled")
        return _document(ocr(path), path, source_path)
    if suffix in _PLAIN_TEXT_SUFFIXES:
        text = _read_text(path)
        headings = _markdown_headings(text) if suffix in _MARKDOWN_SUFFIXES else ()
        return _document(text, path, source_path, sections=headings)
    if suffix == ".json":
        return _document(_parse_json(path), path, source_path)
    if suffix == ".xml":
        return _document(_parse_xml(path), path, source_path)
    if suffix in {".html", ".htm"}:
        return _html_document(path, source_path)
    if suffix in {".csv", ".tsv"}:
        return _delimited_document(path, source_path, delimiter="," if suffix == ".csv" else "\t")
    if suffix == ".pdf":
        return _pdf_document(path, source_path, ocr=ocr)
    if suffix == ".docx":
        return _docx_document(path, source_path)
    if suffix == ".pptx":
        return _pptx_document(path, source_path)
    if suffix in {".xlsx", ".xlsm"}:
        return _xlsx_document(path, source_path)
    if suffix == ".xls":
        return _xls_document(path, source_path)
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


_TEXT_ENCODINGS = ("utf-8-sig", "gb18030")


def _read_text(path: Path) -> str:
    """Decode text files as UTF-8 first, then GB18030 for legacy Chinese files."""
    data = path.read_bytes()
    for encoding in _TEXT_ENCODINGS:
        try:
            text = data.decode(encoding)
        except UnicodeDecodeError:
            continue
        return text.replace("\r\n", "\n").replace("\r", "\n")
    return data.decode("utf-8", errors="replace").replace("\r\n", "\n").replace("\r", "\n")


def _document(
    text: str,
    path: Path,
    source_path: str = "",
    *,
    title: str = "",
    sections: tuple[str, ...] = (),
    tables: tuple[ParsedTable, ...] = (),
    pages: int | None = None,
) -> ParsedDocument:
    """Wrap extracted text in the structure, with the file's own metadata.

    One constructor for every format, so the metadata block cannot drift apart
    between eleven parsers — and the title has one rule rather than eleven: what
    the document declares, else what its first heading says, else its own file
    name. A format that genuinely knows its title (a PDF's metadata, an HTML
    ``<title>``, a Word or Excel core property) passes it in and wins.

    ``modified_at`` is read from the file at parse time: a scan knows a file's
    mtime already, but a preview of an uploaded document does not, and the
    design puts it in the structure rather than in the caller.
    """
    name = path_basename(source_path) if source_path else path.name
    return ParsedDocument(
        text=text,
        title=title.strip() or (sections[0] if sections else "") or Path(name).stem,
        sections=sections,
        tables=tables,
        pages=pages,
        source_path=source_path,
        filename=name,
        modified_at=datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"),
    )


def _table(location: str, rows: Iterable[Iterable[object]]) -> ParsedTable | None:
    """One table from raw rows, or ``None`` when it holds no cells.

    Rows are the shape every table source can produce — a spreadsheet's rows, a
    Word table's cells, a CSV reader's output — so the trimming rules live here
    once instead of in each reader: trailing empty cells go, empty rows go, and
    the first surviving row is the header (which is what a reader assumes it is,
    and keeping it apart is what lets a row be read *as* a row).
    """
    kept: list[tuple[str, ...]] = []
    for raw in rows:
        cells = _trim_row([_stringify_cell(cell) for cell in raw])
        if cells:
            kept.append(tuple(cells))
    if not kept:
        return None
    return ParsedTable(location=location, header=kept[0], rows=tuple(kept[1:]))


def _table_text(table: ParsedTable) -> str:
    """The searchable form of one table: its location, then one line per row."""
    lines = [f"# {table.location}"]
    lines.extend("\t".join(cells) for cells in (table.header, *table.rows) if cells)
    return "\n".join(lines)


def _tables_text(tables: Iterable[ParsedTable]) -> str:
    return "\n\n".join(_table_text(table) for table in tables)


def _rows_text(rows: Iterable[Iterable[object]]) -> str:
    """Rows as text without a heading — for a table inside its own block."""
    return "\n".join(
        "\t".join(cells)
        for cells in (_trim_row([_stringify_cell(cell) for cell in row]) for row in rows)
        if cells
    )


def _markdown_headings(text: str) -> tuple[str, ...]:
    """ATX headings in order, ignoring fenced code blocks.

    A ``#`` inside a fence is a comment in whatever language the block holds,
    not a heading; reading it as one would put noise in the structure.
    """
    out: list[str] = []
    fenced = False
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fenced = not fenced
            continue
        if fenced or not stripped.startswith("#"):
            continue
        heading = stripped.lstrip("#").strip()
        if heading:
            out.append(heading)
    return tuple(out)


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


def _docx_document(path: Path, source_path: str) -> ParsedDocument:
    from docx import Document

    document = Document(str(path))
    body = document.element.body
    text = _docx_body_text(body, document.part)
    comments = _docx_comment_text(path)
    return _document(
        text if not comments else f"{text}\n# Comments\n{comments}",
        path,
        source_path,
        title=str(document.core_properties.title or ""),
        sections=_docx_heading_lines(body),
        tables=_docx_tables(body),
    )


def _docx_heading_lines(body: Any) -> tuple[str, ...]:
    """The text of every heading-styled paragraph, in document order.

    Word marks a heading with a paragraph style, so the style id is what
    decides it. The paragraph's own ``text`` is read here rather than its runs,
    which is the same reading the body text uses and therefore cannot disagree
    with it about what the heading says.
    """
    from docx.oxml.ns import qn

    out: list[str] = []
    for paragraph in body.iter(qn("w:p")):
        if next(paragraph.iterancestors(_DOCX_FALLBACK_TAG), None) is not None:
            continue
        style = paragraph.find(f"{qn('w:pPr')}/{qn('w:pStyle')}")
        value = "" if style is None else str(style.get(qn("w:val")) or "")
        heading = paragraph.text.strip()
        if heading and _heading_level(value):
            out.append(heading)
    return tuple(out)


def _heading_level(style_id: str) -> int:
    """The heading level a Word style id names, or 0 when it is a body style.

    Style *ids* are the stable, untranslated form (a Chinese document's 标题 1 is
    still ``Heading1`` internally), which is why the id and not the display name
    is what gets matched.
    """
    lowered = style_id.strip().lower().replace(" ", "")
    if not lowered.startswith("heading"):
        return 0
    tail = lowered.removeprefix("heading")
    return int(tail) if tail.isdigit() and 1 <= int(tail) <= 9 else 0


def _docx_tables(body: Any) -> tuple[ParsedTable, ...]:
    """The body's tables, in document order (design §6.2).

    Rows and cells are read as rows and cells rather than run through the text
    formatter, which is the point of keeping a structure at all. A nested table
    is one of its parent cell's contents, so it is not reported a second time as
    a table of its own — it would then be numbered twice and read twice.
    """
    from docx.oxml.ns import qn

    tables: list[ParsedTable] = []
    for element in body.iter(qn("w:tbl")):
        if next(element.iterancestors(qn("w:tbl")), None) is not None:
            continue
        rows = [
            [
                " ".join(node.text or "" for node in cell.iter(qn("w:t"))).strip()
                for cell in row.findall(qn("w:tc"))
            ]
            for row in element.findall(qn("w:tr"))
        ]
        table = _table(f"Table {len(tables) + 1}", rows)
        if table is not None:
            tables.append(table)
    return tuple(tables)


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


def _pptx_document(path: Path, source_path: str) -> ParsedDocument:
    """Slide text, table cells and speaker notes (design §6), one block per slide.

    ``slide.notes_slide`` *creates* a notes part when the file has none, which
    would edit the presentation being read; ``has_notes_slide`` answers without
    that side effect, so a deck without notes is left exactly as it was.

    A deck's sections are its slide titles, and its page count is its slide
    count: those are the two things a reader of a deck navigates by, and neither
    needs inventing.
    """
    from pptx import Presentation

    presentation = Presentation(str(path))
    slides = list(presentation.slides)
    parts: list[str] = []
    tables: list[ParsedTable] = []
    sections: list[str] = []
    for index, slide in enumerate(slides, start=1):
        title = _slide_title(slide)
        if title:
            sections.append(title)
        lines: list[str] = []
        for shape in slide.shapes:
            if getattr(shape, "has_table", False):
                rows = [
                    [_stringify_cell(cell.text) for cell in row.cells] for row in shape.table.rows
                ]
                rendered = _rows_text(rows)
                if rendered:
                    lines.append(rendered)
                table = _table(f"Slide {index}", rows)
                if table is not None:
                    tables.append(table)
            elif getattr(shape, "has_text_frame", False):
                text = shape.text_frame.text.strip()
                if text:
                    lines.append(text)
        notes = _pptx_notes(slide)
        if notes:
            lines.append(f"# Notes\n{notes}")
        if lines:
            parts.append(f"# Slide {index}\n" + "\n".join(lines))
    return _document(
        "\n\n".join(parts),
        path,
        source_path,
        title=str(presentation.core_properties.title or ""),
        sections=tuple(sections),
        tables=tuple(tables),
        pages=len(slides) or None,
    )


def _slide_title(slide: Any) -> str:
    """A slide's title placeholder text, or ``""`` when it has none."""
    title = slide.shapes.title
    if title is None:
        return ""
    return str(title.text).strip()


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


def _html_document(path: Path, source_path: str) -> ParsedDocument:
    raw = _read_text(path)
    parser = _HTMLTextParser()
    parser.feed(raw)
    parser.close()
    return _document(
        _collapse_lines(parser.text()),
        path,
        source_path,
        title=parser.title,
        sections=tuple(parser.headings),
    )


def _html_text(raw: str) -> str:
    parser = _HTMLTextParser()
    parser.feed(raw)
    parser.close()
    return _collapse_lines(parser.text())


def _collapse_lines(raw: str) -> str:
    """One line per source line, whitespace collapsed and empties dropped."""
    return "\n".join(line for line in (" ".join(part.split()) for part in raw.splitlines()) if line)


class _HTMLTextParser(HTMLParser):
    _SKIP = frozenset({"script", "style", "noscript", "template"})
    _HEADINGS = frozenset({"h1", "h2", "h3", "h4", "h5", "h6"})
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
        self.title = ""
        self.headings: list[str] = []
        self._captured: list[str] | None = None
        self._captured_tag = ""

    def handle_starttag(self, tag: str, _attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._SKIP:
            self._skip += 1
            return
        if self._skip:
            return
        if tag in self._HEADINGS or tag == "title":
            # Started here so the element's own text can be read whole; nested
            # markup inside it keeps appending through ``handle_data``.
            self._captured = []
            self._captured_tag = tag
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self._SKIP:
            if self._skip:
                self._skip -= 1
            return
        if self._skip:
            return
        if tag in self._HEADINGS or tag == "title":
            self._close_capture(tag)
        if tag in self._BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip or not data:
            return
        self._parts.append(data)
        if self._captured is not None:
            self._captured.append(data)

    def _close_capture(self, tag: str) -> None:
        if self._captured is None or tag != self._captured_tag:
            return
        value = " ".join("".join(self._captured).split())
        if tag == "title":
            # The document's own title — what a browser tab or a search result
            # shows — as opposed to the first heading, which is a different
            # claim and may be absent.
            self.title = value
        elif value:
            self.headings.append(value)
        self._captured = None
        self._captured_tag = ""

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


def _sheet_tables(
    sheets: Iterable[tuple[str, Iterable[Iterable[object]]]],
) -> tuple[ParsedTable, ...]:
    """One table per sheet, in workbook order, empty sheets skipped."""
    tables: list[ParsedTable] = []
    for location, rows in sheets:
        table = _table(location, rows)
        if table is not None:
            tables.append(table)
    return tuple(tables)


def _delimited_document(path: Path, source_path: str, *, delimiter: str) -> ParsedDocument:
    table = _table(path.stem, csv.reader(_read_text(path).splitlines(), delimiter=delimiter))
    tables = () if table is None else (table,)
    return _document(_tables_text(tables), path, source_path, tables=tables)


def _xlsx_document(path: Path, source_path: str) -> ParsedDocument:
    from openpyxl import load_workbook

    workbook = load_workbook(path, read_only=True, data_only=True)
    try:
        tables = _sheet_tables(
            (str(sheet.title), sheet.iter_rows(values_only=True)) for sheet in workbook.worksheets
        )
        properties = workbook.properties
        title = "" if properties is None else str(properties.title or "")
    finally:
        workbook.close()
    return _document(_tables_text(tables), path, source_path, title=title, tables=tables)


def _xls_document(path: Path, source_path: str) -> ParsedDocument:
    import xlrd

    try:
        book = xlrd.open_workbook(str(path), formatting_info=False)
    except Exception as exc:
        if _mentions_password(exc):
            raise PasswordRequiredError("the file is password-protected") from exc
        raise
    tables = _sheet_tables((sheet.name, _xls_rows(book, sheet)) for sheet in book.sheets())
    return _document(_tables_text(tables), path, source_path, tables=tables)


def _xls_rows(book: Any, sheet: Any) -> list[list[object]]:
    return [
        [_xlrd_cell_value(book, sheet.cell(row, column)) for column in range(sheet.ncols)]
        for row in range(sheet.nrows)
    ]


def _pdf_document(path: Path, source_path: str, *, ocr: OcrExtractor | None) -> ParsedDocument:
    """A PDF's text, plus the two things only a PDF knows: pages and its title.

    An encrypted file never reaches here — :func:`_needs_password` refuses it
    first, which is what keeps this from raising a decryption error at the
    caller.
    """
    from pypdf import PdfReader

    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    if not text.strip() and ocr is not None:
        # design §6: a scanned page has no embedded text, so it goes to OCR.
        text = ocr(path)
    metadata = reader.metadata
    return _document(
        text,
        path,
        source_path,
        title="" if metadata is None else str(metadata.title or ""),
        pages=len(reader.pages),
    )


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
