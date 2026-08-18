"""Ledger 与插件能力目录 API 回归测试。"""
from __future__ import annotations

import httpx
import pytest

from api.deps import get_indexer
from core.config_manager import config_manager
from core.path_resolver import get_app_data_dir
from main import create_app
from models.cards import InfoCard
from models.ledger import LawRecord
from services.indexer import CardIndexer


@pytest.mark.asyncio
async def test_ledger_api_lists_evidence_and_rejects_invalid_law(tmp_path, monkeypatch):
    # 隔离全局 ledger 灰度：legacy 语义（authoritative=false）下写入无锚点卡。
    ledger_cfg = config_manager._config.setdefault("ledger", {})
    monkeypatch.setitem(ledger_cfg, "authoritative", False)
    monkeypatch.setitem(ledger_cfg, "read_mode", "legacy")
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    await indexer.save_card(InfoCard(
        source_book_id="ledger-api-book",
        source_document_id="ledger-api-book",
        content="证据内容",
        card_sub_type="note",
        original_fragment="原文",
    ))
    app = create_app()
    app.dependency_overrides[get_indexer] = lambda: indexer
    try:
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                evidence = await client.get("/api/v1/ledger/evidence", params={"document_id": "ledger-api-book"})
                assert evidence.status_code == 200
                assert len(evidence.json()["data"]) == 1
                invalid = LawRecord(
                    law_id="bad",
                    layer="REALITY_BASE",
                    name="没有证据的现实规则",
                    status="active",
                )
                response = await client.post("/api/v1/ledger/laws", json=invalid.model_dump())
                assert response.status_code == 422
    finally:
        app.dependency_overrides.clear()
        await indexer.close()
