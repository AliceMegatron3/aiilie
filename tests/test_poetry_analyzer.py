"""诗词确定性分析 golden fixtures。"""
from __future__ import annotations

from services.poetry_analyzer import analyze_work, normalize_poetry_text
from models.poetry import PoetryWork


def test_poetry_preserves_normalized_text_and_line_offsets():
    work = PoetryWork(title="静夜思", edition_ids=["edition-1"])
    lines, evaluation = analyze_work(work, "床前明月光，\n疑是地上霜。")
    assert [line.normalized_text for line in lines] == ["床前明月光", "疑是地上霜"]
    assert evaluation.metrics["line_count"] == 2
    assert evaluation.metrics["line_lengths"] == [5, 5]
    assert lines[0].char_start == 0
    assert lines[0].char_end > lines[0].char_start


def test_unicode_normalization_reports_variants_without_erasing_source():
    normalized, variants = normalize_poetry_text("e\u0301")
    assert normalized == "é"
    assert variants
