"""Ledger-first write repository and projection outbox helpers."""
from __future__ import annotations

import asyncio
import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Iterable

from models.ledger import DocumentRecord, PassageRecord


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class LedgerRepository:
    """Writes source records first; compatibility projections consume the outbox."""

    def __init__(self, indexer: Any) -> None:
        self.indexer = indexer
        self._lock = asyncio.Lock()

    async def _transaction(self, operation):
        async with self._lock:
            try:
                result = await operation()
                await self.indexer.conn.commit()
                return result
            except Exception:
                await self.indexer.conn.rollback()
                raise

    async def upsert_document(self, document: DocumentRecord) -> None:
        await self.indexer.conn.execute(
            """INSERT INTO ledger_documents
               (document_id, title, source_uri, media_type, content_hash, parser_id, parser_version,
                license_status, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET
                 title=excluded.title, source_uri=excluded.source_uri, media_type=excluded.media_type,
                 content_hash=excluded.content_hash, parser_id=excluded.parser_id,
                 parser_version=excluded.parser_version, license_status=excluded.license_status,
                 status=excluded.status, updated_at=excluded.updated_at""",
            (
                document.document_id, document.title, document.source_uri, document.media_type,
                document.content_hash, document.parser_id, document.parser_version,
                document.license_status, document.status, document.created_at, document.updated_at,
            ),
        )

    async def upsert_passages(self, passages: Iterable[PassageRecord]) -> int:
        count = 0
        for passage in passages:
            await self.indexer.conn.execute(
                """INSERT INTO ledger_passages
                   (passage_id, document_id, sequence, heading, quote, text_hash,
                    char_start, char_end, page_start, page_end, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(passage_id) DO UPDATE SET
                     sequence=excluded.sequence, heading=excluded.heading, quote=excluded.quote,
                     text_hash=excluded.text_hash, char_start=excluded.char_start,
                     char_end=excluded.char_end, page_start=excluded.page_start,
                     page_end=excluded.page_end, status=excluded.status""",
                (
                    passage.passage_id, passage.document_id, passage.sequence, passage.heading,
                    passage.quote, passage.text_hash, passage.char_start, passage.char_end,
                    passage.page_start, passage.page_end, passage.status, passage.created_at,
                ),
            )
            count += 1
        return count

    async def register_document(
        self,
        *,
        document_id: str,
        title: str,
        source_uri: str,
        media_type: str,
        content: str,
        parser_id: str,
        parser_version: str,
        passages: Iterable[dict[str, Any]],
    ) -> dict[str, Any]:
        content_hash = hashlib.sha256(content.encode("utf-8")).hexdigest()
        now = _now()
        document = DocumentRecord(
            document_id=document_id,
            title=title,
            source_uri=source_uri,
            media_type=media_type,
            content_hash=content_hash,
            parser_id=parser_id,
            parser_version=parser_version,
            created_at=now,
            updated_at=now,
        )

        async def operation() -> dict[str, Any]:
            await self.upsert_document(document)
            records: list[PassageRecord] = []
            for sequence, raw in enumerate(passages):
                quote = str(raw.get("text") or raw.get("quote") or "")
                passage_id = f"passage_{document_id}_{sequence:06d}"
                records.append(PassageRecord(
                    passage_id=passage_id,
                    document_id=document_id,
                    sequence=sequence,
                    heading=str(raw.get("heading") or ""),
                    quote=quote,
                    text_hash=hashlib.sha256(quote.encode("utf-8")).hexdigest(),
                    char_start=raw.get("char_start"),
                    char_end=raw.get("char_end"),
                    page_start=raw.get("page_start"),
                    page_end=raw.get("page_end"),
                    created_at=now,
                ))
            passage_count = await self.upsert_passages(records)
            await self.enqueue(
                artifact_id=document_id,
                operation="DOCUMENT_UPSERTED",
                payload={"document_id": document_id, "content_hash": content_hash, "passage_count": passage_count},
            )
            return {"document_id": document_id, "content_hash": content_hash, "passage_count": passage_count}

        return await self._transaction(operation)

    async def enqueue(self, artifact_id: str, operation: str, payload: dict[str, Any]) -> str:
        fingerprint = hashlib.sha256(
            json.dumps({"artifact_id": artifact_id, "operation": operation, "payload": payload}, sort_keys=True, ensure_ascii=False).encode("utf-8")
        ).hexdigest()[:20]
        event_id = f"outbox_{fingerprint}"
        await self.indexer.conn.execute(
            """INSERT INTO ledger_outbox(event_id, artifact_id, operation, payload, status, created_at)
               VALUES (?, ?, ?, ?, 'PENDING', ?)
               ON CONFLICT(event_id) DO NOTHING""",
            (event_id, artifact_id, operation, json.dumps(payload, ensure_ascii=False), _now()),
        )
        return event_id

    async def archive_document(self, document_id: str, reason: str = "") -> bool:
        async def operation() -> bool:
            cursor = await self.indexer.conn.execute(
                "UPDATE ledger_documents SET status='archived', updated_at=? WHERE document_id=? AND status != 'archived'",
                (_now(), document_id),
            )
            await self.indexer.conn.execute(
                "UPDATE ledger_passages SET status='archived' WHERE document_id=?",
                (document_id,),
            )
            await self.indexer.conn.execute(
                "UPDATE ledger_evidence SET status='archived' WHERE document_id=?",
                (document_id,),
            )
            await self.indexer.conn.execute(
                "UPDATE ledger_claims SET status='archived', updated_at=? WHERE document_id=?",
                (_now(), document_id),
            )
            await self.indexer.conn.execute(
                "UPDATE ledger_metrics SET status='archived' WHERE document_id=?",
                (document_id,),
            )
            await self.indexer.conn.execute(
                "INSERT OR REPLACE INTO ledger_tombstones(artifact_id, artifact_type, reason, created_at) VALUES (?, 'document', ?, ?)",
                (document_id, reason, _now()),
            )
            await self.enqueue(document_id, "DOCUMENT_ARCHIVED", {"document_id": document_id, "reason": reason})
            return cursor.rowcount == 1
        return await self._transaction(operation)

    async def restore_document(self, document_id: str) -> bool:
        async def operation() -> bool:
            cursor = await self.indexer.conn.execute(
                "UPDATE ledger_documents SET status='active', updated_at=? WHERE document_id=? AND status='archived'",
                (_now(), document_id),
            )
            await self.indexer.conn.execute("DELETE FROM ledger_tombstones WHERE artifact_id=?", (document_id,))
            if cursor.rowcount:
                await self.enqueue(document_id, "DOCUMENT_RESTORED", {"document_id": document_id})
            return cursor.rowcount == 1
        return await self._transaction(operation)

    async def read_document(self, document_id: str) -> dict[str, Any] | None:
        async with self._lock:
            cursor = await self.indexer.conn.execute(
                "SELECT * FROM ledger_documents WHERE document_id=?", (document_id,)
            )
            row = await cursor.fetchone()
            return dict(row) if row else None

    async def register_derived_artifact(
        self,
        artifact_id: str,
        source_scope: str,
        artifact_type: str,
        payload: dict[str, Any],
        source_document_id: str = "",
        source_run_id: str = "",
    ) -> dict[str, Any]:
        serialized = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        now = _now()
        async def operation() -> dict[str, Any]:
            await self.indexer.conn.execute(
                """INSERT INTO ledger_derived_artifacts
                   (artifact_id, source_scope, artifact_type, payload, status, source_document_id, source_run_id, content_hash, created_at, updated_at)
                   VALUES (?, ?, ?, ?, 'draft', ?, ?, ?, ?, ?)
                   ON CONFLICT(artifact_id) DO UPDATE SET payload=excluded.payload, content_hash=excluded.content_hash,
                     source_document_id=excluded.source_document_id, source_run_id=excluded.source_run_id, updated_at=excluded.updated_at""",
                (artifact_id, source_scope, artifact_type, serialized, source_document_id, source_run_id, content_hash, now, now),
            )
            await self.enqueue(artifact_id, "DERIVED_ARTIFACT_UPSERTED", {"artifact_id": artifact_id, "content_hash": content_hash})
            return {"artifact_id": artifact_id, "content_hash": content_hash, "status": "draft"}
        return await self._transaction(operation)

    async def read_derived_artifact(self, artifact_id: str) -> dict[str, Any] | None:
        async with self._lock:
            cursor = await self.indexer.conn.execute("SELECT * FROM ledger_derived_artifacts WHERE artifact_id=?", (artifact_id,))
            row = await cursor.fetchone()
            if row is None:
                return None
            result = dict(row)
            result["payload"] = json.loads(result.get("payload") or "{}")
            return result

    async def archive_derived_artifact(self, artifact_id: str, reason: str = "") -> bool:
        async def operation() -> bool:
            cursor = await self.indexer.conn.execute("UPDATE ledger_derived_artifacts SET status='archived', updated_at=? WHERE artifact_id=? AND status != 'archived'", (_now(), artifact_id))
            await self.indexer.conn.execute("INSERT OR REPLACE INTO ledger_tombstones(artifact_id, artifact_type, reason, created_at) VALUES (?, 'derived', ?, ?)", (artifact_id, reason, _now()))
            if cursor.rowcount:
                await self.enqueue(artifact_id, "DERIVED_ARTIFACT_ARCHIVED", {"artifact_id": artifact_id})
            return cursor.rowcount == 1
        return await self._transaction(operation)

    async def reconcile_document(self, document_id: str) -> dict[str, int]:
        async with self._lock:
            cursor = await self.indexer.conn.execute(
                "SELECT COUNT(*) FROM ledger_passages WHERE document_id=? AND status='active'", (document_id,)
            )
            passages = int((await cursor.fetchone())[0])
            cursor = await self.indexer.conn.execute(
                "SELECT COUNT(*) FROM ledger_claims WHERE document_id=? AND status != 'archived'", (document_id,)
            )
            claims = int((await cursor.fetchone())[0])
            return {"passages": passages, "claims": claims}
