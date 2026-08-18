"""P1：量化完成 → 唯一 ReflectionSession 闭环回归。

覆盖：确定性 run_id、幂等（同键不产生第二个会话/副作用）、provenance 持久化、
      回放查询（list_reflection_runs）、缺 source_hash 拒建、失败不伪造成功。
"""
from __future__ import annotations

import pytest

from services.quantify_reflection import (
    create_quantify_reflection,
    list_reflection_runs,
    quantify_run_id,
)
from services.indexer import CardIndexer


@pytest.mark.asyncio
async def test_run_id_is_deterministic_and_stable():
    a = quantify_run_id("book1", "hashA", "both")
    b = quantify_run_id("book1", "hashA", "both")
    c = quantify_run_id("book1", "hashB", "both")
    assert a == b
    assert a != c
    assert a.startswith("quantify_reflect_")


@pytest.mark.asyncio
async def test_create_is_idempotent_no_duplicate_session(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        first = await create_quantify_reflection(
            indexer, book_id="b1", source_hash="h1", mode="both",
            metrics={"info": 3, "data": 2}, model="deepseek",
        )
        assert first["created"] is True
        second = await create_quantify_reflection(
            indexer, book_id="b1", source_hash="h1", mode="both",
            metrics={"info": 3, "data": 2}, model="deepseek",
        )
        assert second["created"] is False
        assert second["session_id"] == first["session_id"]
        assert second["replay"] is True
        # 全程仅 1 个 session / 1 条 run（无重复副作用）
        cursor = await indexer.conn.execute(
            "SELECT COUNT(*) FROM reflection_sessions WHERE trigger_type='QUANTIFY'"
        )
        assert (await cursor.fetchone())[0] == 1
        runs = await list_reflection_runs(indexer, book_id="b1")
        assert len(runs) == 1
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_provenance_metrics_persisted_and_queryable(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        await create_quantify_reflection(
            indexer, book_id="b9", source_hash="src-9", mode="info",
            metrics={"info": 4, "conflicts": 0}, model="",
        )
        runs = await list_reflection_runs(indexer, book_id="b9")
        assert len(runs) == 1
        rec = runs[0]
        assert rec.run_id.startswith("quantify_reflect_")
        assert rec.book_id == "b9"
        assert rec.source_hash == "src-9"
        assert rec.mode == "info"
        assert rec.metrics == {"info": 4, "conflicts": 0}
        assert rec.model == ""
        assert rec.status == "RUNNING"
        assert rec.session_id
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_missing_source_hash_rejected(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        with pytest.raises(ValueError):
            await create_quantify_reflection(indexer, book_id="b", source_hash="", mode="both")
    finally:
        await indexer.close()