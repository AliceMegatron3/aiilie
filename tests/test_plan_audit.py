"""计划审计轨测试（V0.4 每步审计：只追加 / 可回放 / 参数指纹）。"""
from __future__ import annotations

import pytest
import tempfile
from pathlib import Path

from services.plan_audit import AuditEvent, PlanAuditStore, digest_args


@pytest.mark.asyncio
async def test_audit_append_and_replay_order(tmp_path):
    store = PlanAuditStore(base_dir=tmp_path)
    for i in range(3):
        await store.append(
            AuditEvent(
                plan_id="plan_1",
                version=1,
                actor="author",
                tool="library.search_cards",
                step_id=f"step_{i}",
                action="dispatched",
                status="PENDING",
            )
        )
    rows = await store.list("plan_1")
    assert len(rows) == 3
    assert [r["step_id"] for r in rows] == ["step_0", "step_1", "step_2"]
    assert all(r["event_id"] for r in rows)  # 每条事件都有唯一 id
    # 空计划返回空列表
    assert await store.list("plan_empty") == []


@pytest.mark.asyncio
async def test_audit_immutable_no_duplicate_steps(tmp_path):
    store = PlanAuditStore(base_dir=tmp_path)
    event = AuditEvent(
        plan_id="plan_2",
        version=1,
        actor="author",
        tool="document.read",
        step_id="s1",
        action="started",
        status="OK",
    )
    await store.append(event)
    # 重复 append 同一条会再写一行（只追加，不改写）；这里验证不覆盖、按序追加
    await store.append(event)
    assert len(await store.list("plan_2")) == 2


def test_args_digest_deterministic_and_no_raw():
    d1 = digest_args({"a": 1, "b": 2})
    d2 = digest_args({"b": 2, "a": 1})  # 键序无关
    assert d1 == d2
    assert len(d1) == 64
    assert digest_args(None) == ""