from __future__ import annotations

import httpx
import pytest

from api.deps import get_global_router, verify_token
from api.system import get_global_router as get_system_global_router
from core.config_manager import config_manager
from models.cards import CardRelation, InfoCard
from models.library import (
    LibraryCatalog,
    LibraryManifestScanner,
    WorldContextBuilder,
    parse_library_command,
)
from models.system import CommandRequest
from services.dispatcher import ModelDispatcher
from services.global_router import GlobalRouter
from services.indexer import CardIndexer
from services.priority_queue import PriorityTaskQueue
from api.library import _filter_cards_by_quadrant
from main import create_app


def test_quadrant_filter_by_utility_and_entropy():
    """Batch 1：卡片四象限阈值过滤（utility_score / entropy_score 确定性过滤）。"""
    cards = [
        {"card_id": "a", "utility_score": 0.9, "entropy_score": 0.5},
        {"card_id": "b", "utility_score": 0.2, "entropy_score": 0.9},
        {"card_id": "c", "utility_score": 0.8, "entropy_score": 0.8},
        {"card_id": "d", "detail": {"utility_score": 0.7, "entropy_score": 0.6}},
        {"card_id": "e"},  # 无分数 → 视为 0
    ]

    # 仅 utility 阈值
    got = _filter_cards_by_quadrant(cards, min_utility=0.8)
    assert [c["card_id"] for c in got] == ["a", "c"]

    # 仅 entropy 阈值
    got = _filter_cards_by_quadrant(cards, min_entropy=0.8)
    assert [c["card_id"] for c in got] == ["b", "c"]

    # 双阈值（四象限交集）
    got = _filter_cards_by_quadrant(cards, min_utility=0.8, min_entropy=0.6)
    assert [c["card_id"] for c in got] == ["c"]

    # 兼容 ledger 投影的 detail 包装
    got = _filter_cards_by_quadrant(cards, min_utility=0.7)
    assert "d" in [c["card_id"] for c in got]

    # 缺省不过滤
    got = _filter_cards_by_quadrant(cards)
    assert len(got) == len(cards)


def test_manifest_is_hierarchical_and_retrieval_has_trace(tmp_path):
    root = tmp_path / "历史资料"
    branch = root / "研究"
    branch.mkdir(parents=True)
    (root / "00-索引与调用.md").write_text("# 总索引\n", encoding="utf-8")
    (branch / "00-研究索引.md").write_text("# 研究索引\n疫病 时间\n", encoding="utf-8")
    (branch / "01-疫病.md").write_text("# 疫病\n时间与隔离\n", encoding="utf-8")
    manifest_path = tmp_path / "library_manifest.json"
    LibraryManifestScanner(root).write(manifest_path)

    result = LibraryCatalog(manifest_path).retrieve("疫病", limit=1)
    assert result.hits[0].relative_path == "研究/01-疫病.md"
    assert [item["stage"] for item in result.trace[:2]] == ["root_index", "branch_index"]


def test_library_command_protocol_parses_structured_and_natural_language():
    structured = parse_library_command("", {"library_action": "audit"})
    natural = parse_library_command("调用资料：疫病 时间")
    assert structured is not None and structured.action == "audit"
    assert natural is not None and natural.action == "retrieve_material"
    assert natural.query == "疫病 时间"


def test_manifest_audit_distinguishes_missing_orphaned_and_duplicate_entries(tmp_path):
    root = tmp_path / "历史资料"
    root.mkdir()
    manifest_path = tmp_path / "library_manifest.json"
    manifest_path.write_text(
        """{
          "library_id": "historical_materials",
          "library_name": "历史资料",
          "root_path": "历史资料",
          "root_source_document_id": "root",
          "generated_at": "2026-08-16T00:00:00Z",
          "entries": [
            {"source_document_id": "root", "library_id": "historical_materials", "relative_path": "00-index.md", "index_type": "root"},
            {"source_document_id": "duplicate", "library_id": "historical_materials", "relative_path": "missing.md", "parent_source_document_id": "absent"},
            {"source_document_id": "duplicate", "library_id": "historical_materials", "relative_path": "present.md"}
          ]
        }""",
        encoding="utf-8",
    )
    (root / "present.md").write_text("present", encoding="utf-8")

    report = LibraryCatalog(manifest_path).audit()

    assert report.entry_count == 3
    assert report.missing_paths == ["00-index.md", "missing.md"]
    assert report.orphaned_entries == ["duplicate"]
    assert report.duplicate_ids == ["duplicate"]
    assert report.has_duplicate_ids is True


@pytest.mark.asyncio
async def test_scoped_card_relation_and_filter(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：本用例验证 legacy cards/relations 检索语义。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(index_dir=tmp_path)
    await indexer.initialize()
    try:
        card = InfoCard(
            source_book_id="book-a",
            card_sub_type="rule",
            content="188年前不得使用州牧制",
            library_id="historical_materials",
            scope_level="global",
            status="approved",
            evidence_level="A",
            rule_strength="hard",
            valid_time_end="0188",
            relations=[CardRelation(target_card_id="other-card", relation_type="contradicts")],
        )
        await indexer.save_card(card)
        matches = await indexer.search_cards(
            library_id="historical_materials", scope_level="global",
            rule_strength="hard", relation_to="other-card", relation_type="contradicts",
        )
        assert len(matches) == 1
        assert matches[0]["evidence_level"] == "A"
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_world_context_classifies_rules_and_bound_books(tmp_path):
    class FakeIndexer:
        async def search_cards(self, **filters):
            assert filters["source_book"] == "book-a"
            return [
                {"card_id": "hard", "content": "must obey", "status": "approved", "rule_strength": "hard"},
                {"card_id": "reviewed", "content": "prefer", "status": "reviewed", "rule_strength": "advisory"},
                {"card_id": "draft", "content": "verify", "status": "draft", "rule_strength": "none"},
            ]

    catalog = LibraryCatalog(tmp_path / "missing-manifest.json")
    context = await WorldContextBuilder(FakeIndexer(), catalog=catalog).build(
        query="", book_ids=["book-a"]
    )
    assert [card["card_id"] for card in context.must_include] == ["hard"]
    assert [card["card_id"] for card in context.should_include] == ["reviewed"]
    assert [card["card_id"] for card in context.optional_reference] == ["draft"]
    assert context.trace[-1]["stage"] == "cards"


@pytest.mark.asyncio
async def test_world_context_without_bound_books_only_retrieves_material(tmp_path):
    root = tmp_path / "历史资料"
    root.mkdir()
    (root / "00-索引与调用.md").write_text("# 总索引\n", encoding="utf-8")
    (root / "01-疫病.md").write_text("# 疫病\n时间与隔离\n", encoding="utf-8")
    manifest_path = tmp_path / "library_manifest.json"
    LibraryManifestScanner(root).write(manifest_path)

    class FailingIndexer:
        async def search_cards(self, **filters):
            raise AssertionError("未绑定书籍时不应查询全部卡片")

    context = await WorldContextBuilder(
        FailingIndexer(), catalog=LibraryCatalog(manifest_path)
    ).build(query="疫病")

    assert context.must_include == []
    assert context.should_include == []
    assert [item["title"] for item in context.optional_reference] == ["疫病"]
    assert context.trace[-1]["stage"] == "cards"
    assert context.trace[-1]["count"] == 0


@pytest.mark.asyncio
async def test_global_router_runs_material_and_audit_without_card_indexer():
    router = GlobalRouter(
        task_queue=PriorityTaskQueue(),
        system_monitor=object(),
        task_manager=None,
        reflection_trigger=None,
        optimization_applier=None,
        indexer=None,
    )

    material = await router.route_command(
        CommandRequest(command="调用资料：疫病", options={"is_command_mode": True})
    )
    assert material["status"] == "completed"
    assert material["action"] == "retrieve_material"
    assert material["trace"][0]["stage"] == "root_index"

    audit = await router.route_command(
        CommandRequest(command="审计资料库", options={"is_command_mode": True})
    )
    assert audit["status"] == "completed"
    assert audit["action"] == "audit"
    assert audit["report"]["entry_count"] == 52
    assert audit["report"]["missing_paths"] == []


@pytest.mark.asyncio
async def test_system_command_api_exposes_real_library_audit_result():
    app = create_app()
    router = GlobalRouter(
        task_queue=PriorityTaskQueue(),
        system_monitor=object(),
        task_manager=None,
        reflection_trigger=None,
        optimization_applier=None,
        indexer=None,
    )
    app.dependency_overrides[verify_token] = lambda: None
    app.dependency_overrides[get_global_router] = lambda: router
    app.dependency_overrides[get_system_global_router] = lambda: router
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.post(
                "/api/v1/system/command",
                json={"command": "审计资料库", "options": {"is_command_mode": True}},
            )

        assert response.status_code == 200
        payload = response.json()
        assert payload["action"] == "audit"
        assert payload["report"] == {
            "entry_count": 52,
            "missing_paths": [],
            "orphaned_entries": [],
            "duplicate_ids": [],
            "has_duplicate_ids": False,
        }
    finally:
        app.dependency_overrides.clear()


@pytest.mark.asyncio
async def test_dispatcher_keeps_hard_rules_and_traces_budget_overflow(monkeypatch):
    class FakeIndexer:
        async def search_cards(self, **filters):
            return [{
                "card_id": "hard",
                "content": "hard rule " * 20,
                "status": "approved",
                "rule_strength": "hard",
            }]

    dispatcher = object.__new__(ModelDispatcher)
    dispatcher.indexer = FakeIndexer()
    dispatcher.get_context_limit = lambda: 1
    context = await dispatcher._fetch_knowledge_context(["book-a"], query="rule")

    assert "hard rule" in context
    assert dispatcher.last_world_context.trace[-1] == {
        "stage": "context_budget",
        "estimated_tokens": 57,
        "limit": 1,
        "hard_rules_exceed_limit": True,
    }