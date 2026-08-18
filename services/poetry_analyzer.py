"""确定性古诗文本分析器第一版。"""
from __future__ import annotations

import hashlib
import re
import unicodedata
from typing import Any

from models.poetry import PoetryEvaluation, PoetryLine, PoetryWork

_PUNCTUATION = "，。！？；：、,.!?;:"


def normalize_poetry_text(text: str) -> tuple[str, list[dict[str, Any]]]:
    original = text or ""
    normalized = unicodedata.normalize("NFC", original).replace("\r\n", "\n").replace("\r", "\n")
    variants: list[dict[str, Any]] = []
    for index, (before, after) in enumerate(zip(original, normalized)):
        if before != after:
            variants.append({"position": index, "before": before, "after": after})
    return normalized, variants


def split_poetry_lines(text: str) -> list[tuple[str, int, int]]:
    normalized, _ = normalize_poetry_text(text)
    lines: list[tuple[str, int, int]] = []
    cursor = 0
    for raw in re.split(r"\n+", normalized):
        value = raw.strip()
        if not value:
            cursor += len(raw) + 1
            continue
        start = normalized.find(value, cursor)
        end = start + len(value)
        lines.append((value, start, end))
        cursor = end
    return lines


def analyze_work(work: PoetryWork, text: str, rule_set_version: str = "poetry-basic-1") -> tuple[list[PoetryLine], PoetryEvaluation]:
    normalized, variants = normalize_poetry_text(text)
    lines: list[PoetryLine] = []
    line_slices = split_poetry_lines(normalized)
    for sequence, (raw, start, end) in enumerate(line_slices):
        clean = "".join(char for char in raw if char not in _PUNCTUATION and not char.isspace())
        line = PoetryLine(
            work_id=work.work_id,
            edition_id=work.edition_ids[0] if work.edition_ids else "unknown-edition",
            sequence=sequence,
            raw_text=raw,
            normalized_text=clean,
            char_start=start,
            char_end=end,
            punctuation_original="".join(char for char in raw if char in _PUNCTUATION),
            variant_chars=variants if sequence == 0 else [],
        )
        lines.append(line)
    input_hash = hashlib.sha256(normalized.encode("utf-8")).hexdigest()
    lengths = [len(line.normalized_text) for line in lines]
    evaluation = PoetryEvaluation(
        work_id=work.work_id,
        rule_set_version=rule_set_version,
        input_hash=input_hash,
        metrics={
            "line_count": len(lines),
            "line_lengths": lengths,
            "uniform_line_length": len(set(lengths)) <= 1 if lengths else True,
            "character_count": sum(lengths),
            "punctuation_preserved": True,
        },
        ambiguous_items=[],
    )
    return lines, evaluation
