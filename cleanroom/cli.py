"""Command line entry point with explicit, non-overwriting output."""

import argparse
import json
import sys
from pathlib import Path

from .core import clean_document, inspect_document


def parser() -> argparse.ArgumentParser:
    command = argparse.ArgumentParser(description="本地文档隐藏字符/元数据检查与重建；不检测统计水印。")
    actions = command.add_subparsers(dest="action", required=True)
    inspect = actions.add_parser("inspect", help="只读检查，以 JSON 输出结果")
    inspect.add_argument("input", type=Path)
    clean = actions.add_parser("clean", help="导出新文件和审计报告，禁止覆盖原文件")
    clean.add_argument("input", type=Path)
    clean.add_argument("-o", "--output", type=Path, required=True)
    clean.add_argument("--mode", choices=["preserve", "rebuild"], default="preserve",
                       help="preserve 保版（PDF只清元数据）；rebuild 提取文字重新排版")
    clean.add_argument("--keep-invisibles", action="store_true", help="保留所有不可见字符，仅处理文件结构")
    clean.add_argument("--allow-lossy", action="store_true", help="明确允许重建丢失检测到的复杂部件")
    actions.add_parser("gui", help="打开本地桌面界面")
    return command


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    args = parser().parse_args(argv)
    try:
        if args.action == "gui":
            from .gui import launch
            launch()
            return 0
        if args.action == "inspect":
            report = inspect_document(args.input)
        else:
            report = clean_document(args.input, args.output, mode=args.mode,
                                    remove_invisibles=not args.keep_invisibles,
                                    allow_lossy=args.allow_lossy)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, OSError, ImportError) as exc:
        print(f"处理失败：{exc}", file=sys.stderr)
        return 2
