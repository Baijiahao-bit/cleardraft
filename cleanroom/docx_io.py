"""Local DOCX text extraction, conservative cleanup, and clean reconstruction.

Text cleanup is not a test for a statistical language-model watermark. This
module never follows relationships or accesses resources outside the package.
"""

from __future__ import annotations

import posixpath
import stat
from collections.abc import Callable
from lzma import LZMAError
from pathlib import Path, PurePosixPath
from tempfile import TemporaryDirectory
from zipfile import BadZipFile, ZIP_DEFLATED, ZipFile, ZipInfo
from zlib import error as ZlibError

from docx import Document
from docx.enum.text import WD_BREAK
from lxml import etree

MAX_COMPRESSED_BYTES = 100 * 1024 * 1024
MAX_EXPANDED_BYTES = 300 * 1024 * 1024
MAX_ENTRIES = 5000
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
STRICT_W = "http://purl.oclc.org/ooxml/wordprocessingml/main"
MATH_NS = "http://schemas.openxmlformats.org/officeDocument/2006/math"
DRAWING_NS = "http://schemas.openxmlformats.org/drawingml/2006/main"
COMPAT_NS = "http://schemas.openxmlformats.org/markup-compatibility/2006"
XML_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
NS = {"w": W}
TRACKED_TAGS = {
    "ins", "del", "moveFrom", "moveTo", "rPrChange", "pPrChange",
    "sectPrChange", "tblPrChange", "trPrChange", "tcPrChange",
    "numberingChange", "cellIns", "cellDel", "cellMerge",
}
COMPLEX_WORD_TAGS = {
    "tbl": "tables", "sym": "symbol characters", "altChunk": "alternative-format content",
    "fldSimple": "field codes", "fldChar": "field codes", "instrText": "field codes",
    "footnoteReference": "footnotes", "endnoteReference": "endnotes",
    "txbxContent": "text boxes", "drawing": "drawings", "pict": "drawings",
    "object": "embedded objects", "ruby": "ruby annotations", "numPr": "numbered lists",
    "hyperlink": "hyperlinks", "sdt": "content controls", "subDoc": "subdocuments",
    "ptab": "special text elements", "noBreakHyphen": "special text elements",
    "softHyphen": "special text elements",
}


def _safe_xml(data: bytes, name: str) -> etree._Element:
    parser = etree.XMLParser(resolve_entities=False, no_network=True,
                             load_dtd=False, huge_tree=False, recover=False)
    try:
        root = etree.fromstring(data, parser=parser)
    except etree.XMLSyntaxError as exc:
        raise ValueError(f"Invalid XML in DOCX part: {name}") from exc
    if root.getroottree().docinfo.doctype:
        raise ValueError(f"DTD/entities are not permitted in DOCX: {name}")
    return root


def _validate_entry(info: ZipInfo) -> None:
    # ZipInfo normalizes backslashes on Windows and truncates NULs. Validate
    # the original archive name before those platform-specific transformations.
    name = info.orig_filename
    path = PurePosixPath(name)
    if (not name or "\\" in name or "\x00" in name or ":" in name
            or path.is_absolute() or ".." in path.parts):
        raise ValueError("DOCX contains an unsafe package path")
    if info.flag_bits & 1:
        raise ValueError("Encrypted DOCX ZIP entries are unsupported")
    if stat.S_ISLNK(info.external_attr >> 16):
        raise ValueError("DOCX packages must not contain symbolic links")
    if "vbaproject" in name.lower() or name.lower().endswith(".vba"):
        raise ValueError("Macro-enabled Word documents are unsupported")


def _read_package(path: Path) -> tuple[list[tuple[ZipInfo, bytes]], dict[str, etree._Element]]:
    source = Path(path)
    if source.stat().st_size > MAX_COMPRESSED_BYTES:
        raise ValueError("DOCX exceeds the 100 MiB compressed size limit")
    try:
        with ZipFile(source) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ENTRIES:
                raise ValueError("DOCX exceeds the 5000-entry limit")
            if sum(info.file_size for info in infos) > MAX_EXPANDED_BYTES:
                raise ValueError("DOCX exceeds the 300 MiB expanded size limit")
            if len({info.filename for info in infos}) != len(infos):
                raise ValueError("DOCX contains duplicate package entries")
            for info in infos:
                _validate_entry(info)
            entries = [(info, archive.read(info)) for info in infos]
    except (BadZipFile, RuntimeError, NotImplementedError, ZlibError, LZMAError, EOFError) as exc:
        raise ValueError("Invalid, encrypted, or unsupported DOCX ZIP package") from exc
    xml = {info.filename: _safe_xml(data, info.filename)
           for info, data in entries
           if info.filename.lower().endswith((".xml", ".rels"))}
    _validate_document(xml)
    return entries, xml


def _validate_document(xml: dict[str, etree._Element]) -> None:
    if "word/document.xml" not in xml or "[Content_Types].xml" not in xml:
        raise ValueError("The ZIP package is not a supported DOCX document")
    root = xml["word/document.xml"]
    if etree.QName(root).namespace == STRICT_W:
        raise ValueError("Strict OOXML is unsupported; resave as a standard DOCX")
    if root.tag != f"{{{W}}}document" or root.find(f"{{{W}}}body") is None:
        raise ValueError("Unsupported Word document XML structure")
    content_types = xml["[Content_Types].xml"]
    if any("macroenabled" in node.get("ContentType", "").lower()
           or "vbaproject" in node.get("ContentType", "").lower()
           for node in content_types):
        raise ValueError("Macro-enabled Word documents are unsupported")


def _paragraph_text(paragraph: etree._Element) -> str:
    chunks = []
    for node in paragraph.iter():
        if not isinstance(node.tag, str):
            continue
        parents = node.iterancestors(f"{{{W}}}p")
        if next(parents, None) is not paragraph:
            continue
        if node.tag == f"{{{W}}}t":
            chunks.append(node.text or "")
        elif node.tag == f"{{{W}}}tab":
            chunks.append("\t")
        elif node.tag in {f"{{{W}}}br", f"{{{W}}}cr"}:
            chunks.append("\f" if node.get(f"{{{W}}}type") == "page" else "\n")
    return "".join(chunks)


def extract_docx(path: Path) -> str:
    """Extract body paragraphs, including table cells, in XML document order."""
    _, xml = _read_package(path)
    paragraphs = xml["word/document.xml"].xpath("./w:body//w:p", namespaces=NS)
    return "\n".join(_paragraph_text(paragraph) for paragraph in paragraphs)


def _metadata_fields(xml: dict[str, etree._Element]) -> list[str]:
    fields = []
    for group in ("core", "app", "custom"):
        root = xml.get(f"docProps/{group}.xml")
        if root is None:
            continue
        for child in root:
            if not isinstance(child.tag, str) or not "".join(child.itertext()).strip():
                continue
            name = child.get("name", etree.QName(child).localname)
            fields.append(f"{group}:{name}")
    return sorted(set(fields))


def _node_markers(root: etree._Element, name: str) -> set[str]:
    markers = set()
    style_part = name.endswith(("styles.xml", "stylesWithEffects.xml", "numbering.xml"))
    for node in root.iter():
        if not isinstance(node.tag, str):
            continue
        tag = etree.QName(node)
        if tag.namespace == W and tag.localname in TRACKED_TAGS:
            markers.add("tracked changes")
        if node.tag == f"{{{W}}}docVars":
            markers.add("document variables")
        if node.tag == f"{{{W}}}vanish":
            markers.add("hidden text formatting")
        if style_part:
            continue
        if tag.namespace == W and tag.localname in COMPLEX_WORD_TAGS:
            markers.add(COMPLEX_WORD_TAGS[tag.localname])
        if tag.namespace == MATH_NS and tag.localname in {"oMath", "oMathPara", "t"}:
            markers.add("equations")
        if tag.namespace == DRAWING_NS and tag.localname == "t":
            markers.add("drawing text")
        if tag.namespace == COMPAT_NS and tag.localname == "AlternateContent":
            markers.add("alternate content")
    return markers


def _markers(xml: dict[str, etree._Element], names: set[str]) -> list[str]:
    markers = set()
    if any(name.startswith("customXml/") for name in names):
        markers.add("customXml")
    if any(name.startswith("word/comments") for name in names):
        markers.add("comments")
    if any(name.startswith(("word/media/", "word/embeddings/")) for name in names):
        markers.add("embedded media")
    if any(name.startswith("_xmlsignatures/") for name in names):
        markers.add("digital signatures")
    for prefix, marker in (("word/header", "headers"), ("word/footer", "footers"),
                           ("word/footnotes", "footnotes"), ("word/endnotes", "endnotes")):
        if any(name.startswith(prefix) and name.endswith(".xml") for name in names):
            markers.add(marker)
    for name, root in xml.items():
        if name.endswith(".rels") and any(
                child.get("TargetMode", "").lower() == "external" for child in root):
            markers.add("external relationships")
        if name.startswith("word/"):
            markers.update(_node_markers(root, name))
    return sorted(markers)


def _inspect(entries: list[tuple[ZipInfo, bytes]], xml: dict[str, etree._Element]) -> dict:
    markers = _markers(xml, {info.filename for info, _ in entries})
    warnings = []
    if markers:
        warnings.append("保格式模式保留这些容器内容；其中可能仍含作者信息、隐藏文本或来源标记："
                        + ", ".join(markers))
    if "digital signatures" in markers:
        warnings.append("修改文档会使已有数字签名失效。")
    extra = []
    for name, root in xml.items():
        if name.startswith("word/") and name != "word/document.xml":
            extra.extend(node.text or "" for node in root.iter(f"{{{W}}}t"))
        if name.startswith("word/"):
            extra.extend(node.text or "" for node in root.iter(f"{{{W}}}instrText"))
            extra.extend(node.text or "" for node in root.iter(f"{{{W}}}delText"))
            extra.extend(node.text or "" for node in root.iter(f"{{{MATH_NS}}}t"))
            extra.extend(node.text or "" for node in root.iter(f"{{{DRAWING_NS}}}t"))
    return {"metadata_fields": _metadata_fields(xml), "container_markers": markers,
            "warnings": warnings, "extra_text": "\n".join(extra)}


def inspect_docx(path: Path) -> dict:
    """Report field names and residual containers without exposing their values."""
    entries, xml = _read_package(path)
    return _inspect(entries, xml)


def rebuild_docx(text: str, path: Path) -> None:
    """Re-type extracted text into a new document; original layout is not retained."""
    document = Document()
    # python-docx's bundled blank template has a bibliography customXml part.
    # A new text-only document does not need this generator template material.
    for relationship in tuple(document.part.rels.values()):
        if relationship.reltype.endswith("/customXml"):
            document.part.drop_rel(relationship.rId)
    props = document.core_properties
    for name in ("author", "last_modified_by", "title", "subject", "keywords",
                 "comments", "category", "identifier", "language", "version"):
        setattr(props, name, "")
    for line in text.split("\n"):
        paragraph = document.add_paragraph()
        for index, part in enumerate(line.split("\f")):
            if index:
                paragraph.add_run().add_break(WD_BREAK.PAGE)
            paragraph.add_run(part)
    with TemporaryDirectory(prefix="doc-cleanroom-docx-") as temporary:
        draft = Path(temporary) / "rebuilt.docx"
        document.save(draft)
        clean_docx(draft, Path(path), lambda value: value)


def _relationship_target(name: str, target: str) -> str:
    base = posixpath.dirname(posixpath.dirname(name))
    return posixpath.normpath(posixpath.join(base, target)).lstrip("/")


def _remove_metadata_refs(root: etree._Element, name: str, removed: set[str]) -> bool:
    changed = False
    for child in list(root):
        target = child.get("Target", "")
        is_removed_relation = (name.endswith(".rels")
                               and child.get("TargetMode", "").lower() != "external"
                               and _relationship_target(name, target) in removed)
        is_removed_type = (name == "[Content_Types].xml"
                           and child.get("PartName", "").lstrip("/") in removed)
        if is_removed_relation or is_removed_type:
            root.remove(child)
            changed = True
    return changed


def _clean_word_xml(root: etree._Element, transform: Callable[[str], str]) -> tuple[int, int]:
    changed_text = 0
    removed_rsids = 0
    for node in list(root.iter()):
        if not isinstance(node.tag, str):
            continue
        if node.tag == f"{{{W}}}t" and node.text is not None:
            cleaned = transform(node.text)
            if not isinstance(cleaned, str):
                raise ValueError("Text transform must return a string")
            if cleaned != node.text:
                node.text = cleaned
                if cleaned[:1].isspace() or cleaned[-1:].isspace():
                    node.set(XML_SPACE, "preserve")
                changed_text += 1
        for key in list(node.attrib):
            if key.startswith(f"{{{W}}}rsid"):
                del node.attrib[key]
                removed_rsids += 1
        if node.tag == f"{{{W}}}rsids" and node.getparent() is not None:
            node.getparent().remove(node)
            removed_rsids += 1
    return changed_text, removed_rsids


def clean_docx(source: Path, destination: Path, transform: Callable[[str], str]) -> dict:
    """Clean w:t text and package properties while retaining document formatting.

    Comments, revisions, custom XML, embedded files, and field codes are retained
    and reported. The source cannot be overwritten and no resources are fetched.
    """
    if (Path(source).resolve() == Path(destination).resolve()
            or (Path(destination).exists() and Path(source).samefile(destination))):
        raise ValueError("Source and destination must be different files")
    entries, xml = _read_package(source)
    removed = {info.filename for info, _ in entries if info.filename.startswith("docProps/")}
    output = []
    changed_text = 0
    removed_rsids = 0
    for info, data in entries:
        if info.filename in removed:
            continue
        root = xml.get(info.filename)
        if root is not None:
            changed = _remove_metadata_refs(root, info.filename, removed)
            if info.filename.startswith("word/") and info.filename.endswith(".xml"):
                text_count, rsid_count = _clean_word_xml(root, transform)
                changed_text += text_count
                removed_rsids += rsid_count
                changed = changed or bool(text_count or rsid_count)
            if changed:
                data = etree.tostring(root, encoding="UTF-8", xml_declaration=True, standalone=True)
        output.append((info, data))
    with ZipFile(Path(destination), "x", compression=ZIP_DEFLATED) as archive:
        for info, data in output:
            archive.writestr(info, data)
    report = inspect_docx(destination)
    audit = {key: value for key, value in report.items() if key != "extra_text"}
    return {**audit, "changed_text_nodes": changed_text, "removed_rsid_markers": removed_rsids,
            "removed_parts": sorted(removed)}
