"""services/document_confirmation.py — 文档生成基线与作者确认(PA)
====================================================================
普通保存是工作过程，不是作者评价。本模块明确区分：

1. set_generation_baseline: 某任务生成的内容进入文档编辑器时登记基线；
2. author_confirm: 作者明确点“确认定稿”时，才把当前内容与基线比较，
   生成 author_signal 并清除基线；
3. 自动保存/普通 PUT 不调用这里，绝不污染作者信号。

基线存储为独立 JSON 文件，内容仅限当前待确认生成稿，确认/撤销后删除。
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _baseline_path(doc_id: str) -> Path:
    from core.path_resolver import get_app_data_dir

    return get_app_data_dir() / "document_generation_baselines" / f"{doc_id}.json"


def set_generation_baseline(doc_id: str, task_id: str, generated_text: str) -> dict:
    if not doc_id or not task_id or not (generated_text or "").strip():
        raise ValueError("doc_id、task_id 与 generated_text 均不能为空")
    record = {
        "doc_id": doc_id,
        "task_id": task_id,
        "generated_text": generated_text,
        "created_at": _now(),
    }
    path = _baseline_path(doc_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False), encoding="utf-8")
    return record


def get_generation_baseline(doc_id: str) -> dict | None:
    path = _baseline_path(doc_id)
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not data.get("task_id") or not str(data.get("generated_text") or "").strip():
            return None
        return data
    except (OSError, json.JSONDecodeError):
        return None


def clear_generation_baseline(doc_id: str) -> None:
    path = _baseline_path(doc_id)
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def confirm_author_final(doc_id: str, final_text: str) -> dict:
    """用待确认基线生成作者信号并消费基线，避免重复确认。"""
    baseline = get_generation_baseline(doc_id)
    if baseline is None:
        raise ValueError("当前文档没有待确认的生成基线")
    if not (final_text or "").strip():
        raise ValueError("定稿内容不能为空")
    from services.author_signals import record_author_signal

    signal = record_author_signal(
        str(baseline["task_id"]), str(baseline["generated_text"]), final_text
    )
    clear_generation_baseline(doc_id)
    return {
        "task_id": baseline["task_id"],
        "baseline_created_at": baseline["created_at"],
        "signal": signal,
    }
