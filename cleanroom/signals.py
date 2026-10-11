"""Observable Unicode candidates, never a statistical watermark detector."""

import unicodedata
from collections import Counter

SELECTED = frozenset({"\u200b", "\u2060", "\ufeff", "\u00ad"})
LIMITATIONS = (
    "这些字符只是可枚举的候选标记，出现它们不能证明文档来自 GPT。",
    "未检测词元/词语统计水印；原文字词不变时，重新导出不能保证消除这类水印。",
    "未调用 AI 文本检测器，也不作人类撰写或无法被 AI 检测的判断。",
    "检查范围为解析器可提取的文字和列出的文件结构；未知标记、图像隐写及编码层标记未验证。",
)


def clean_text(text: str) -> str:
    """Remove only four explicitly documented codepoints, without rewriting."""
    return "".join(char for char in text if char not in SELECTED)


def is_candidate(char: str) -> bool:
    return (unicodedata.category(char) == "Cf" or char == "\u034f"
            or 0xFE00 <= ord(char) <= 0xFE0F
            or 0xE0100 <= ord(char) <= 0xE01EF)


def scan_text(text: str) -> dict:
    # Local accumulators keep this O(n), including adversarial inputs containing
    # many different variation selectors. Never collect unbounded positions.
    counts = Counter()
    positions = {}
    for index, char in enumerate(text):
        if not is_candidate(char):
            continue
        counts[char] += 1
        if counts[char] <= 20:
            positions[char] = (*positions.get(char, ()), index)
    return {
        "selected_count": sum(n for char, n in counts.items() if char in SELECTED),
        "other_count": sum(n for char, n in counts.items() if char not in SELECTED),
        "characters": [{
            "codepoint": f"U+{ord(char):04X}",
            "name": unicodedata.name(char, "UNKNOWN"),
            "count": counts[char],
            "selected_for_removal": char in SELECTED,
            "first_positions": list(positions[char]),
        } for char in sorted(counts)],
        "interpretation": "candidate_characters_only_not_proof_of_watermark",
    }


def comparable_text(text: str) -> str:
    """Layout-insensitive check. This explicitly ignores ALL whitespace."""
    return "".join(text.split())
