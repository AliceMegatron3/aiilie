"""
api/behavior_plugins.py — 行为插件治理与统计 API(P2)
======================================================
- GET  /plugins/behavior          规格清单+聚合统计
- GET  /plugins/behavior/stats    仅统计(JSONL聚合)
- POST /plugins/behavior/{id}/status  治理流转(active/gray/retired/candidate)
口径:浅知识(QUANTIFIED来源)禁止直接置active——三道门之作者审;
灰度需携带gray_percent,采样确定性由注册表保证。
"""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from api.deps import verify_token
from models.behavior_plugin import PluginSource, PluginStatus
from services.author_signals import join_author_retention, load_author_signals, record_author_signal
from services.behavior_plugins import (
    aggregate_stats,
    behavior_plugin_registry,
    load_registry_overrides,
    load_run_records,
    save_registry_overrides,
)

router = APIRouter(dependencies=[Depends(verify_token)])

_overrides_loaded = False


def _ensure_overrides_loaded() -> None:
    global _overrides_loaded
    if not _overrides_loaded:
        load_registry_overrides(behavior_plugin_registry)
        _overrides_loaded = True


@router.get("/plugins/behavior")
async def list_behavior_plugins():
    _ensure_overrides_loaded()
    stats = aggregate_stats(load_run_records())
    return {
        "plugins": [
            {
                **spec.model_dump(),
                "stats": stats.get(spec.plugin_id, {}),
            }
            for spec in behavior_plugin_registry.list_plugins()
        ]
    }


@router.get("/plugins/behavior/stats")
async def behavior_plugin_stats():
    runs = load_run_records()
    stats = aggregate_stats(runs)
    # P2后续:作者信号接棒——每插件作者保留率(真信号,粗粒度归属)
    retention = join_author_retention(runs, load_author_signals())
    for pid, entry in retention.items():
        stats.setdefault(pid, {"plugin_id": pid, **entry})["author_retention"] = entry["author_retention"]
        stats[pid]["signal_tasks"] = entry["tasks"]
    return {"stats": stats}


class AuthorSignalRequest(BaseModel):
    task_id: str = Field(min_length=1)
    generated_text: str = ""
    final_text: str = ""


@router.post("/plugins/behavior/author-signal")
async def author_signal(req: AuthorSignalRequest):
    """作者确认/改稿后登记真实信号:生成终稿 vs 作者定稿 → 保留率。"""
    record = record_author_signal(req.task_id, req.generated_text, req.final_text)
    return {"signal": record}


class PluginStatusRequest(BaseModel):
    status: str = Field(pattern="^(ACTIVE|GRAY|RETIRED|CANDIDATE)$")
    gray_percent: int = Field(default=0, ge=0, le=100)


@router.post("/plugins/behavior/{plugin_id}/status")
async def set_plugin_status(plugin_id: str, req: PluginStatusRequest):
    _ensure_overrides_loaded()
    spec = behavior_plugin_registry.get(plugin_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    new_status = PluginStatus(req.status)
    # 浅知识三道门:QUANTIFIED 来源禁止未经灰度直接转正
    if (new_status == PluginStatus.ACTIVE
            and spec.source == PluginSource.QUANTIFIED
            and spec.status != PluginStatus.GRAY):
        raise HTTPException(
            status_code=409,
            detail="量化浅知识插件须先经灰度(GRAY)验证方可转正(ACTIVE)",
        )
    updated = behavior_plugin_registry.set_status(plugin_id, new_status, req.gray_percent)
    save_registry_overrides(behavior_plugin_registry)
    return {"plugin": updated.model_dump() if updated else None}
