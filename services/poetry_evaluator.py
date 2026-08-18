"""诗词评测集跑分：对 data/poetry/evaluation_set.json 逐首跑格律/分析并输出通过率。

每首样例声明期望的硬性结果（句数/字数/押韵一致性），本模块用 validate_meter 实际比对：
- 样例「命中」（matched）= 实际结果与期望逐项一致（好诗应通过，坏样例应失败）。
- pass_rate = 命中样例数 / 总样例数，衡量评测集标注正确性与引擎一致性。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from services.poetry_phonology import _METER_PROFILES, _split_lines, validate_meter

_DEFAULT_PATH = Path(__file__).resolve().parent.parent / "data" / "poetry" / "evaluation_set.json"


def _resolve_path(path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    try:
        from core.path_resolver import get_bundle_dir
        return get_bundle_dir() / "data" / "poetry" / "evaluation_set.json"
    except Exception:
        return _DEFAULT_PATH


def load_evaluation_set(path: str | Path | None = None) -> list[dict[str, Any]]:
    resolved = _resolve_path(path)
    if not resolved.exists():
        raise FileNotFoundError(f"评测集不存在: {resolved}")
    data = json.loads(resolved.read_text(encoding="utf-8"))
    items = data.get("items", [])
    return items


def run_evaluation(path: str | Path | None = None) -> dict[str, Any]:
    items = load_evaluation_set(path)
    per_item: list[dict[str, Any]] = []
    matched = 0
    for item in items:
        text = item.get("text", "")
        style = item.get("style", "五绝")
        expect = item.get("expect", {})
        clean = _split_lines(text)
        profile = _METER_PROFILES.get(style, {})
        expected_count = profile.get("line_count")
        expected_lengths = profile.get("line_lengths", [])

        meter = validate_meter(text, style)
        rule_map = {r["name"]: r for r in meter["rules"]}
        actual = {
            "line_count_ok": expected_count is not None and meter["line_count"] == expected_count,
            "line_lengths_ok": bool(expected_lengths) and meter["line_lengths"] == expected_lengths,
            "rhyme_ok": (rule_map.get("rhyme_consistency") or {}).get("status") == "pass",
        }

        # 只有声明的期望字段参与比对
        declared = {k: expect[k] for k in ("line_count_ok", "line_lengths_ok", "rhyme_ok") if k in expect}
        item_matched = all(actual.get(k) == v for k, v in declared.items())
        if item_matched:
            matched += 1

        per_item.append({
            "id": item.get("id"),
            "title": item.get("title"),
            "author": item.get("author", ""),
            "style": style,
            "actual": actual,
            "expected": expect,
            "meter_summary": meter["summary"],
            "meter_ok": meter["ok"],
            "matched": item_matched,
        })

    total = len(items)
    pass_rate = round(matched / total, 4) if total else 0.0
    hard_pass = sum(1 for it in per_item if it["actual"]["line_count_ok"]
                    and it["actual"]["line_lengths_ok"] and it["actual"]["rhyme_ok"])
    return {
        "rule_set_version": "poetry-eval-1",
        "total": total,
        "matched": matched,
        "pass_rate": pass_rate,
        "hard_pass": hard_pass,
        "hard_pass_rate": round(hard_pass / total, 4) if total else 0.0,
        "per_item": per_item,
        "note": "matched = 实际格律结果与标注期望一致；pass_rate 衡量评测集标注正确性（>0 即引擎与数据自洽）。",
    }
