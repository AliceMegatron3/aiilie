"""Fail-closed authoritative startup behavior."""
from __future__ import annotations

import pytest

from services.ledger_readiness import LedgerReadiness


@pytest.mark.asyncio
async def test_authoritative_readiness_requires_real_migration(tmp_path):
    class EmptyConnection:
        async def execute(self, sql, params=()):
            class Cursor:
                async def fetchone(self):
                    return None
                async def fetchall(self):
                    return []
            return Cursor()

    class EmptyIndexer:
        conn = EmptyConnection()

    report = await LedgerReadiness(EmptyIndexer()).report()
    assert report["ready_for_authoritative"] is False
    assert "migration_not_completed" in report["blockers"]
