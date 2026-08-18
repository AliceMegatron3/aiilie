# Readiness gate for Ledger authoritative mode."""
from __future__ import annotations

from typing import Any

from core.config_manager import config_manager


async def _current_cards_high_water(indexer: Any) -> int:
    """当前 cards 表的 MAX(rowid)，用于判断迁移是否落后于现数据。"""
    try:
        cursor = await indexer.conn.execute("SELECT COALESCE(MAX(rowid), 0) FROM cards")
        row = await cursor.fetchone()
        return int((row or (0,))[0])
    except Exception:
        return 0


class LedgerReadiness:
    def __init__(self, indexer: Any, dual_read: Any | None = None) -> None:
        self.indexer = indexer
        self.dual_read = dual_read

    async def report(self) -> dict[str, Any]:
        blockers: list[str] = []
        latest_run: dict[str, Any] | None = None
        try:
            cursor = await self.indexer.conn.execute("SELECT * FROM ledger_migration_runs WHERE dry_run=0 ORDER BY started_at DESC LIMIT 1")
            row = await cursor.fetchone()
            latest_run = dict(row) if row else None
        except Exception as exc:
            blockers.append(f"migration_query_failed:{exc}")
        if latest_run is None or latest_run.get("status") != "COMPLETED":
            blockers.append("migration_not_completed")
        elif int(latest_run.get("source_high_water", 0)) < await _current_cards_high_water(self.indexer):
            # Upgrade Gate：最新迁移虽 COMPLETED，但落后于当前卡片水位（迁移后又新增），视为过期。
            blockers.append("migration_is_stale")
        try:
            cursor = await self.indexer.conn.execute("SELECT status, COUNT(*) FROM ledger_outbox GROUP BY status")
            statuses = {str(row[0]): int(row[1]) for row in await cursor.fetchall()}
            if any(statuses.get(status, 0) for status in ("PENDING", "RUNNING", "FAILED", "DEAD_LETTER")):
                blockers.append("outbox_not_drained")
        except Exception as exc:
            blockers.append(f"outbox_query_failed:{exc}")
        authoritative = config_manager.get_bool("ledger.authoritative", False)
        legacy_projection = config_manager.get_bool("ledger.allow_legacy_card_projection", True)
        # 权威 + 关闭 legacy 投影后，cards 表冻结为只读投影，数量不再与 claims 同步，
        # 计数一致检查不再适用（跳过），避免新增卡后误报 blocker 阻断启动。
        card_claim_count_check = "skipped" if (authoritative and not legacy_projection) else "passed"
        try:
            cursor = await self.indexer.conn.execute("SELECT COUNT(*) FROM cards")
            card_count = int((await cursor.fetchone())[0])
            cursor = await self.indexer.conn.execute("SELECT COUNT(*) FROM ledger_claims")
            claim_count = int((await cursor.fetchone())[0])
            if card_claim_count_check != "skipped" and card_count != claim_count:
                card_claim_count_check = "blocked"
                blockers.append(f"card_claim_count_mismatch:{card_count}!={claim_count}")
        except Exception as exc:
            card_claim_count_check = "blocked"
            blockers.append(f"card_claim_query_failed:{exc}")

        # 双读门禁语义（阶段C 收敛）：
        # - authoritative 且关闭 legacy 投影时，cards 表已冻结，legacy-vs-Ledger 双读对比无意义，
        #   不应作为阻塞（这是迁移完成后的稳态，而非过渡期观察）。
        # - 未装配 dual_read 门禁实例时，按「不适用」处理，不制造伪阻塞。
        # - 仅当仍处于 legacy 投影开启的过渡期、且显式开启 dual_read_enabled 时，才要求双读 ready。
        if self.dual_read is None:
            dual_status = "not_applicable"
        else:
            dual_status = "passed" if self.dual_read.report().get("ready", False) else "blocked"
        if (
            not (authoritative and not legacy_projection)
            and config_manager.get_bool("ledger.dual_read_enabled", False)
            and dual_status == "blocked"
        ):
            blockers.append("dual_read_mismatch")
        return {
            "ready_for_authoritative": not blockers,
            "authoritative_configured": config_manager.get_bool("ledger.authoritative", False),
            "blockers": blockers,
            "card_claim_count_check": card_claim_count_check,
            "latest_migration": latest_run,
            "dual_read": {"status": dual_status, "ready": dual_status == "passed"},
        }

    async def assert_ready(self) -> dict[str, Any]:
        report = await self.report()
        if not report["ready_for_authoritative"]:
            raise RuntimeError("Ledger authoritative 门禁未通过: " + "; ".join(report["blockers"]))
        return report
