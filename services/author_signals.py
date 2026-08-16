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
    """登记一条作者信号(编辑器/回流确认时调用;失败不阻断)。"""
    record = {
        "ts": _now(),
        "task_id": task_id,
        "generated_chars": len((generated_text or "").strip()),
        "final_chars": len((final_text or "").strip()),
        "retention": compute_retention(generated_text, final_text),
    }
    target = Path(path or _default_path())
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        with target.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception as exc:
        logger.warning("[AuthorSignals] 落账失败(不阻断): %s", exc)
    return record


def load_author_signals(path=None) -> dict[str, float]:
    """task_id → 最新保留率(后写覆盖)。"""
    source = Path(path or _default_path())
    if not source.exists():
        return {}
    out: dict[str, float] = {}
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
            out[str(rec.get("task_id"))] = float(rec.get("retention", 0) or 0)
        except Exception:
            continue
    return out


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
