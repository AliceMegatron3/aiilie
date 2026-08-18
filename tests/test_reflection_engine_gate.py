"""Batch 4：反思引擎 LLM 输出门——无模型/模型失败 → 显式 NEEDS_REVIEW，
不伪装为有效反思。"""
from __future__ import annotations

import json

import pytest

from services.reflection_engine import ReflectionEngine


class _FailingClient:
    async def generate_completion(self, prompt, **kw):
        raise RuntimeError("provider down")


@pytest.mark.asyncio
async def test_no_model_client_returns_needs_review(tmp_path):
    engine = ReflectionEngine()  # 未注入模型客户端
    out = await engine.execute_final_reflection("proj-x", ["材料A", "材料B"])
    try:
        data = json.loads(out)
    except Exception:
        raise AssertionError("降级输出必须是合法 JSON")
    assert data["status"] == "NEEDS_REVIEW"  # 不得伪装成功
    assert data["rule_candidates"] == []
    assert data["skill_candidates"] == []
    assert any("模型不可用" in q for q in data["author_questions"])


@pytest.mark.asyncio
async def test_failed_model_returns_needs_review_not_success(tmp_path):
    engine = ReflectionEngine()
    engine.set_model_client(_FailingClient())  # 模型调用失败
    out = await engine.execute_final_reflection("proj-y", ["材料X"])
    data = json.loads(out)
    assert data["status"] == "NEEDS_REVIEW"
    assert data["observations"] == []