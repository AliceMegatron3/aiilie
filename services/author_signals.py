"""
services/author_signals.py — 作者真实信号回流(P2后续:真实指标接棒)
=====================================================================
验收率只是机器自评;本模块引入**作者信号**:作者确认/改稿后,
对比"生成终稿 vs 作者定稿"得到保留率(difflib确定性相似度),
按 task_id 与打磨运行记录连接,聚合成每插件的"作者保留率"——
学习体的真信号(替代 len>50 假指标链路的最后一环)。

归属口径(诚实粗粒度):同任务中所有验收通过的插件共享该任务的
保留率;插件级差异需后续按"逐趟快照"细化。
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _default_path() -> Path:
    from core.path_resolver import get_app_data_dir
    return get_app_data_dir() / "author_signals.jsonl"


def compute_retention(generated_text: str, final_text: str) -> float:
    """确定性保留率:作者定稿对生成终稿的相似比(1=原样保留,0=重写)。"""
    a = (generated_text or "").strip()
    b = (final_text or "").strip()
    if not a or not b:
        return 0.0
    return round(SequenceMatcher(None, a, b).ratio(), 4)


def record_author_signal(
    task_id: str, generated_text: str, final_text: str, path=None
) -> dict:
    """登记一条作者信号(仅作者明确确认时调用;失败不阻断)。

    批次C:附带生成稿与定稿指纹,便于与打磨通行的 output_digest 比对,
    判断"作者最终保留的是哪一趟通行的产物"(插件级归因)。
    """
    from services.telemetry_store import append_record, resolve_path, text_digest

    record = {
        "ts": _now(),
        "task_id": task_id,
        "generated_chars": len((generated_text or "").strip()),
        "final_chars": len((final_text or "").strip()),
        "retention": compute_retention(generated_text, final_text),
        "generated_digest": text_digest(generated_text),
        "final_digest": text_digest(final_text),
    }
    append_record(resolve_path("author_signals.jsonl", path), record)
    return record


def load_author_signals(path=None) -> dict[str, float]:
    """task_id → 最新保留率(后写覆盖)。"""
    from services.telemetry_store import read_records, resolve_path

    out: dict[str, float] = {}
    for rec in read_records(resolve_path("author_signals.jsonl", path)):
        task_id = str(rec.get("task_id") or "")
        if not task_id:
            continue
        try:
            out[task_id] = float(rec.get("retention", 0) or 0)
        except (TypeError, ValueError):
            continue
    return out


def load_author_signal_records(path=None) -> list[dict]:
    """完整作者信号记录(含指纹),供插件级归因使用。"""
    from services.telemetry_store import read_records, resolve_path

    return read_records(resolve_path("author_signals.jsonl", path))


def attribute_plugin_outcomes(run_records, signal_records: list[dict]) -> dict[str, dict]:
    """插件级归因:作者定稿指纹是否等于某趟通行的产物指纹。

    kept_final = 该趟通行的产物正是作者定稿(未再改动);
    superseded = 该趟被采纳但作者定稿指纹不同(后续通行或作者改写)。
    仅用指纹比对,不保存正文。
    """
    finals: dict[str, str] = {}
    for rec in signal_records:
        task_id = str(rec.get("task_id") or "")
        digest = str(rec.get("final_digest") or "")
        if task_id and digest:
            finals[task_id] = digest
    acc: dict[str, dict] = {}
    for run in run_records:
        if not run.accepted or not run.output_digest:
            continue
        final_digest = finals.get(run.task_id)
        if not final_digest:
            continue
        entry = acc.setdefault(
            run.plugin_id,
            {"plugin_id": run.plugin_id, "attributed_runs": 0, "kept_final": 0, "superseded": 0},
        )
        entry["attributed_runs"] += 1
        if run.output_digest == final_digest:
            entry["kept_final"] += 1
        else:
            entry["superseded"] += 1
    for entry in acc.values():
        total = entry["attributed_runs"]
        entry["kept_final_rate"] = round(entry["kept_final"] / total, 4) if total else 0.0
    return acc


def join_author_retention(run_records, signals: dict[str, float]) -> dict[str, dict]:
    """运行记录×作者信号 → 每插件作者保留率(验收通过且该任务有信号者)。"""
    from models.behavior_plugin import BehaviorPluginRunRecord  # noqa: F401

    acc: dict[str, dict] = {}
    for rec in run_records:
        if not rec.accepted or rec.task_id not in signals:
            continue
        entry = acc.setdefault(rec.plugin_id, {"plugin_id": rec.plugin_id, "tasks": 0, "author_retention": 0.0})
        entry["tasks"] += 1
        entry["author_retention"] += signals[rec.task_id]
    for entry in acc.values():
        if entry["tasks"]:
            entry["author_retention"] = round(entry["author_retention"] / entry["tasks"], 4)
    return acc
