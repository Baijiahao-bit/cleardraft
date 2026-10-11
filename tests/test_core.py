import json
import subprocess
import sys
from pathlib import Path

import pytest

from cleanroom.core import clean_document, inspect_document
from cleanroom.signals import clean_text, scan_text


def test_selected_controls_are_removed_but_joiners_and_spacing_stay():
    text = "你\u200b好\u2060世\ufeff界\u00ad! 👩\u200d💻 فارسی\u200c A\u00a0B\n"
    assert clean_text(text) == "你好世界! 👩\u200d💻 فارسی\u200c A\u00a0B\n"
    assert scan_text(text)["selected_count"] == 4
    assert scan_text(text)["other_count"] == 2


def test_clean_text_roundtrip_and_audit(tmp_path):
    source = tmp_path / "中文.txt"
    source.write_text("Hello\u200b 世界\ufeff\n", encoding="utf-8")
    output = tmp_path / "result.txt"
    report = clean_document(source, output)
    assert output.read_text(encoding="utf-8") == "Hello 世界\n"
    assert source.read_text(encoding="utf-8") == "Hello\u200b 世界\ufeff\n"
    assert report["before"]["invisible_characters"]["selected_count"] == 2
    assert report["after"]["invisible_characters"]["selected_count"] == 0
    assert report["verification"]["expected_text_matches"]
    assert report["verification"]["statistical_watermark"] == "not_tested"
    assert json.loads(Path(str(output) + ".audit.json").read_text(encoding="utf-8")) == report


def test_keep_invisibles(tmp_path):
    source = tmp_path / "a.md"
    source.write_text("a\u200bb", encoding="utf-8")
    report = clean_document(source, tmp_path / "b.md", remove_invisibles=False)
    assert report["after"]["invisible_characters"]["selected_count"] == 1


def test_many_candidate_types_and_bounded_positions():
    selectors = "".join(chr(i) for i in range(0xE0100, 0xE01F0))
    result = scan_text("A" * 400_000 + selectors + "\u200b" * 100)
    assert result["selected_count"] == 100
    assert result["other_count"] == 240
    assert all(len(item["first_positions"]) <= 20 for item in result["characters"])


@pytest.mark.parametrize("case", ["same", "exists", "audit_exists", "bad_mode", "bad_extension"])
def test_reject_unsafe_outputs(tmp_path, case):
    source = tmp_path / "a.txt"
    source.write_text("safe", encoding="utf-8")
    target = tmp_path / "b.txt"
    mode = "preserve"
    if case == "same":
        target = source
    elif case == "exists":
        target.write_text("keep", encoding="utf-8")
    elif case == "audit_exists":
        Path(str(target) + ".audit.json").write_text("keep", encoding="utf-8")
    elif case == "bad_mode":
        mode = "unknown"
    else:
        target = tmp_path / "b.exe"
    with pytest.raises(ValueError):
        clean_document(source, target, mode=mode)
    assert source.read_text() == "safe"


def test_invalid_input(tmp_path):
    with pytest.raises(ValueError):
        inspect_document(tmp_path / "missing.txt")
    bad = tmp_path / "bad.txt"
    bad.write_bytes(b"\xff\xff")
    with pytest.raises(ValueError, match="UTF-8"):
        inspect_document(bad)


def test_cli_end_to_end(tmp_path):
    source = tmp_path / "source.txt"
    source.write_text("测试\u200b通过", encoding="utf-8")
    out = tmp_path / "out.txt"
    result = subprocess.run([sys.executable, "-m", "cleanroom", "clean", str(source), "-o", str(out)], capture_output=True)
    assert result.returncode == 0, result.stderr
    assert out.read_text(encoding="utf-8") == "测试通过"
    failure = subprocess.run([sys.executable, "-m", "cleanroom", "clean", str(source), "-o", str(out)], capture_output=True)
    assert failure.returncode != 0
