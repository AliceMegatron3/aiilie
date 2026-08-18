"""P2 学习体进化回归测试:效果统计+治理流转+浅知识三道门。"""
from __future__ import annotations

import pytest

from models.behavior_plugin import (
    BehaviorPluginRunRecord,
    BehaviorPluginSpec,
    PluginSource,
    PluginStatus,
)
from services.behavior_plugins import (
    BehaviorPluginRegistry,
    aggregate_stats,
    load_registry_overrides,
    load_run_records,
    persist_run_records,
    save_registry_overrides,
)


def _rec(plugin_id: str, triggered=True, accepted=True, duration_ms=100):
    return BehaviorPluginRunRecord(
        plugin_id=plugin_id, task_id="t", triggered=triggered,
        accepted=accepted, duration_ms=duration_ms,
    )


def test_aggregate_stats_rates_and_durations():
    records = [
        _rec("a", accepted=True, duration_ms=100),
        _rec("a", accepted=True, duration_ms=300),
        _rec("a", accepted=False, duration_ms=50),
        _rec("b", triggered=False),
    ]
    stats = aggregate_stats(records)
    assert stats["a"]["runs"] == 3 and stats["a"]["triggered"] == 3
    assert stats["a"]["accepted"] == 2
    assert stats["a"]["acceptance_rate"] == round(2 / 3, 4)
    assert stats["a"]["avg_duration_ms"] == 200  # 仅按已接受样本均摊
    assert stats["b"]["triggered"] == 0 and stats["b"]["acceptance_rate"] == 0.0


def test_runs_jsonl_roundtrip(tmp_path):
    path = tmp_path / "runs.jsonl"
    persist_run_records([_rec("a"), _rec("b", accepted=False)], history_path=path)
    persist_run_records([_rec("a")], history_path=path)
    loaded = load_run_records(history_path=path)
    assert [r.plugin_id for r in loaded] == ["a", "b", "a"]
    # 脏行容忍
    path.write_text(path.read_text(encoding="utf-8") + "{bad json}\n", encoding="utf-8")
    assert len(load_run_records(history_path=path)) == 3


def test_registry_overrides_roundtrip(tmp_path):
    reg = BehaviorPluginRegistry()
    reg.register(BehaviorPluginSpec(
        plugin_id="q2", name="量化件", prompt_template_id="behavior_editor_style",
        source=PluginSource.QUANTIFIED,
    ))
    # 治理:灰度50
    reg.set_status("q2", PluginStatus.GRAY, gray_percent=50)
    path = tmp_path / "overrides.json"
    save_registry_overrides(reg, path=path)

    reg2 = BehaviorPluginRegistry()
    reg2.register(BehaviorPluginSpec(
        plugin_id="q2", name="量化件", prompt_template_id="behavior_editor_style",
        source=PluginSource.QUANTIFIED,
    ))
    load_registry_overrides(reg2, path=path)
    assert reg2.get("q2").status == PluginStatus.GRAY
    assert reg2.get("q2").gray_percent == 50


def test_quantified_gate_cannot_jump_to_active(tmp_path):
    """浅知识三道门:QUANTIFIED 不可从 CANDIDATE 直接 ACTIVE(须先灰度)。"""
    from fastapi import HTTPException
    from api.behavior_plugins import set_plugin_status, PluginStatusRequest

    reg = BehaviorPluginRegistry()
    from api import behavior_plugins as bp_api

    bp_api.behavior_plugin_registry = reg  # 注入测试注册表
    reg.register(BehaviorPluginSpec(
        plugin_id="q3", name="量化件", prompt_template_id="behavior_editor_style",
        source=PluginSource.QUANTIFIED,
    ))
    with pytest.raises(HTTPException) as exc_info:
        import asyncio

        asyncio.run(set_plugin_status("q3", PluginStatusRequest(status="ACTIVE")))
    assert exc_info.value.status_code == 409
    # 合法路径:CANDIDATE→GRAY→ACTIVE
    asyncio.run(set_plugin_status("q3", PluginStatusRequest(status="GRAY", gray_percent=30)))
    assert reg.get("q3").status == PluginStatus.GRAY
    # 灰度采样在运行时生效(灰度态下约三成命中)
    from models.behavior_plugin import TriggerContext

    hits = sum(1 for i in range(100) if any(p.plugin_id == "q3" for p in reg.active_for(TriggerContext(task_id=f"t{i}"))))
    assert 15 <= hits <= 45, "30%灰度采样应接近三成命中"
    result = asyncio.run(set_plugin_status("q3", PluginStatusRequest(status="ACTIVE")))
    assert result["data"]["plugin"]["status"] == "ACTIVE"
    bp_api.behavior_plugin_registry = BehaviorPluginRegistry()  # 还原


def test_governance_endpoints_mounted():
    from api.api_router import api_router
    from tests.conftest import flatten_api_router

    paths = {r.path for r in flatten_api_router(api_router)}
    assert any("/plugins/behavior" in p for p in paths)
    assert any("/grayscale" in p and "skills" in p for p in paths)
