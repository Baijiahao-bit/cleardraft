"""Read evidence independently of cleanroom's scanner and transformation code."""

import argparse
import hashlib
import json
import zipfile
from pathlib import Path

from defusedxml import ElementTree
from pypdf import PdfReader

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
REMOVABLE = "\u200b\u2060\ufeff\u00ad"


def raw_docx(path):
    with zipfile.ZipFile(path) as archive:
        parts = {name: archive.read(name) for name in archive.namelist()}
    text = "".join(node.text or "" for name, value in sorted(parts.items())
                   if name.startswith("word/") and name.endswith(".xml")
                   for node in ElementTree.fromstring(value).iter(W + "t"))
    return text, sorted(name for name in parts if name.startswith("docProps/"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    args = parser.parse_args()
    directory = args.directory
    before, properties_before = raw_docx(directory / "synthetic-input.docx")
    after, properties_after = raw_docx(directory / "docx-preserved.docx")
    expected = "".join(c for c in before if c not in REMOVABLE)
    pdf_before = PdfReader(directory / "synthetic-input.pdf")
    pdf_after = PdfReader(directory / "pdf-metadata-cleaned.pdf")
    checks = {
        "docx_expected_w_t_text_equal_including_headers": expected == after,
        "docx_selected_characters_before": sum(c in REMOVABLE for c in before),
        "docx_selected_characters_after": sum(c in REMOVABLE for c in after),
        "docx_metadata_parts_before": properties_before,
        "docx_metadata_parts_after": properties_after,
        "docx_joiners_preserved": after.count("\u200d") == before.count("\u200d") and after.count("\u200c") == before.count("\u200c"),
        "pdf_metadata_before": sorted(str(k) for k, v in (pdf_before.metadata or {}).items() if str(v)),
        "pdf_metadata_after": sorted(str(k) for k, v in (pdf_after.metadata or {}).items() if str(v)),
        "pdf_page_content_streams_identical": len(pdf_before.pages) == len(pdf_after.pages) and all(
            a.get_contents().get_data() == b.get_contents().get_data()
            for a, b in zip(pdf_before.pages, pdf_after.pages)),
        "official_textGrain_detector": "NOT RUN; detection verdict remains UNKNOWN",
        "file_sha256": {path.name: hashlib.sha256(path.read_bytes()).hexdigest()
                        for path in sorted(directory.iterdir()) if path.suffix in {".docx", ".pdf", ".txt"}},
    }
    required = (checks["docx_expected_w_t_text_equal_including_headers"],
                checks["docx_selected_characters_after"] == 0,
                not checks["docx_metadata_parts_after"], checks["docx_joiners_preserved"],
                not checks["pdf_metadata_after"], checks["pdf_page_content_streams_identical"])
    checks["observable_checks_passed"] = all(required)
    print(json.dumps(checks, ensure_ascii=False, indent=2))
    raise SystemExit(0 if all(required) else 1)


if __name__ == "__main__":
    import sys
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
