"""
tests/test_ledger_source_registration.py — Ledger 来源契约登记测试
====================================================================
覆盖：
- register_real_source_documents：真实来源文档/段落登记、dry-run 不写库、幂等 UPSERT；
- mark_legacy_cards_derived：无来源旧卡标记为 derived/draft、同步冷 JSON、
  不覆盖 archived、冷文件缺失计入 skipped_cold、幂等；
- verify_source_contract：不产生任何 authoritative claim。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from services.indexer import CardIndexer
from services.ledger_source_registration import (
    mark_legacy_cards_derived,
    register_real_source_documents,
    verify_source_contract,
)

pytestmark = pytest.mark.asyncio


@pytest.fixture
async def indexer(tmp_path):
    idx = CardIndexer(tmp_path / "index")
    await idx.initialize()
    yield idx
    await idx.close()


async def _count(indexer: CardIndexer, table: str) -> int:
    cursor = await indexer.conn.execute(f"SELECT COUNT(*) FROM {table}")
    row = await cursor.fetchone()
    return int(row[0])


async def _insert_legacy_card(
    indexer: CardIndexer,
    tmp_path: Path,
    card_id: str,
    source_book: str,
    status: str = "draft",
    evidence_level: str = "unknown",
    with_cold: bool = True,
) -> None:
    """用 raw SQL 直接插入无来源旧卡（不触发 save_cards 的 ledger 投影）。"""
    if with_cold:
        cold_path = tmp_path / f"{card_id}.card"
        cold_path.write_text(
            json.dumps(
                {"card_id": card_id, "source_book_id": source_book,
                 "content": "测试内容", "status": status, "evidence_level": evidence_level},
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    else:
        cold_path = tmp_path / f"missing_{card_id}.card"  # 指向不存在的冷文件
    await indexer.conn.execute(
        """INSERT INTO cards (
            card_id, source_book, source_chapter, card_type, category, subtype,
            summary, content, tags, create_time, entropy_score, utility_score,
            original_fragment, payload, detail_path, library_id, knowledge_type,
            book_type_ids, domain, scope_level, status, evidence_level,
            rule_strength, source_document_id, source_anchor, valid_time_start,
            valid_time_end, valid_places, related_card_ids
        ) VALUES (?, ?, '', 'info', 'misc', '世界观', ?, ?, '[]', '2026-01-01T00:00:00+00:00',
                  0.5, 0.5, '', '{}', ?, '', 'FACT', '[]', '', 'book', ?, ?,
                  'none', '', '{}', NULL, NULL, '[]', '[]')""",
        (card_id, source_book, "测试内容", "测试内容", str(cold_path), status, evidence_level),
    )
    await indexer.conn.commit()


async def test_register_real_source_documents(indexer, tmp_path):
    src_root = tmp_path / "sources"
    (src_root / "sub").mkdir(parents=True)
    (src_root / "doc1.md").write_text("# Title A\n\npara one\n\npara two\n", encoding="utf-8")
    (src_root / "sub" / "doc2.md").write_text("# Sub B\n\nsome text\n", encoding="utf-8")

    manifest = {
        "manifest_version": "1.0",
        "library_id": "test_lib",
        "root_path": "",
        "entries": [
            {"source_document_id": "test_lib:aaaa", "relative_path": "doc1.md", "title": "Doc One"},
            {"source_document_id": "test_lib:bbbb", "relative_path": "sub/doc2.md", "title": "Doc Two"},
            {"source_document_id": "test_lib:cccc", "relative_path": "missing.md", "title": "Missing"},
        ],
    }
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")

    # dry-run：只返回计划，不写库
    plan = await register_real_source_documents(indexer, manifest_path, [src_root], dry_run=True)
    assert plan["dry_run"] is True
    assert plan["registered_documents"] == 2
    assert plan["registered_passages"] == 5
    assert plan["skipped"] == ["missing.md"]
    assert plan["errors"] == []
    assert await _count(indexer, "ledger_documents") == 0
    assert await _count(indexer, "ledger_passages") == 0

    # 真实登记
    result = await register_real_source_documents(indexer, manifest_path, [src_root])
    assert result["registered_documents"] == 2
    assert result["registered_passages"] == 5
    assert await _count(indexer, "ledger_documents") == 2
    assert await _count(indexer, "ledger_passages") == 5

    # 幂等：二次调用不产生重复行
    again = await register_real_source_documents(indexer, manifest_path, [src_root])
    assert again["registered_documents"] == 2
    assert await _count(indexer, "ledger_documents") == 2
    assert await _count(indexer, "ledger_passages") == 5

    # 段落内容与切分核对
    cursor = await indexer.conn.execute(
        "SELECT sequence, heading, quote, char_start, char_end FROM ledger_passages"
        " WHERE document_id='test_lib:aaaa' ORDER BY sequence"
    )
    rows = await cursor.fetchall()
    assert len(rows) == 3
    assert rows[0]["heading"] == "Title A"
    assert rows[0]["quote"] == "# Title A"
    assert rows[1]["quote"] == "para one"
    assert rows[2]["quote"] == "para two"
    for row in rows:
        assert row["char_end"] >= row["char_start"]

    cursor = await indexer.conn.execute(
        "SELECT media_type, parser_id, status, content_hash FROM ledger_documents WHERE document_id='test_lib:aaaa'"
    )
    doc = await cursor.fetchone()
    assert doc["media_type"] == "text/markdown"
    assert doc["parser_id"] == "manifest"
    assert doc["status"] == "active"
    assert len(doc["content_hash"]) == 64


async def test_mark_legacy_cards_derived(indexer, tmp_path):
    await _insert_legacy_card(indexer, tmp_path, "legacy-1", "book_legacy")
    await _insert_legacy_card(indexer, tmp_path, "legacy-2", "book_legacy")

    result = await mark_legacy_cards_derived(indexer, "book_legacy")
    assert result["total_cards"] == 2
    assert result["updated_cards"] == 2
    assert result["skipped_cold"] == 0
    assert result["errors"] == []

    cursor = await indexer.conn.execute(
        "SELECT status, evidence_level, detail_path FROM cards WHERE card_id IN ('legacy-1', 'legacy-2')"
    )
    for row in await cursor.fetchall():
        assert row["status"] == "draft"
        assert row["evidence_level"] == "derived"
        cold = json.loads(Path(row["detail_path"]).read_text(encoding="utf-8"))
        assert cold["status"] == "draft"
        assert cold["evidence_level"] == "derived"

    # 幂等：二次调用不再更新
    again = await mark_legacy_cards_derived(indexer, "book_legacy")
    assert again["total_cards"] == 2
    assert again["updated_cards"] == 0

    # 不产生任何 authoritative claim
    contract = await verify_source_contract(indexer, source_book="book_legacy")
    assert contract["ledger_claims"] == 0
    assert contract["legacy_derived_cards"] == 2
    assert contract["empty_source_document_id_cards"] == 2
    assert contract["ledger_documents"] == 0
    assert contract["ledger_passages"] == 0


async def test_mark_derived_keyset_pagination_skips_no_rows(indexer, tmp_path):
    """Batch 3 回归：强制多页 keyset 遍历（小 batch），逐行推进不得跳行或重复。

    旧 OFFSET 实现会在处理中更新命中行时因结果集位移而跳行；keyset 以
    rowid>游标推进，batch_size 远小于总数时必须精确处理每一行且仅一次。
    """
    total = 7
    batch = 2  # 4 批
    for i in range(total):
        await _insert_legacy_card(indexer, tmp_path, f"page-{i}", "book_paged")

    result = await mark_legacy_cards_derived(indexer, "book_paged", batch_size=batch)
    assert result["total_cards"] == total
    assert result["updated_cards"] == total
    assert result["errors"] == []

    cursor = await indexer.conn.execute(
        "SELECT COUNT(*) FROM cards WHERE source_book='book_paged' AND evidence_level='derived'"
    )
    assert (await cursor.fetchone())[0] == total

    # 幂等：再次以多页运行不再重复更新（不重复副作用）
    again = await mark_legacy_cards_derived(indexer, "book_paged", batch_size=batch)
    assert again["total_cards"] == total
    assert again["updated_cards"] == 0


async def test_mark_legacy_preserves_archived_and_counts_skipped_cold(indexer, tmp_path):
    await _insert_legacy_card(indexer, tmp_path, "arch-1", "book_arch", status="archived")
    await _insert_legacy_card(indexer, tmp_path, "arch-2", "book_arch", with_cold=False)

    result = await mark_legacy_cards_derived(indexer, "book_arch")
    assert result["total_cards"] == 2
    assert result["updated_cards"] == 2
    assert result["skipped_cold"] == 1

    # archived 卡的 status 不被覆盖，但 evidence_level 仍标记为 derived
    cursor = await indexer.conn.execute("SELECT status, evidence_level FROM cards WHERE card_id = 'arch-1'")
    row = await cursor.fetchone()
    assert row["status"] == "archived"
    assert row["evidence_level"] == "derived"
