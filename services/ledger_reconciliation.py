"""Legacy-card migration preflight and reconciliation helpers."""
from __future__ import annotations

from typing import Any

from services.indexer import CardIndexer


async def reconcile_legacy_cards(indexer: CardIndexer) -> dict[str, Any]:
    cursor = await indexer.conn.execute(
        "SELECT card_id, source_document_id, source_book, source_anchor, evidence_level, status FROM cards"
    )
    rows = [indexer._row_to_dict(row) for row in await cursor.fetchall()]
    missing_source: list[str] = []
    missing_anchor: list[str] = []
    missing_claim: list[str] = []
    derived_drafts: list[str] = []
    for row in rows:
        card_id = str(row["card_id"])
        # derived/draft 卡是无锚点的派生草稿（非权威 claim），符合来源契约，不阻塞权威就绪。
        # archived 卡语义保持不变：status 非 draft 且 evidence_level 非 derived 时仍按权威卡统计。
        is_derived = str(row.get("evidence_level") or "") == "derived" or str(row.get("status") or "") in ("draft",)
        document_id = str(row.get("source_document_id") or row.get("source_book_id") or "")
        if not document_id:
            missing_source.append(card_id)
            continue
        if not row.get("source_anchor"):
            if is_derived:
                derived_drafts.append(card_id)
            else:
                missing_anchor.append(card_id)
        claim = await indexer.conn.execute("SELECT 1 FROM ledger_claims WHERE claim_id=?", (card_id,))
        if await claim.fetchone() is None:
            missing_claim.append(card_id)
    cursor = await indexer.conn.execute("SELECT COUNT(*) FROM ledger_tombstones")
    tombstones = int((await cursor.fetchone())[0])
    return {
        "cards": len(rows),
        "missing_source": missing_source,
        "missing_anchor": missing_anchor,
        "missing_claim": missing_claim,
        "tombstones": tombstones,
        "derived_drafts": derived_drafts,
        "derived_draft_count": len(derived_drafts),
        "ready_for_authoritative": not (missing_source or missing_anchor or missing_claim),
    }
