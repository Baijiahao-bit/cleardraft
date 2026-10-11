"""Round-trip and fail-closed coverage for the PDF adapter."""

from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter
from pypdf.actions import JavaScript
from pypdf.generic import (
    ArrayObject,
    DecodedStreamObject,
    DictionaryObject,
    NameObject,
    NumberObject,
    TextStringObject,
)
from reportlab.pdfgen import canvas

from cleanroom.pdf_io import (
    clean_pdf_metadata,
    extract_pdf,
    inspect_pdf,
    rebuild_pdf,
)


def make_pdf(path: Path, text: str = "Original text") -> Path:
    document = canvas.Canvas(str(path))
    document.setTitle("Private source title")
    document.setAuthor("Private author")
    document.drawString(72, 720, text)
    document.save()
    return path


def compact(text: str) -> str:
    return "".join(text.split())


def test_pdf_rebuild_round_trips_chinese_and_markup(tmp_path):
    text = "中文文档测试：科技成果转化。English 123 <xml> & \"quotes\"\n第二行。\f下一页内容"
    target = tmp_path / "rebuilt.pdf"
    rebuild_pdf(text, target)
    assert compact(extract_pdf(target)) == compact(text)
    assert len(PdfReader(target).pages) == 2
    assert inspect_pdf(target)["metadata_fields"] == []


def test_pdf_rebuild_long_line_is_not_clipped(tmp_path):
    text = "长段落ABC123" * 450
    target = tmp_path / "long.pdf"
    rebuild_pdf(text, target)
    assert compact(extract_pdf(target)) == text
    assert len(PdfReader(target).pages) > 1


def test_rebuild_rejects_unrenderable_unicode_without_output(tmp_path):
    target = tmp_path / "unsupported.pdf"
    with pytest.raises(ValueError, match="Unicode|character|字符"):
        rebuild_pdf("unrenderable \U0001f9ec", target)
    assert not target.exists()


def test_rebuild_preserves_blank_page_boundaries(tmp_path):
    target = tmp_path / "pages.pdf"
    rebuild_pdf("first\f\flast", target)
    assert len(PdfReader(target).pages) == 3
    assert extract_pdf(target).count("\f") == 2


def test_inspect_reports_metadata_names_without_private_values(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    report = inspect_pdf(source)
    assert "/Author" in report["metadata_fields"]
    assert "Private author" not in str(report)
    assert "Private source title" not in str(report)
    assert isinstance(report["warnings"], list)


def test_metadata_clean_preserves_page_stream_and_text(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    target = tmp_path / "clean.pdf"
    clean_pdf_metadata(source, target)
    before = PdfReader(source)
    after = PdfReader(target)
    assert after.metadata is None or dict(after.metadata) == {}
    assert before.pages[0].get_contents().get_data() == after.pages[0].get_contents().get_data()
    assert extract_pdf(source) == extract_pdf(target)


def test_metadata_clean_removes_catalog_page_and_image_xmp(tmp_path):
    source = tmp_path / "xmp.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    xmp = DecodedStreamObject()
    xmp.set_data(b"<x:xmpmeta xmlns:x='adobe:ns:meta/'>private</x:xmpmeta>")
    xmp.update({NameObject("/Type"): NameObject("/Metadata"), NameObject("/Subtype"): NameObject("/XML")})
    writer.root_object[NameObject("/Metadata")] = writer._add_object(xmp)
    page[NameObject("/Metadata")] = writer._add_object(xmp)
    writer.add_metadata({"/Author": "private"})
    writer.write(source)
    assert "xmp" in inspect_pdf(source)["container_markers"]
    target = tmp_path / "clean.pdf"
    clean_pdf_metadata(source, target)
    reader = PdfReader(target)
    assert "/Metadata" not in reader.root_object
    assert "/Metadata" not in reader.pages[0]
    assert "xmp" not in inspect_pdf(target)["container_markers"]
    assert b"private" not in target.read_bytes()


def test_inspect_and_extract_reject_encrypted_files(tmp_path):
    source = tmp_path / "encrypted.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    writer.encrypt("password")
    writer.write(source)
    for operation in (extract_pdf, inspect_pdf):
        with pytest.raises(ValueError, match="encrypt|加密"):
            operation(source)


def test_extract_refuses_image_only_pages_and_inspect_warns(tmp_path):
    source = tmp_path / "scanned.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    picture = DecodedStreamObject()
    picture.set_data(b"\x00\x00\x00")
    picture.update({NameObject("/Type"): NameObject("/XObject"), NameObject("/Subtype"): NameObject("/Image"), NameObject("/Width"): NumberObject(1), NameObject("/Height"): NumberObject(1), NameObject("/BitsPerComponent"): NumberObject(8), NameObject("/ColorSpace"): NameObject("/DeviceRGB")})
    page[NameObject("/Resources")] = DictionaryObject({NameObject("/XObject"): DictionaryObject({NameObject("/Im1"): writer._add_object(picture)})})
    writer.write(source)
    with pytest.raises(ValueError, match="OCR"):
        extract_pdf(source)
    report = inspect_pdf(source)
    assert "images" in report["container_markers"]
    assert any("OCR" in warning for warning in report["warnings"])


def test_inspect_reports_active_objects_without_removing_them(tmp_path):
    source = tmp_path / "active.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    writer.add_open_action(JavaScript("app.alert('test')"))
    writer.add_attachment("fixture.txt", b"fixture")
    page[NameObject("/Annots")] = ArrayObject([DictionaryObject({NameObject("/Subtype"): NameObject("/Text"), NameObject("/Contents"): TextStringObject("annotation")})])
    writer.root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject()})
    writer.write(source)
    markers = inspect_pdf(source)["container_markers"]
    assert {"javascript", "attachments", "annotations", "forms"} <= set(markers)
    target = tmp_path / "clean.pdf"
    clean_pdf_metadata(source, target)
    assert set(markers) <= set(inspect_pdf(target)["container_markers"])


def test_limits_reject_too_many_pages(tmp_path):
    source = tmp_path / "many-pages.pdf"
    writer = PdfWriter()
    for _ in range(501):
        writer.add_blank_page(width=10, height=10)
    writer.write(source)
    with pytest.raises(ValueError, match="500"):
        extract_pdf(source)


def test_limits_reject_large_file_before_parsing(tmp_path):
    source = tmp_path / "large.pdf"
    with source.open("wb") as stream:
        stream.truncate(100 * 1024 * 1024 + 1)
    with pytest.raises(ValueError, match="100"):
        inspect_pdf(source)


def test_corrupt_pdf_has_actionable_value_error(tmp_path):
    source = tmp_path / "broken.pdf"
    source.write_bytes(b"Not a PDF")
    with pytest.raises(ValueError, match="PDF"):
        extract_pdf(source)


def test_clean_metadata_refuses_same_file(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    original = source.read_bytes()
    with pytest.raises(ValueError, match="same|同"):
        clean_pdf_metadata(source, source)
    assert source.read_bytes() == original


def test_metadata_rewrite_refuses_digital_signatures(tmp_path):
    source = tmp_path / "signed.pdf"
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    signature = DictionaryObject({NameObject("/FT"): NameObject("/Sig")})
    writer.root_object[NameObject("/AcroForm")] = DictionaryObject({NameObject("/Fields"): ArrayObject([signature])})
    writer.write(source)
    assert "signatures" in inspect_pdf(source)["container_markers"]
    with pytest.raises(ValueError, match="signature"):
        clean_pdf_metadata(source, tmp_path / "unsigned.pdf")


def test_extract_refuses_outlined_text_or_vector_only_page(tmp_path):
    source = tmp_path / "outlined.pdf"
    document = canvas.Canvas(str(source))
    document.rect(72, 72, 100, 100)
    document.save()
    with pytest.raises(ValueError, match="OCR"):
        extract_pdf(source)


def test_clean_metadata_refuses_hardlink_to_source(tmp_path):
    source = make_pdf(tmp_path / "source.pdf")
    destination = tmp_path / "alias.pdf"
    destination.hardlink_to(source)
    original = source.read_bytes()
    with pytest.raises(ValueError, match="same|同"):
        clean_pdf_metadata(source, destination)
    assert source.read_bytes() == original


@pytest.mark.parametrize("nested", [False, True])
def test_inspect_detects_vector_graphics_alongside_text(tmp_path, nested):
    source = tmp_path / "diagram.pdf"
    document = canvas.Canvas(str(source))
    document.drawString(72, 720, "Diagram:")
    if nested:
        document.beginForm("diagram")
    document.rect(72, 650, 200, 40)
    document.line(72, 650, 272, 690)
    if nested:
        document.endForm()
        document.doForm("diagram")
    document.save()
    assert "Diagram:" in extract_pdf(source)
    assert "vector_graphics" in inspect_pdf(source)["container_markers"]


@pytest.mark.parametrize("payload,marker", [
    (b"q 10 0 0 10 0 0 cm BI /W 1 /H 1 /BPC 8 /CS /RGB ID \x00\x00\x00 EI Q", "images"),
    (b"/Sh1 sh", "vector_graphics"),
])
def test_inspect_detects_inline_images_and_shading(tmp_path, payload, marker):
    source = tmp_path / "graphics.pdf"
    writer = PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    stream = DecodedStreamObject()
    stream.set_data(payload)
    page[NameObject("/Contents")] = writer._add_object(stream)
    writer.write(source)
    assert marker in inspect_pdf(source)["container_markers"]
    with pytest.raises(ValueError, match="OCR"):
        extract_pdf(source)


@pytest.mark.parametrize("mode", ["rebuild", "metadata"])
def test_backend_refuses_existing_destination_without_modifying_it(tmp_path, mode):
    source = make_pdf(tmp_path / "source.pdf")
    destination = tmp_path / "existing.pdf"
    sentinel = b"existing file must remain unchanged"
    destination.write_bytes(sentinel)
    with pytest.raises(FileExistsError):
        if mode == "rebuild":
            rebuild_pdf("New text", destination)
        else:
            clean_pdf_metadata(source, destination)
    assert destination.read_bytes() == sentinel
