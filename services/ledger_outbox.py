"""Ledger outbox state machine and idempotent drain service."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable


OUTBOX_PENDING = "PENDING"
OUTBOX_RUNNING = "RUNNING"
OUTBOX_APPLIED = "APPLIED"
OUTBOX_FAILED = "FAILED"
OUTBOX_DEAD_LETTER = "DEAD_LETTER"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerOutbox:
    def __init__(self, indexer: Any, max_attempts: int = 3) -> None:
        self.indexer = indexer
        self.max_attempts = max_attempts

    async def initialize(self) -> None:
        cursor = await self.indexer.conn.execute("PRAGMA table_info(ledger_outbox)")
        columns = {str(row[1]) for row in await cursor.fetchall()}
        for name, definition in (
            ("attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("last_error", "TEXT NOT NULL DEFAULT ''"),
            ("heartbeat", "TEXT"),
        ):
            if name not in columns:
                await self.indexer.conn.execute(f"ALTER TABLE ledger_outbox ADD COLUMN {name} {definition}")
        await self.indexer.conn.commit()

    async def recover_running(self) -> int:
        cursor = await self.indexer.conn.execute(
            "UPDATE ledger_outbox SET status='PENDING', heartbeat=NULL WHERE status='RUNNING'"
        )
        await self.indexer.conn.commit()
        return int(cursor.rowcount)

    async def pending(self, limit: int = 100) -> list[dict[str, Any]]:
        cursor = await self.indexer.conn.execute(
            "SELECT * FROM ledger_outbox WHERE status IN ('PENDING','FAILED') AND attempts < ? ORDER BY created_at LIMIT ?",
            (self.max_attempts, max(1, min(int(limit), 500))),
        )
        rows = []
        for row in await cursor.fetchall():
            item = dict(row)
            try:
                item["payload"] = json.loads(item.get("payload") or "{}")
            except (TypeError, ValueError):
                item["payload"] = {}
            rows.append(item)
        return rows

    async def drain(self, handler: Callable[[dict[str, Any]], Awaitable[None]], limit: int = 100) -> dict[str, int]:
        stats = {"applied": 0, "failed": 0, "dead_letter": 0}
        for item in await self.pending(limit):
            event_id = str(item["event_id"])
            await self.indexer.conn.execute(
                "UPDATE ledger_outbox SET status='RUNNING', heartbeat=? WHERE event_id=? AND status IN ('PENDING','FAILED')",
                (_now(), event_id),
            )
            await self.indexer.conn.commit()
            try:
                await handler(item)
            except Exception as exc:
                attempts = int(item.get("attempts", 0) or 0) + 1
                status = OUTBOX_DEAD_LETTER if attempts >= self.max_attempts else OUTBOX_FAILED
                await self.indexer.conn.execute(
                    "UPDATE ledger_outbox SET status=?, attempts=?, last_error=?, heartbeat=NULL WHERE event_id=?",
                    (status, attempts, str(exc)[:2000], event_id),
                )
                await self.indexer.conn.commit()
                stats["dead_letter" if status == OUTBOX_DEAD_LETTER else "failed"] += 1
            else:
                await self.indexer.conn.execute(
                    "UPDATE ledger_outbox SET status='APPLIED', attempts=attempts+1, last_error='', applied_at=?, heartbeat=NULL WHERE event_id=?",
                    (_now(), event_id),
                )
                await self.indexer.conn.commit()
                stats["applied"] += 1
        return stats

    async def drain_compat(self, limit: int = 100) -> dict[str, int]:
        """Confirm legacy projections for existing card-first events.

        This bridge is intentionally verification-only. It does not make cards
        authoritative and can later be replaced by the Ledger-first projector.
        """
        async def verify(item: dict[str, Any]) -> None:
            operation = str(item.get("operation") or "")
            artifact_id = str(item.get("artifact_id") or "")
            if operation == "CLAIM_QUANTIFIED":
                # 幂等 handler：坚信投影已原子写库，此处仅校验 `ledger_claims` 主行存在。
                # 存在 → 判定 APPLIED（稳定 event_id 的幂等语义）；缺失 → 拒绝且保持 PENDING/
                # FAILED 供重试，绝不误标成功。
                claim_id = str((item.get("payload") or {}).get("claim_id") or artifact_id)
                cursor = await self.indexer.conn.execute(
                    "SELECT 1 FROM ledger_claims WHERE claim_id=?", (claim_id,)
                )
                if await cursor.fetchone() is None:
                    raise RuntimeError("claim quantified projection missing")
            elif operation == "CARD_PROJECTED":
                # 阶段C：authoritative + 关闭 legacy 投影时，卡片从不写入 legacy cards 表，
                # 兼容桥不应再强制校验卡表缺失（否则必然 DEAD_LETTER）。
                from core.config_manager import config_manager

                ledger_only = bool(config_manager.get_bool("ledger.authoritative", False)) and not bool(
                    config_manager.get_bool("ledger.allow_legacy_card_projection", True)
                )
                if ledger_only:
                    return
                cursor = await self.indexer.conn.execute("SELECT 1 FROM cards WHERE card_id=?", (artifact_id,))
                if await cursor.fetchone() is None:
                    raise RuntimeError("card projection missing")
            elif operation == "RELATION_UPSERTED":
                cursor = await self.indexer.conn.execute("SELECT 1 FROM card_relations WHERE relation_id=?", (artifact_id,))
                if await cursor.fetchone() is None:
                    raise RuntimeError("relation projection missing")
            elif operation == "CLAIM_TOMBSTONED":
                cursor = await self.indexer.conn.execute("SELECT 1 FROM cards WHERE card_id=?", (artifact_id,))
                if await cursor.fetchone() is not None:
                    raise RuntimeError("tombstoned claim still visible in cards")
            elif operation in {"DOCUMENT_UPSERTED", "DOCUMENT_RENAMED", "DOCUMENT_ARCHIVED", "DOCUMENT_RESTORED"}:
                document_id = str((item.get("payload") or {}).get("document_id") or artifact_id)
                cursor = await self.indexer.conn.execute("SELECT 1 FROM ledger_documents WHERE document_id=?", (document_id,))
                if await cursor.fetchone() is None:
                    raise RuntimeError("ledger document missing")
            else:
                raise RuntimeError(f"unsupported outbox operation: {operation}")

        return await self.drain(verify, limit=limit)

    async def health(self) -> dict[str, int | bool]:
        cursor = await self.indexer.conn.execute(
            "SELECT status, COUNT(*) FROM ledger_outbox GROUP BY status"
        )
        counts = {str(row[0]).lower(): int(row[1]) for row in await cursor.fetchall()}
        return {
            "pending": counts.get("pending", 0),
            "running": counts.get("running", 0),
            "failed": counts.get("failed", 0),
            "dead_letter": counts.get("dead_letter", 0),
            "ready": counts.get("pending", 0) == 0 and counts.get("running", 0) == 0 and counts.get("failed", 0) == 0 and counts.get("dead_letter", 0) == 0,
        }
