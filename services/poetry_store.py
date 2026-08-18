"""诗词作品/版本持久化 + Ledger provenance 对齐。

- 表 `poetry_works` / `poetry_editions`：幂等建表，复用 core.database 的 conn/execute_write 风格。
- 版本校勘：同一作品可存多版本（version 递增），get_work 返回全部版本。
- provenance：把来源（source_document_id / passage_id / extraction_run_id）写入
  editions.provenance（JSON），并尽力对齐 services/indexer.py 的 ledger 体系
  （可选经 LedgerRepository.register_derived_artifact 注册，或直写 ledger_derived_artifacts）。
- analyze_work：聚合现有 PoetryAnalyzer（句/字/标点/偏移）+ 音韵（韵部/平仄/格律/典故）。
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from typing import Any
from uuid import uuid4

from models.poetry import PoetryWork
from services.poetry_analyzer import analyze_work as analyzer_analyze_work
from services.poetry_phonology import (
    evaluate_prosody,
    find_allusions,
    find_rhyme_candidates,
    validate_meter,
)

_PUNCTUATION = "，。！？；：、,.!?;:"

_DDL = """
CREATE TABLE IF NOT EXISTS poetry_works (
    work_id     TEXT PRIMARY KEY,
    title       TEXT NOT NULL,
    author      TEXT NOT NULL DEFAULT '',
    dynasty     TEXT NOT NULL DEFAULT '',
    style       TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL,
    updated_at  TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS poetry_editions (
    edition_id      TEXT PRIMARY KEY,
    work_id         TEXT NOT NULL,
    version         INTEGER NOT NULL DEFAULT 1,
    text            TEXT NOT NULL,
    normalized_text TEXT NOT NULL DEFAULT '',
    metrics         TEXT NOT NULL DEFAULT '{}',
    provenance      TEXT NOT NULL DEFAULT '{}',
    created_at      TEXT NOT NULL,
    FOREIGN KEY (work_id) REFERENCES poetry_works(work_id)
);
CREATE INDEX IF NOT EXISTS idx_poetry_editions_work
    ON poetry_editions(work_id, version);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _id(prefix: str) -> str:
    return f"{prefix}_{uuid4().hex[:16]}"


def _clean_line(raw: str) -> str:
    return "".join(ch for ch in raw if ch not in _PUNCTUATION and not ch.isspace())


def _split_lines(text: str) -> list[str]:
    return [_clean_line(line) for line in re.split(r"\n+", text) if _clean_line(line)]


def _infer_style(lines: list[str]) -> str | None:
    from services.poetry_phonology import _METER_PROFILES
    lengths = [len(line) for line in lines]
    for key, profile in _METER_PROFILES.items():
        if len(lines) == profile["line_count"] and lengths == profile["line_lengths"]:
            return key
    return None


class PoetryStore:
    """SQLite 诗词作品/版本仓库（临时库/真实库皆可，build 时幂等建表）。"""

    def __init__(self, db: Any, ledger_repository: Any = None) -> None:
        self.db = db
        self.ledger_repository = ledger_repository

    async def initialize(self) -> None:
        await self.db.conn.executescript(_DDL)
        await self.db.conn.commit()

    # ── 写入 ────────────────────────────────────────────────
    async def save_work(
        self,
        *,
        title: str,
        author: str = "",
        dynasty: str = "",
        style: str = "",
        text: str,
        work_id: str | None = None,
        source_document_id: str = "",
        passage_id: str = "",
        extraction_run_id: str = "",
    ) -> dict[str, Any]:
        await self.initialize()
        now = _now()
        if work_id is None:
            work_id = _id("work")
            await self.db.execute_write(
                """INSERT INTO poetry_works (work_id, title, author, dynasty, style, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (work_id, title, author, dynasty, style, now, now),
            )
        else:
            await self.db.execute_write(
                """UPDATE poetry_works SET title=?, author=?, dynasty=?, style=?, updated_at=?
                   WHERE work_id=?""",
                (title, author, dynasty, style, now, work_id),
            )
        edition = await self.save_edition(work_id=work_id, text=text)
        if source_document_id or passage_id or extraction_run_id:
            await self.add_ledger_provenance(
                edition["edition_id"],
                source_document_id=source_document_id,
                passage_id=passage_id,
                extraction_run_id=extraction_run_id,
            )
        return {
            "work": {"work_id": work_id, "title": title, "author": author, "dynasty": dynasty, "style": style},
            "edition": edition,
        }

    async def save_edition(
        self, *, work_id: str, text: str, metrics: dict[str, Any] | None = None, provenance: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        await self.initialize()
        edition_id = _id("edition")
        version = await self._next_version(work_id)
        normalized = "\n".join(_split_lines(text))
        if metrics is None:
            metrics = self._basic_metrics(text)
        await self.db.execute_write(
            """INSERT INTO poetry_editions
               (edition_id, work_id, version, text, normalized_text, metrics, provenance, created_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                edition_id, work_id, version, text, normalized,
                json.dumps(metrics, ensure_ascii=False),
                json.dumps(provenance or {}, ensure_ascii=False),
                _now(),
            ),
        )
        return {
            "edition_id": edition_id, "work_id": work_id, "version": version,
            "text": text, "normalized_text": normalized, "metrics": metrics,
        }

    async def add_ledger_provenance(
        self,
        edition_id: str,
        source_document_id: str = "",
        passage_id: str = "",
        extraction_run_id: str = "",
    ) -> dict[str, Any]:
        """把来源写入 editions.provenance（JSON），并对齐 ledger_derived_artifacts。"""
        await self.initialize()
        row = await self._fetch_edition(edition_id)
        if row is None:
            return {"ok": False, "error": "edition not found", "edition_id": edition_id}
        provenance = json.loads(row["provenance"] or "{}")
        records = provenance.setdefault("sources", [])
        record = {
            "source_document_id": source_document_id,
            "passage_id": passage_id,
            "extraction_run_id": extraction_run_id,
            "added_at": _now(),
        }
        records.append(record)
        await self.db.execute_write(
            "UPDATE poetry_editions SET provenance=? WHERE edition_id=?",
            (json.dumps(provenance, ensure_ascii=False), edition_id),
        )
        registered = await self._register_ledger_derived(edition_id, record)
        return {"ok": True, "edition_id": edition_id, "provenance": provenance, "ledger_registered": registered}

    async def _register_ledger_derived(self, edition_id: str, record: dict[str, Any]) -> bool:
        """可选：经 LedgerRepository 或直写 ledger_derived_artifacts 注册派生产物。"""
        try:
            if self.ledger_repository is not None:
                await self.ledger_repository.register_derived_artifact(
                    artifact_id=f"poetry_edition_{edition_id}",
                    source_scope="poetry",
                    artifact_type="poetry_edition",
                    payload=record,
                    source_document_id=record.get("source_document_id", ""),
                    source_run_id=record.get("extraction_run_id", ""),
                )
                return True
            cursor = await self.db.conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='ledger_derived_artifacts'"
            )
            if await cursor.fetchone() is None:
                return False
            serialized = json.dumps(record, ensure_ascii=False, sort_keys=True, default=str)
            content_hash = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
            now = _now()
            await self.db.execute_write(
                """INSERT INTO ledger_derived_artifacts
                   (artifact_id, source_scope, artifact_type, payload, status,
                    source_document_id, source_run_id, content_hash, created_at, updated_at)
                   VALUES (?, 'poetry', 'poetry_edition', ?, 'draft', ?, ?, ?, ?, ?)""",
                (
                    f"poetry_edition_{edition_id}", serialized,
                    record.get("source_document_id", ""), record.get("extraction_run_id", ""),
                    content_hash, now, now,
                ),
            )
            return True
        except Exception:
            return False

    # ── 读取 ────────────────────────────────────────────────
    async def get_work(self, work_id: str) -> dict[str, Any] | None:
        await self.initialize()
        cursor = await self.db.conn.execute("SELECT * FROM poetry_works WHERE work_id=?", (work_id,))
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        work = dict(zip(columns, row))
        work["editions"] = await self._editions_of(work_id)
        return work

    async def list_works(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        await self.initialize()
        cursor = await self.db.conn.execute(
            "SELECT * FROM poetry_works ORDER BY updated_at DESC LIMIT ? OFFSET ?", (limit, offset)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        works: list[dict[str, Any]] = []
        for row in rows:
            work = dict(zip(columns, row))
            editions = await self._editions_of(work["work_id"])
            if editions:
                work["latest_text"] = editions[-1]["text"]
                work["latest_version"] = editions[-1]["version"]
            works.append(work)
        return works

    async def _editions_of(self, work_id: str) -> list[dict[str, Any]]:
        cursor = await self.db.conn.execute(
            "SELECT * FROM poetry_editions WHERE work_id=? ORDER BY version ASC", (work_id,)
        )
        rows = await cursor.fetchall()
        columns = [desc[0] for desc in cursor.description]
        editions = []
        for row in rows:
            item = dict(zip(columns, row))
            for key in ("metrics", "provenance"):
                try:
                    item[key] = json.loads(item.get(key) or "{}")
                except json.JSONDecodeError:
                    item[key] = {}
            editions.append(item)
        return editions

    async def _fetch_edition(self, edition_id: str) -> dict[str, Any] | None:
        cursor = await self.db.conn.execute(
            "SELECT * FROM poetry_editions WHERE edition_id=?", (edition_id,)
        )
        row = await cursor.fetchone()
        if row is None:
            return None
        columns = [desc[0] for desc in cursor.description]
        return dict(zip(columns, row))

    async def _next_version(self, work_id: str) -> int:
        cursor = await self.db.conn.execute(
            "SELECT COALESCE(MAX(version), 0) + 1 FROM poetry_editions WHERE work_id=?", (work_id,)
        )
        row = await cursor.fetchone()
        return int(row[0]) if row else 1

    # ── 基础指标（复用 analyzer 风格） ─────────────────────
    @staticmethod
    def _basic_metrics(text: str) -> dict[str, Any]:
        lines = _split_lines(text)
        lengths = [len(line) for line in lines]
        return {
            "line_count": len(lines),
            "line_lengths": lengths,
            "uniform_line_length": len(set(lengths)) <= 1 if lengths else True,
            "character_count": sum(lengths),
            "punctuation_preserved": True,
        }

    # ── 增强分析（句/字/标点/偏移 + 韵部/平仄/格律/典故） ──
    def analyze_work(self, text: str, style: str | None = None) -> dict[str, Any]:
        """调用现有 PoetryAnalyzer 做句/字/标点/偏移，并叠加音韵学分析。"""
        work = PoetryWork(title="analysis", edition_ids=["analysis-edition"])
        lines, evaluation = analyzer_analyze_work(work, text)
        clean = [line.normalized_text for line in lines]
        resolved_style = style or _infer_style(clean) or "五绝"
        meter = validate_meter(text, resolved_style)
        prosody = evaluate_prosody(clean, resolved_style)
        tail = clean[-1][-1] if clean and clean[-1] else ""
        return {
            "style": resolved_style,
            "lines": [line.model_dump(mode="json") for line in lines],
            "metrics": evaluation.metrics,
            "input_hash": evaluation.input_hash,
            "meter": meter,
            "prosody": prosody,
            "rhyme_candidates": find_rhyme_candidates(tail) if tail else [],
            "allusions": find_allusions(text),
        }
