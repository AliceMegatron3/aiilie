"""Shadow comparison between legacy card projections and Ledger reads."""
from __future__ import annotations

import hashlib
import json
import time
from collections import deque
from typing import Any

from core.config_manager import config_manager


class LedgerDualRead:
    def __init__(self, indexer: Any, facade: Any, max_records: int = 500) -> None:
        self.indexer = indexer
        self.facade = facade
        self.mismatches: deque[dict[str, Any]] = deque(maxlen=max_records)

    @property
    def mode(self) -> str:
        return str(config_manager.get("ledger.read_mode", "legacy") or "legacy")

    async def compare_search(self, *, keyword: str | None = None, source_book: str | None = None, limit: int = 50, offset: int = 0) -> dict[str, Any]:
        started = time.perf_counter()
        legacy = await self.indexer.search_cards(keyword=keyword, source_book=source_book, limit=limit, offset=offset)
        ledger = await self.facade.search_cards(keyword=keyword, source_book=source_book, limit=limit, offset=offset)
        legacy_ids = [str(item.get("card_id")) for item in legacy]
        ledger_ids = [str(item.get("card_id")) for item in ledger]
        mismatch = {
            "query_hash": hashlib.sha256(json.dumps({"keyword": keyword, "source_book": source_book, "limit": limit, "offset": offset}, sort_keys=True).encode()).hexdigest(),
            "missing_in_ledger": sorted(set(legacy_ids) - set(ledger_ids)),
            "missing_in_legacy": sorted(set(ledger_ids) - set(legacy_ids)),
            "order_mismatch": legacy_ids != ledger_ids,
            "latency_ms": round((time.perf_counter() - started) * 1000, 3),
        }
        mismatch["equal"] = not mismatch["missing_in_ledger"] and not mismatch["missing_in_legacy"]
        if not mismatch["equal"]:
            self.mismatches.append(mismatch)
        return {"legacy": legacy, "ledger": ledger, "comparison": mismatch}

    def report(self) -> dict[str, Any]:
        return {"mode": self.mode, "mismatch_count": len(self.mismatches), "mismatches": list(self.mismatches), "ready": len(self.mismatches) == 0}
