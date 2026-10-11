"""Shared local service for the command line and desktop interface."""

import hashlib
import json
import os
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from . import __version__
from .signals import LIMITATIONS, clean_text, comparable_text, scan_text

SUPPORTED = frozenset({".docx", ".pdf", ".txt", ".md"})
MAX_BYTES = 100 * 1024 * 1024


def _validate_path_name(path: Path) -> None:
    if os.name != "nt":
        return
    reserved = {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"} | {
        f"{prefix}{number}" for prefix in ("COM", "LPT") for number in range(1, 10)
    }
    for part in path.parts:
        if part == path.anchor:
            continue
        if (any(char in '<>:"|?*' or ord(char) < 32 for char in part)
                or part.endswith((" ", ".")) or part.split(".")[0].upper() in reserved):
            raise ValueError("不支持 Windows 特殊文件名、设备路径或 NTFS 隐藏数据流，请使用普通文件路径。")


def validate_input(path: Path) -> Path:
    _validate_path_name(Path(path))
    path = Path(path).resolve()
    _validate_path_name(path)
    if not path.is_file():
        raise ValueError("输入文件不存在或不是普通文件。")
    if path.suffix.lower() not in SUPPORTED:
        raise ValueError("只支持 .docx、.pdf、UTF-8 .txt 和 .md；旧版 .doc 请先另存为 .docx。")
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("输入文件超过 100 MiB 限制。")
    return path


def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        from .docx_io import extract_docx
        return extract_docx(path)
    if suffix == ".pdf":
        from .pdf_io import extract_pdf
        return extract_pdf(path)
    try:
        return path.read_bytes().decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError("文本文件必须使用 UTF-8 编码，请先转换编码。") from exc


def _file_hash(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _text_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _inspect(path: Path, text: str) -> dict:
    details = {"metadata_fields": [], "container_markers": [], "warnings": []}
    if path.suffix.lower() == ".docx":
        from .docx_io import inspect_docx
        details = inspect_docx(path)
    elif path.suffix.lower() == ".pdf":
        from .pdf_io import inspect_pdf
        details = inspect_pdf(path)
    extra = details.get("extra_text", "")
    return {
        "filename": path.name,
        "sha256": _file_hash(path),
        "size_bytes": path.stat().st_size,
        "extracted_text_length": len(text),
        "extracted_text_sha256": _text_hash(text),
        "invisible_characters": scan_text(text),
        "extra_text_invisible_characters": scan_text(extra),
        "metadata_fields": details.get("metadata_fields", []),
        "container_markers": details.get("container_markers", []),
        "warnings": details.get("warnings", []),
    }


def inspect_document(path: Path) -> dict:
    path = validate_input(path)
    return {**_inspect(path, extract_text(path)), "limitations": list(LIMITATIONS)}


def _validate_output(source: Path, output: Path, mode: str) -> tuple[Path, Path]:
    _validate_path_name(Path(output))
    output = Path(output).absolute()
    audit = Path(str(output) + ".audit.json")
    if mode not in {"preserve", "rebuild"}:
        raise ValueError("mode 必须为 preserve 或 rebuild。")
    if output.suffix.lower() not in SUPPORTED:
        raise ValueError("不支持的输出格式。")
    if mode == "preserve" and source.suffix.lower() != output.suffix.lower():
        raise ValueError("保版模式输入和输出扩展名必须相同；跨格式请使用 rebuild。")
    if source == output.resolve() or output.exists() or output.is_symlink():
        raise ValueError("禁止覆盖输入或已有文件，请选择一个新的输出文件名。")
    if audit.exists() or audit.is_symlink():
        raise ValueError("审计报告已存在，请选择一个新的输出文件名。")
    if not output.parent.is_dir():
        raise ValueError("输出文件夹不存在，请先创建。")
    return output, audit


def _write_rebuilt(text: str, target: Path) -> None:
    if target.suffix.lower() == ".docx":
        from .docx_io import rebuild_docx
        rebuild_docx(text, target)
    elif target.suffix.lower() == ".pdf":
        from .pdf_io import rebuild_pdf
        rebuild_pdf(text, target)
    else:
        target.write_bytes(text.encode("utf-8"))


def _transform(source: Path, stage: Path, text: str, mode: str, remove: bool) -> dict:
    transform = clean_text if remove else lambda value: value
    if mode == "rebuild" or source.suffix.lower() in {".txt", ".md"}:
        _write_rebuilt(transform(text), stage)
        return {"method": "text_reconstruction", "layout_preserved": False}
    if source.suffix.lower() == ".docx":
        from .docx_io import clean_docx
        return {"method": "docx_xml_cleanup", **(clean_docx(source, stage, transform) or {})}
    from .pdf_io import clean_pdf_metadata
    return {"method": "pdf_metadata_only", **(clean_pdf_metadata(source, stage) or {})}


def _publish(stage: Path, output: Path, audit: Path, report: dict) -> None:
    # Exclusive creation, including the report, prevents accidental overwrites.
    created = []
    try:
        with output.open("xb") as stream:
            created.append(output)
            with stage.open("rb") as content:
                shutil.copyfileobj(content, stream)
        with audit.open("x", encoding="utf-8", newline="\n") as stream:
            created.append(audit)
            json.dump(report, stream, ensure_ascii=False, indent=2)
    except BaseException:
        for path in created:
            path.unlink(missing_ok=True)
        raise


def clean_document(source: Path, output: Path, *, mode: str = "preserve",
                   remove_invisibles: bool = True, allow_lossy: bool = False) -> dict:
    source = validate_input(source)
    output, audit = _validate_output(source, output, mode)
    text = extract_text(source)
    before = _inspect(source, text)
    if mode == "rebuild" and before["container_markers"] and not allow_lossy:
        raise ValueError("检测到复杂文档部件，重建可能丢失内容或布局。请用保版模式，"
                         "或核对检查报告后明确选择 --allow-lossy。部件："
                         + ", ".join(before["container_markers"]))
    metadata_only = mode == "preserve" and source.suffix.lower() == ".pdf"
    expected = clean_text(text) if remove_invisibles and not metadata_only else text
    with tempfile.TemporaryDirectory(prefix="cleanroom-", dir=output.parent) as temp:
        stage = Path(temp) / output.name
        operation = _transform(source, stage, text, mode, remove_invisibles)
        actual = extract_text(stage)
        matches = comparable_text(expected) == comparable_text(actual)
        if not matches:
            raise ValueError("输出复核失败：重新提取的文字与预期不一致，未交付输出。"
                             "可能存在字体、PDF文字编码或复杂文档内容问题，请改用保版模式。")
        after = _inspect(stage, actual)
        if _file_hash(source) != before["sha256"]:
            raise ValueError("处理期间源文件被其他程序修改，请关闭编辑器后重试。")
        report = _build_report(before, after, operation, expected, actual, mode,
                               remove_invisibles, allow_lossy, metadata_only)
        _publish(stage, output, audit, report)
    return report


def _build_report(before, after, operation, expected, actual, mode, remove, lossy, metadata_only):
    notes = ["正文复核忽略空白字符（空格、换行、分页）；这不是视觉排版一致的证明。"]
    if mode == "rebuild":
        notes.append("重建仅保留解析器提取的文字；图片、公式、链接功能、页眉页脚等可能丢失，需人工核对。")
    if metadata_only:
        notes.append("PDF 保版模式只清理所支持的元数据，文字中的不可见字符未清理。")
    return {
        "schema_version": 1,
        "tool_version": __version__,
        "created_at_utc": datetime.now(timezone.utc).isoformat(),
        "options": {"mode": mode, "remove_selected_invisibles": remove, "allow_lossy": lossy},
        "before": before, "after": after, "operation": operation,
        "verification": {
            "expected_text_matches": comparable_text(expected) == comparable_text(actual),
            "comparison": "Unicode codepoint order after removing all whitespace",
            "exact_extracted_text_matches": expected == actual,
            "expected_non_whitespace_sha256": _text_hash(comparable_text(expected)),
            "actual_non_whitespace_sha256": _text_hash(comparable_text(actual)),
            "selected_characters_remaining": after["invisible_characters"]["selected_count"],
            "other_candidates_remaining": after["invisible_characters"]["other_count"],
            "statistical_watermark": "not_tested",
            "ai_detector": "not_tested",
            "visual_layout": "not_verified",
            "conclusion": "只验证报告中列出的可观察项目，不能据此证明不存在 GPT 水印。",
        },
        "notes": notes, "limitations": list(LIMITATIONS),
        "openai_text_watermark": {
            "technology": "textGrain",
            "status": "not_tested",
            "verdict": "unknown",
            "reason": "未获官方文本检测器访问权限；文档重建保留措辞，不能据此判断 textGrain 已移除。",
            "official_announcement": "https://openai.com/index/eu-text-provenance/",
            "documentation_checked_on": "2026-10-11",
        },
    }
