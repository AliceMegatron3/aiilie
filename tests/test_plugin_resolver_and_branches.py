"""插件解析器与法则分支不可变 revision 测试。"""
from __future__ import annotations

import pytest

from models.ledger import LawBranchRecord
from services.plugin_resolver import PluginResolver
from services.indexer import CardIndexer


def test_plugin_resolver_rejects_untrusted_and_conflict():
    resolver = PluginResolver([
        {
            "plugin_id": "extractor.a", "trust_state": "TRUSTED",
            "capabilities": ["read_passage"],
            "requires": [], "conflicts": ["extractor.b"],
            "resource_budget": {"timeout_seconds": 30},
        },
        {
            "plugin_id": "extractor.b", "trust_state": "UNTRUSTED",
            "capabilities": ["read_passage"], "requires": [], "conflicts": [],
        },
    ])
    report = resolver.resolve(
        ["extractor.a", "extractor.b"],
        required_capabilities=["read_passage"],
        caller_capabilities=["read_passage"],
    )
    assert report["decision"] == "DENY"
    assert any("未信任" in error for error in report["errors"])
    assert any("冲突" in error for error in report["errors"])


def test_plugin_resolver_rejects_cycle():
    resolver = PluginResolver([
        {"plugin_id": "a", "trust_state": "TRUSTED", "requires": ["b"], "capabilities": []},
        {"plugin_id": "b", "trust_state": "TRUSTED", "requires": ["a"], "capabilities": []},
    ])
    report = resolver.resolve(["a"])
    assert report["decision"] == "DENY"
    assert any("循环依赖" in error for error in report["errors"])


@pytest.mark.asyncio
async def test_law_branch_override_and_rollback_are_new_revisions(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        branch = await indexer.create_law_branch(LawBranchRecord(branch_id="world-1", name="主世界"))
        assert branch["current_revision"] == 0
        first = await indexer.append_law_revision("world-1", "override", {"qi_density": 3}, "作者加入灵气")
        second = await indexer.append_law_revision("world-1", "rollback", {"qi_density": 0}, "回滚实验")
        assert first["revision"] == 1
        assert second["revision"] == 2
        revisions = await indexer.list_law_revisions("world-1")
        assert [item["revision"] for item in revisions] == [2, 1, 0]
        assert revisions[1]["snapshot"] == {"qi_density": 3}
    finally:
        await indexer.close()
