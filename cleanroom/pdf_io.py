"""Conservative local PDF adapters; no AI-watermark detection claims.

Rebuilding preserves extracted text only, not layout, images, or mathematics.
Metadata cleaning preserves content streams and therefore preserves text markers.
"""

from __future__ import annotations

from functools import wraps
from io import BytesIO
from itertools import groupby
from pathlib import Path
from typing import Iterator

import reportlab
from pypdf import PdfReader, PdfWriter
from pypdf.errors import PyPdfError
from pypdf.generic import ArrayObject, ContentStream, DictionaryObject, IndirectObject, NameObject
from reportlab.lib.pagesizes import A4
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas

MAX_BYTES = 100 * 1024 * 1024
MAX_PAGES = 500
MAX_OBJECTS = 100_000
MAX_TEXT_CHARACTERS = 5_000_000
FONT_SIZE = 11
LINE_HEIGHT = 17
MARGIN = 48
LATIN_FONT = "CleanroomVera"
CJK_FONT = "STSong-Light"
VECTOR_OPERATORS = frozenset({
    b"m", b"l", b"c", b"v", b"y", b"h", b"re", b"S", b"s",
    b"f", b"F", b"f*", b"B", b"B*", b"b", b"b*", b"sh",
})


def _friendly_pdf_errors(operation):
    @wraps(operation)
    def guarded(*args, **kwargs):
        try:
            return operation(*args, **kwargs)
        except PyPdfError as exc:
            raise ValueError("Cannot process PDF; it may be damaged or unsupported.") from exc
    return guarded


def _read(path: Path) -> PdfReader:
    path = Path(path)
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("PDF exceeds the 100 MB size limit.")
    try:
        reader = PdfReader(path, strict=False)
        if reader.is_encrypted:
            raise ValueError("Unsupported encrypted PDF; decrypt it locally first.")
        if len(reader.pages) > MAX_PAGES:
            raise ValueError("PDF exceeds the 500-page limit.")
        return reader
    except (PyPdfError, KeyError, TypeError, RecursionError) as exc:
        raise ValueError("Cannot parse PDF; it may be damaged or unsupported.") from exc


def _objects(start: object) -> Iterator[DictionaryObject]:
    """Walk an object graph without parsing or executing PDF active content."""
    pending = [start]
    seen: set[object] = set()
    while pending:
        value = pending.pop()
        identity = (
            (id(value.pdf), value.idnum, value.generation)
            if isinstance(value, IndirectObject)
            else id(value)
        )
        if identity in seen:
            continue
        seen.add(identity)
        if len(seen) > MAX_OBJECTS:
            raise ValueError("PDF has too many objects to inspect safely.")
        if isinstance(value, IndirectObject):
            value = value.get_object()
        if isinstance(value, DictionaryObject):
            yield value
            pending.extend(value.values())
        elif isinstance(value, (ArrayObject, list)):
            pending.extend(value)


def _page_visual_markers(page: DictionaryObject, reader: PdfReader) -> set[str]:
    """Detect drawn graphics, including nested Forms and inline images."""
    resources = page.get("/Resources", DictionaryObject())
    markers: set[str] = set()
    streams = [page.get_contents()]
    for item in _objects(resources):
        if item.get("/Subtype") == "/Image":
            markers.add("images")
        elif item.get("/Subtype") == "/Form":
            streams.append(item)
    for stream in streams:
        if stream is None:
            continue
        for _, operator in ContentStream(stream, reader).operations:
            if operator in VECTOR_OPERATORS:
                markers.add("vector_graphics")
            elif operator == b"INLINE IMAGE":
                markers.add("images")
    return markers


def _page_text(page: DictionaryObject, index: int) -> str:
    try:
        text = page.extract_text() or ""
    except (PyPdfError, KeyError, TypeError, ValueError, RecursionError) as exc:
        raise ValueError(f"Cannot extract PDF page {index + 1}; OCR/manual review is required.") from exc
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ValueError("Extracted PDF text exceeds the 5-million-character limit.")
    return text


@_friendly_pdf_errors
def extract_pdf(path: Path) -> str:
    """Extract page text separated by form feeds; refuse image-only pages."""
    reader = _read(path)
    pages: list[str] = []
    total = 0
    for index, page in enumerate(reader.pages):
        text = _page_text(page, index)
        if not text.strip() and _page_visual_markers(page, reader):
            raise ValueError(f"PDF page {index + 1} has graphics but no text. Run OCR and manually verify it first.")
        if not text.strip() and page.get_contents() is not None:
            if page.get_contents().get_data().strip():
                # An apparently blank page may contain outlined text or drawings.
                # ReportLab's empty page stream contains only setup operators.
                content = page.get_contents().get_data()
                if any(operator in content.split() for operator in (b"Do", b"m", b"re", b"Tj", b"TJ")):
                    raise ValueError(f"PDF page {index + 1} has unextractable content; OCR/manual review is required.")
        total += len(text)
        if total > MAX_TEXT_CHARACTERS:
            raise ValueError("Extracted PDF text exceeds the 5-million-character limit.")
        pages.append(text)
    return "\f".join(pages)


@_friendly_pdf_errors
def inspect_pdf(path: Path) -> dict:
    """Return structural marker names, never private metadata values."""
    reader = _read(path)
    markers: set[str] = set()
    for item in _objects(reader.root_object):
        if item.get("/Subtype") == "/Image":
            markers.add("images")
        for key, label in (("/Annots", "annotations"), ("/AcroForm", "forms"),
                           ("/EmbeddedFiles", "attachments"), ("/Metadata", "xmp"),
                           ("/JavaScript", "javascript"), ("/JS", "javascript")):
            if key in item:
                markers.add(label)
        if item.get("/Type") == "/EmbeddedFile" or "/EF" in item:
            markers.add("attachments")
        if item.get("/FT") == "/Sig" or item.get("/Type") == "/Sig":
            markers.add("signatures")
    warnings = [
        "PDF extraction cannot prove original reading order, formulas, or layout fidelity; manually compare the source and result.",
        "Rebuild mode reflows extracted text and discards images, vector graphics, annotations, forms, attachments, and original layout.",
        "Metadata-only mode preserves text, hidden characters, and active content; it is not a text-watermark remover.",
    ]
    for index, page in enumerate(reader.pages):
        page_markers = _page_visual_markers(page, reader)
        markers.update(page_markers)
        if not _page_text(page, index).strip() and page_markers:
            warnings.append(f"Page {index + 1} has graphics without extractable text: OCR and manual verification are required before rebuilding.")
    if "signatures" in markers:
        warnings.append("Digital signatures are present; rewriting would invalidate them.")
    metadata = reader.metadata or {}
    return {
        "metadata_fields": sorted(str(key) for key, value in metadata.items() if value not in (None, "")),
        "container_markers": sorted(markers),
        "warnings": warnings,
    }


def _register_fonts() -> None:
    if LATIN_FONT not in pdfmetrics.getRegisteredFontNames():
        font_path = Path(reportlab.__file__).parent / "fonts" / "Vera.ttf"
        pdfmetrics.registerFont(TTFont(LATIN_FONT, str(font_path)))
    if CJK_FONT not in pdfmetrics.getRegisteredFontNames():
        pdfmetrics.registerFont(UnicodeCIDFont(CJK_FONT))


def _font_for_char(character: str) -> str:
    codepoint = ord(character)
    if codepoint in pdfmetrics.getFont(LATIN_FONT).face.charToGlyph:
        return LATIN_FONT
    if 0x4E00 <= codepoint <= 0x9FA5 or 0x3000 <= codepoint <= 0x303F or 0xFF01 <= codepoint <= 0xFF60:
        return CJK_FONT
    raise ValueError(
        f"Cannot reliably render Unicode character U+{codepoint:04X} in PDF. "
        "Use DOCX output or an explicitly configured font in a document editor."
    )


def _wrap_line(text: str, available_width: float) -> Iterator[str]:
    line: list[str] = []
    width = 0.0
    for character in text:
        glyph_width = pdfmetrics.stringWidth(character, _font_for_char(character), FONT_SIZE)
        if line and width + glyph_width > available_width:
            yield "".join(line)
            line = []
            width = 0.0
        line.append(character)
        width += glyph_width
    yield "".join(line)


def _draw_line(document: canvas.Canvas, line: str, y: float) -> None:
    text_object = document.beginText(MARGIN, y)
    for font, characters in groupby(line, key=_font_for_char):
        text_object.setFont(font, FONT_SIZE)
        # Canvas emits literal PDF text, so <xml>, &, and quotes are not markup.
        text_object.textOut("".join(characters))
    document.drawText(text_object)


def _write_new_pdf(writer: PdfWriter, destination: Path) -> None:
    # Exclusive creation protects existing files, including aliases and symlinks.
    stream = destination.open("xb")
    try:
        with stream:
            writer.write(stream)
    except Exception as exc:
        try:
            destination.unlink()
        except OSError as cleanup_error:
            exc.add_note(f"Could not remove the incomplete new output: {cleanup_error}")
        raise


@_friendly_pdf_errors
def rebuild_pdf(text: str, path: Path) -> None:
    """Reflow supported Unicode text into A4 PDF; reject unsupported glyphs."""
    if len(text) > MAX_TEXT_CHARACTERS:
        raise ValueError("Text exceeds the 5-million-character limit.")
    _register_fonts()
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").replace("\t", "    ")
    for character in set(normalized) - {"\n", "\f"}:
        _font_for_char(character)
    buffer = BytesIO()
    document = canvas.Canvas(buffer, pagesize=A4, pageCompression=1)
    width, height = A4
    page_count = 1
    for page_index, page in enumerate(normalized.split("\f")):
        if page_index:
            document.showPage()
            page_count += 1
        y = height - MARGIN
        for original_line in page.split("\n"):
            for line in _wrap_line(original_line, width - 2 * MARGIN):
                if y < MARGIN:
                    document.showPage()
                    page_count += 1
                    y = height - MARGIN
                if page_count > MAX_PAGES:
                    raise ValueError("Rebuilt PDF would exceed the 500-page limit.")
                _draw_line(document, line, y)
                y -= LINE_HEIGHT
    document.showPage()
    document.save()
    writer = PdfWriter(clone_from=PdfReader(buffer))
    writer.metadata = None
    _write_new_pdf(writer, Path(path))


@_friendly_pdf_errors
def clean_pdf_metadata(source: Path, destination: Path) -> dict:
    """Clone a PDF, remove info/XMP, and preserve all content streams."""
    source = Path(source)
    destination = Path(destination)
    if source.resolve() == destination.resolve() or (destination.exists() and source.samefile(destination)):
        raise ValueError("Source and destination must not be the same file.")
    reader = _read(source)
    report = inspect_pdf(source)
    if "signatures" in report["container_markers"]:
        raise ValueError("Signed PDF cannot be rewritten without invalidating its signatures.")
    writer = PdfWriter(clone_from=reader)
    writer.metadata = None
    xmp_removed = 0
    for item in _objects(writer.root_object):
        if "/Metadata" in item:
            item.pop(NameObject("/Metadata"))
            xmp_removed += 1
    # Remove unreachable cloned XMP streams instead of leaving recoverable bytes.
    writer.compress_identical_objects(remove_duplicates=False, remove_unreferenced=True)
    _write_new_pdf(writer, destination)
    return {
        "removed_metadata_fields": report["metadata_fields"],
        "removed_xmp_objects": xmp_removed,
        "warnings": ["Content streams and hidden text were preserved; this operation does not prove any text watermark was removed."],
    }
