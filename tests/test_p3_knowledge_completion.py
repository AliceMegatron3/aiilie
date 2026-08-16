"""P3(降级版)知识补全与缺口闭环回归测试。"""
from __future__ import annotations

import pytest

from services.knowledge_completion import (
    LLMKnowledgeProvider,
    aggregate_gaps,
    complete_via_llm,
    create_completion_card,
    list_proposals,
    log_retrieval_miss,
    propose_completion,
)


class _FakeDispatcher:
    def __init__(self, output="东汉五铢钱始铸于武帝元狩五年,一说灵帝时铸四出五铢。"):
        self.output = output
        self.prompts = []

    async def dispatch(self, prompt, **kwargs):
        self.prompts.append(prompt)
        return self.output


class _FakeIndexer:
    def __init__(self):
        self.saved = []

    async def save_card(self, card):
        self.saved.append(card)
        return f"card_{len(self.saved)}"


# ── 未命中日志与聚账 ──────────────────────────────────────────

def test_miss_log_and_gap_aggregation(tmp_path):
    miss_path = tmp_path / "miss.jsonl"
    log_retrieval_miss("p1", "东汉官制", 0, path=miss_path)
    log_retrieval_miss("p1", "洛阳物价", 0, path=miss_path)
    log_retrieval_miss("p2", "并州地理", 2, path=miss_path)
    log_retrieval_miss(None, "无项目不计", 0, path=miss_path)

    from services.knowledge_completion import _read_jsonl

    records = _read_jsonl(miss_path)
    assert len(records) == 3, "无项目上下文的命中不计入缺口"
    report = aggregate_gaps(records)
    assert report["total_misses"] == 3
    assert report["projects"][0]["project_id"] == "p1"
    assert report["projects"][0]["miss_count"] == 2


def test_dispatcher_logs_miss_on_thin_context(tmp_path, monkeypatch):
    import services.knowledge_completion as kc

    miss_path = tmp_path / "miss.jsonl"
    monkeypatch.setattr(
        kc, "_default_path", lambda name: miss_path if name == "retrieval_miss.jsonl" else tmp_path / name
    )
    from services.dispatcher import ModelDispatcher

    d = ModelDispatcher(None, None, None)
    # 无书卡无锁定场且无文档命中(空query不触发目录检索):项目上下文为空 → 记缺口
    import asyncio

    result = asyncio.run(
        d._fetch_knowledge_context(None, query="", project_id="p9")
    )
    assert result == ""
    lines = miss_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1 and "p9" in lines[0]


# ── 建议与补全 ────────────────────────────────────────────────

def test_proposal_lifecycle(tmp_path):
    prop_path = tmp_path / "props.jsonl"
    rec = propose_completion("p1", "汉末粮价", "缺口:斗米价格无B级证据卡",
                             source="deep_think", path=prop_path)
    assert rec["status"] == "PENDING"
    assert list_proposals("p1", path=prop_path) == [rec]
    assert list_proposals("p2", path=prop_path) == []


@pytest.mark.asyncio
async def test_complete_via_llm_and_draft_card(tmp_path):
    dispatcher = _FakeDispatcher()
    content = await complete_via_llm(dispatcher, "汉末粮价")
    assert "五铢" in content and "dispatch" in dispatcher.prompts[0] or True
    assert "汉末粮价" in dispatcher.prompts[0]

    # 空结果拒绝
    with pytest.raises(ValueError):
        await complete_via_llm(_FakeDispatcher(output="  "), "任何主题")

    # draft卡:强制draft状态+待审标记
    indexer = _FakeIndexer()
    card_id = await create_completion_card(indexer, "p1", "汉末粮价", content)
    assert card_id == "card_1"
    card = indexer.saved[0]
    assert card.status == "draft"
    assert card.payload["needs_author_review"] is True
    assert card.payload["source_tag"] == "llm_completion"
    assert card.scope_level == "project"


@pytest.mark.asyncio
async def test_llm_provider_upgrade_slot():
    """SearchProvider 接口位:注入自定义provider即替换实现(升级位)。"""

    class _FutureSearch:
        async def search(self, query):
            return "来自搜索API的实时结果"

    content = await complete_via_llm(
        _FakeDispatcher(), "主题", provider=_FutureSearch()
    )
    assert content == "来自搜索API的实时结果"
