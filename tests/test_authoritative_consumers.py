"""Authoritative consumer guard for derived quantization artifacts."""
from __future__ import annotations

import pytest

from core.config_manager import config_manager
from services.indexer import CardIndexer
from services.ledger_repository import LedgerRepository


@pytest.mark.asyncio
async def test_derived_artifact_is_draft_and_does_not_require_source_anchor(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    try:
        repository = LedgerRepository(indexer)
        result = await repository.register_derived_artifact(
            artifact_id="derived-book-metric",
            source_scope="book-1",
            artifact_type="quantize_convergence_metric",
            payload={"metric_type": "emotion_curve", "value": {"chapter": 1}},
            source_document_id="book-1",
            source_run_id="run-1",
        )
        artifact = await repository.read_derived_artifact(result["artifact_id"])
        assert artifact["status"] == "draft"
        assert artifact["payload"]["metric_type"] == "emotion_curve"
    finally:
        await indexer.close()
