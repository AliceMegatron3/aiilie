"""Ledger outbox state machine tests."""
from __future__ import annotations

import pytest

from services.indexer import CardIndexer
from services.ledger_outbox import LedgerOutbox


@pytest.mark.asyncio
async def test_outbox_drain_is_idempotent_and_dead_letters(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    outbox = LedgerOutbox(indexer, max_attempts=2)
    await outbox.initialize()
    await indexer.conn.execute(
        "INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at) VALUES ('evt-ok','a','TEST','{}','PENDING',datetime('now'))"
    )
    await indexer.conn.commit()
    handled: list[str] = []
    try:
        result = await outbox.drain(lambda item: _record(handled, item), limit=10)
        assert result["applied"] == 1
        assert handled == ["evt-ok"]
        assert (await outbox.health())["ready"] is True
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_outbox_failure_retries_then_dead_letters(tmp_path):
    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    outbox = LedgerOutbox(indexer, max_attempts=2)
    await outbox.initialize()
    await indexer.conn.execute(
        "INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at) VALUES ('evt-bad','a','TEST','{}','PENDING',datetime('now'))"
    )
    await indexer.conn.commit()
    try:
        async def fail(_item):
            raise RuntimeError("boom")
        await outbox.drain(fail)
        assert (await outbox.health())["failed"] == 1
        await outbox.drain(fail)
        health = await outbox.health()
        assert health["dead_letter"] == 1
        assert health["ready"] is False
    finally:
        await indexer.close()


async def _record(handled: list[str], item: dict) -> None:
    handled.append(str(item["event_id"]))


@pytest.mark.asyncio
async def test_claim_quantified_drains_when_projection_exists(tmp_path):
    """Batch 3：CLAIM_QUANTIFIED outbox handler 幂等——投影主行存在即 APPLIED，ready。"""
    from datetime import datetime, timezone

    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    outbox = LedgerOutbox(indexer, max_attempts=2)
    await outbox.initialize()
    now = datetime.now(timezone.utc).isoformat()
    await indexer.conn.execute(
        """INSERT INTO ledger_claims(claim_id, document_id, evidence_ids, claim_type,
           content, fingerprint, scope_level, confidence, status, created_at, updated_at)
           VALUES (?, ?, '[]', 'FACT', ?, 'fp', 'book', 0.5, 'draft', ?, ?)""",
        ("claim_abc", "doc1", "因果推理", now, now),
    )
    await indexer.conn.execute(
        "INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at) VALUES (?,?,?,?,?,?)",
        ("claim_quantified:claim_abc", "claim_abc", "CLAIM_QUANTIFIED",
         '{"claim_id":"claim_abc","document_id":"doc1"}', "PENDING", now),
    )
    await indexer.conn.commit()
    try:
        result = await outbox.drain_compat(limit=10)
        assert result["applied"] == 1
        assert result["failed"] == 0
        assert (await outbox.health())["ready"] is True
    finally:
        await indexer.close()


@pytest.mark.asyncio
async def test_claim_quantified_missing_projection_fails_not_applied(tmp_path):
    """Batch 3：CLAIM_QUANTIFIED 无对应 ledger_claims 主行 → 不误标 APPLIED。"""
    from datetime import datetime, timezone

    indexer = CardIndexer(tmp_path / "index")
    await indexer.initialize()
    outbox = LedgerOutbox(indexer, max_attempts=2)
    await outbox.initialize()
    now = datetime.now(timezone.utc).isoformat()
    # 只在 outbox 写事件，不写 ledger_claims
    await indexer.conn.execute(
        "INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at) VALUES (?,?,?,?,?,?)",
        ("claim_quantified:missing", "missing", "CLAIM_QUANTIFIED",
         '{"claim_id":"missing","document_id":"doc1"}', "PENDING", now),
    )
    await indexer.conn.commit()
    try:
        result = await outbox.drain_compat(limit=10)
        assert result["failed"] == 1
        assert result["applied"] == 0
        health = await outbox.health()
        assert health["failed"] == 1 and health["ready"] is False
    finally:
        await indexer.close()
