"""Ledger 来源契约：登记真实来源文档/段落，并将无来源旧卡标记为 derived/draft。

来源诚实性落地：
- 把可恢复的真实来源文档（如 library_manifest.json 映射的 .md 研究文档）登记进
  ``ledger_documents`` / ``ledger_passages``，为后续权威 claim 提供合法锚点；
- 对原文已丢失、无法诚实建立 passage 锚点的旧卡，显式标记
  ``evidence_level='derived'``、``status='draft'``，绝不伪装成 authoritative claim。

约束：
- 全部写操作幂等（UPSERT / 条件更新），可断点续跑，不删除任何现有数据；
- 不伪造任何 source_anchor / passage 指向；
- 每个写批次一个事务并提交。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

_MARKDOWN_HEADING_RE = re.compile(r"^#{1,6}\s*(.+?)\s*$")

# cards 表中用于定位冷 JSON 文件的列名（旧库 file_path / 新库 detail_path）
_COLD_PATH_COLUMNS = ("detail_path", "file_path")

# 允许被覆盖为 draft 的 status 值；archived 等其余状态永不覆盖
_DRAFTABLE_STATUSES = {None, "", "draft", "active"}

# manifest 顶层元信息键（entries 之外的说明性键，不属于文档条目）
_MANIFEST_META_KEYS = {
    "manifest_version", "library_id", "library_name", "root_path",
    "root_source_document_id", "generated_at", "entries",
}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _build_passage(block_text: str, char_start: int, current_heading: str) -> dict[str, Any]:
    """由单个文本块构造 passage 记录（quote 截取前 2000 字）。"""
    block = block_text.strip()
    heading = current_heading
    first = block.splitlines()[0].strip() if block else ""
    match = _MARKDOWN_HEADING_RE.match(first)
    if match:
        heading = match.group(1).strip()
    return {
        "heading": heading,
        "quote": block[:2000],
        "text_hash": _sha256(block),
        "char_start": char_start,
        "char_end": char_start + len(block),
    }


def _split_markdown_passages(text: str) -> list[dict[str, Any]]:
    """按空行/标题把 Markdown 文本切分为段落。

    标题行（``#`` 开头）更新"当前节标题"，并作为其所在段落的 heading；
    段落以空行分隔；返回 char_start/char_end（相对原文的字符区间）。
    """
    passages: list[dict[str, Any]] = []
    current_heading = ""
    block: list[str] = []
    block_start = 0
    offset = 0
    for line in text.splitlines(keepends=True):
        if not line.strip():
            if block:
                passages.append(_build_passage("".join(block), block_start, current_heading))
                block = []
        else:
            if not block:
                block_start = offset
                stripped = line.strip()
                match = _MARKDOWN_HEADING_RE.match(stripped)
                if match:
                    current_heading = match.group(1).strip()
            block.append(line)
        offset += len(line)
    if block:
        passages.append(_build_passage("".join(block), block_start, current_heading))
    return passages


def _manifest_entries(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    """从 manifest 中提取文档条目列表。

    优先使用 ``entries`` 列表；否则兼容 {document_id: {relative_path, title, ...}} 映射。
    """
    entries = manifest.get("entries")
    if isinstance(entries, list):
        return [entry for entry in entries if isinstance(entry, dict)]
    if isinstance(manifest, dict):
        out: list[dict[str, Any]] = []
        for key, value in manifest.items():
            if key in _MANIFEST_META_KEYS or not isinstance(value, dict):
                continue
            out.append({
                "source_document_id": str(key),
                "relative_path": str(value.get("relative_path") or value.get("path") or ""),
                "title": str(value.get("title") or ""),
            })
        return out
    return []


def _resolve_source_file(roots: list[Path], relative_path: str, root_path: str = "") -> Path | None:
    """按 manifest 相对路径在多个根下定位真实文件（含 root_path 前缀两种形式）。"""
    rel = relative_path.replace("\\", "/")
    candidates: list[Path] = []
    for root in roots:
        candidates.append(root / rel)
        if root_path:
            candidates.append(root / root_path / rel)
    for candidate in candidates:
        try:
            if candidate.is_file():
                return candidate
        except OSError:
            continue
    return None


async def register_real_source_documents(
    indexer: Any,
    manifest_path: Path | str,
    roots: list[Path],
    dry_run: bool = False,
) -> dict[str, Any]:
    """把 manifest 中可恢复的真实来源文档登记进 ledger_documents / ledger_passages。

    对每个真实存在的 .md 文件：
    - 计算内容 sha256 作为 content_hash；
    - 以 manifest 的 source_document_id 作为 document_id 幂等 UPSERT 进
      ledger_documents（parser_id='manifest'，status='active'）；
    - 按空行/标题切分为段落，幂等 UPSERT 进 ledger_passages
      （passage_id 形如 ``{document_id}:p{i}``）。

    ``dry_run=True`` 只计算与返回计划统计，不写库。
    每个文件一个事务提交，可断点续跑。
    """
    manifest_path = Path(manifest_path)
    roots = [Path(root) for root in roots]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    root_path = str(manifest.get("root_path") or "")
    entries = _manifest_entries(manifest)

    conn = indexer.conn
    now = _now()
    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "manifest": str(manifest_path),
        "registered_documents": 0,
        "registered_passages": 0,
        "skipped": [],    # 文件缺失的 relative_path
        "errors": [],     # 处理异常
        "documents": [],  # 已登记/计划登记的文档摘要
    }

    for entry in entries:
        document_id = str(entry.get("source_document_id") or "").strip()
        if not document_id:
            continue
        rel = str(entry.get("relative_path") or "").strip()
        if not rel:
            stats["errors"].append(f"{document_id}: manifest 缺少 relative_path")
            continue
        title = str(entry.get("title") or rel or document_id)
        source = _resolve_source_file(roots, rel, root_path)
        if source is None:
            stats["skipped"].append(rel)
            continue
        try:
            content = source.read_text(encoding="utf-8", errors="replace")
        except OSError as exc:
            stats["errors"].append(f"{rel}: {exc}")
            continue
        content_hash = _sha256(content)
        source_uri = rel.replace("\\", "/")
        media_type = "text/markdown" if source.suffix.lower() == ".md" else "text/plain"
        passages = _split_markdown_passages(content)

        stats["documents"].append({
            "document_id": document_id,
            "title": title,
            "source_uri": source_uri,
            "content_hash": content_hash,
            "passages": len(passages),
        })
        stats["registered_documents"] += 1
        stats["registered_passages"] += len(passages)
        if dry_run:
            continue

        await conn.execute(
            """INSERT INTO ledger_documents
               (document_id, title, source_uri, media_type, content_hash,
                parser_id, status, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, 'manifest', 'active', ?, ?)
               ON CONFLICT(document_id) DO UPDATE SET
                 title=excluded.title, source_uri=excluded.source_uri,
                 media_type=excluded.media_type, content_hash=excluded.content_hash,
                 parser_id='manifest', status='active', updated_at=excluded.updated_at""",
            (document_id, title, source_uri, media_type, content_hash, now, now),
        )
        for index, passage in enumerate(passages):
            await conn.execute(
                """INSERT INTO ledger_passages
                   (passage_id, document_id, sequence, heading, quote, text_hash,
                    char_start, char_end, status, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'active', ?)
                   ON CONFLICT(passage_id) DO UPDATE SET
                     sequence=excluded.sequence, heading=excluded.heading,
                     quote=excluded.quote, text_hash=excluded.text_hash,
                     char_start=excluded.char_start, char_end=excluded.char_end,
                     status='active'""",
                (
                    f"{document_id}:p{index}", document_id, index,
                    passage["heading"], passage["quote"], passage["text_hash"],
                    passage["char_start"], passage["char_end"], now,
                ),
            )
        await conn.commit()
    return stats


async def _detect_cold_path_column(conn: Any) -> str:
    cursor = await conn.execute("PRAGMA table_info(cards)")
    columns = {str(row[1]) for row in await cursor.fetchall()}
    for column in _COLD_PATH_COLUMNS:
        if column in columns:
            return column
    return "file_path"


def _target_values(row: Any) -> tuple[str, str, bool]:
    """计算目标 evidence_level / status，并判断是否需要更新（幂等）。"""
    current_status = row["status"]
    current_evidence = row["evidence_level"]
    new_evidence = "derived"
    new_status = current_status if current_status not in _DRAFTABLE_STATUSES else "draft"
    changed = (current_evidence != new_evidence) or (current_status != new_status)
    return new_evidence, new_status, changed


def _atomic_write_json(path: Path, data: dict[str, Any]) -> None:
    """先写临时文件再原子替换，避免写坏冷 JSON。"""
    tmp = path.with_name(path.name + ".tmp")
    try:
        tmp.write_text(
            json.dumps(data, ensure_ascii=False, indent=2, default=str),
            encoding="utf-8",
        )
        os.replace(tmp, path)
    except OSError:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        raise


def _update_cold_file(cold_path: str, evidence_level: str, status: str, stats: dict[str, Any]) -> None:
    """同步更新冷 JSON 文件中的 status/evidence_level；文件缺失计入 skipped_cold。"""
    if not cold_path:
        stats["skipped_cold"] += 1
        return
    path = Path(cold_path)
    if not path.is_file():
        stats["skipped_cold"] += 1
        return
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        stats["skipped_cold"] += 1
        return
    if not isinstance(data, dict):
        stats["skipped_cold"] += 1
        return
    if data.get("evidence_level") == evidence_level and data.get("status") == status:
        return  # 已是最新，幂等不重写
    data["evidence_level"] = evidence_level
    data["status"] = status
    _atomic_write_json(path, data)


async def mark_legacy_cards_derived(
    indexer: Any,
    source_book: str,
    batch_size: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把指定书籍下无来源文档的旧卡标记为 derived/draft。

    - 仅处理 ``source_book`` 且 ``source_document_id=''`` 的卡（无来源即无 passage 锚点）；
    - ``evidence_level -> 'derived'``；``status`` 仅在 空/'draft'/'active' 时置为 'draft'，
      绝不覆盖 'archived'；
    - 同步更新冷 JSON 文件（存在且可解析才更新；文件缺失计入 skipped_cold）；
    - 分批提交（每批一个事务），幂等、可断点续跑。
    """
    conn = indexer.conn
    cold_column = await _detect_cold_path_column(conn)
    batch_size = max(1, int(batch_size))
    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "source_book": source_book,
        "total_cards": 0,
        "updated_cards": 0,
        "skipped_cold": 0,
        "errors": [],
    }

    # Batch 3：keyset pagination——以行号游标推进，避免 OFFSET 在谓词可能在处理中变化时跳行。
    last_rowid = -1
    while True:
        cursor = await conn.execute(
            f"SELECT rowid, card_id, status, evidence_level, {cold_column} AS cold_path "
            f"FROM cards WHERE source_book = ? AND source_document_id = '' AND rowid > ? "
            f"ORDER BY rowid LIMIT ?",
            (source_book, last_rowid, batch_size),
        )
        rows = await cursor.fetchall()
        if not rows:
            break
        stats["total_cards"] += len(rows)

        for row in rows:
            evidence_level, status, changed = _target_values(row)
            if changed:
                stats["updated_cards"] += 1
            if dry_run:
                continue
            if changed:
                await conn.execute(
                    "UPDATE cards SET evidence_level = ?, status = ? WHERE card_id = ?",
                    (evidence_level, status, row["card_id"]),
                )
            try:
                _update_cold_file(row["cold_path"], evidence_level, status, stats)
            except OSError as exc:
                stats["errors"].append(f"{row['card_id']}: 冷文件写入失败 {exc}")
        if not dry_run:
            await conn.commit()
        last_rowid = int(rows[-1]["rowid"])
    return stats


async def verify_source_contract(indexer: Any, source_book: str = "book_b10eaeb7") -> dict[str, Any]:
    """核对 Ledger 来源契约的关键指标。

    返回 ledger_documents / ledger_passages 数量、指定书籍中
    evidence_level='derived' 的卡数、仍为空 source_document_id 的卡数、
    以及 ledger_claims 数（应保持 0，本模块不产生任何 authoritative claim）。
    """
    conn = indexer.conn

    async def count(sql: str, *args: Any) -> int:
        cursor = await conn.execute(sql, args)
        row = await cursor.fetchone()
        return int(row[0]) if row else 0

    return {
        "ledger_documents": await count("SELECT COUNT(*) FROM ledger_documents"),
        "ledger_passages": await count("SELECT COUNT(*) FROM ledger_passages"),
        "legacy_derived_cards": await count(
            "SELECT COUNT(*) FROM cards WHERE source_book = ? AND evidence_level = 'derived'",
            source_book,
        ),
        "empty_source_document_id_cards": await count(
            "SELECT COUNT(*) FROM cards WHERE source_document_id = ''"
        ),
        "ledger_claims": await count("SELECT COUNT(*) FROM ledger_claims"),
    }


async def backfill_legacy_cards_from_cold(
    indexer: Any,
    cold_roots: list[Path],
    source_book: str | None = None,
    batch_size: int = 500,
    dry_run: bool = False,
) -> dict[str, Any]:
    """把冷存储 JSON 卡片回填进旧 schema 的 cards 热表。

    旧库 cards 表没有 ``content`` / ``source_chapter`` / ``detail_path`` /
    ``create_time`` 列（热 summary 为模板化占位），迁移到 Ledger 前需先从冷存储
    ``*.card`` 文件恢复真实内容，否则 ``_project_card_to_ledger`` 读到空 content，
    fingerprint 几乎全部相同，触发 ``ledger_claims.fingerprint`` UNIQUE 冲突。

    - 幂等 ``ALTER TABLE cards ADD COLUMN`` 补齐缺失列；
    - 遍历 cold_roots 下 ``*.card`` 文件（跳过 ``*.ai_backup``），card_id 对应
      热行存在时按最新冷值回填：``content`` 取值优先级
      ``original_fragment``(≥20字) > ``content`` > 热 ``summary``；
    - 每 batch_size 条提交一个事务；每次总是写成最新冷值，重复执行天然幂等，
      可从上次提交点续跑；
    - ``dry_run=True`` 只统计不写库（含不改 schema）。
    """
    conn = indexer.conn
    batch_size = max(1, int(batch_size))

    cursor = await conn.execute("PRAGMA table_info(cards)")
    existing = {str(row[1]) for row in await cursor.fetchall()}
    additions = {
        "content": "TEXT NOT NULL DEFAULT ''",
        "source_chapter": "TEXT NOT NULL DEFAULT ''",
        "detail_path": "TEXT NOT NULL DEFAULT ''",
        "create_time": "TEXT NOT NULL DEFAULT ''",
    }
    missing = [name for name in additions if name not in existing]
    if missing and not dry_run:
        for name in missing:
            await conn.execute(f"ALTER TABLE cards ADD COLUMN {name} {additions[name]}")
        await conn.commit()
        existing.update(missing)

    def _col(name: str, default: str = "") -> str:
        # 旧 schema 可能缺少部分列（尤其 dry_run 不改 schema），用字面量占位保证 SELECT 可执行。
        return name if name in existing else f"'{default}' AS {name}"

    cursor = await conn.execute(
        f"SELECT card_id, source_book, summary, "
        f"{_col('content')}, {_col('source_chapter')}, {_col('detail_path')}, {_col('create_time')} "
        f"FROM cards"
    )
    hot_rows: dict[str, dict[str, str]] = {}
    for row in await cursor.fetchall():
        hot_rows[str(row["card_id"])] = {
            "source_book": str(row["source_book"] or ""),
            "summary": str(row["summary"] or ""),
            "content": str(row["content"] or ""),
            "source_chapter": str(row["source_chapter"] or ""),
            "detail_path": str(row["detail_path"] or ""),
            "create_time": str(row["create_time"] or ""),
        }

    stats: dict[str, Any] = {
        "dry_run": dry_run,
        "source_book": source_book,
        "total_cards": 0,
        "updated_cards": 0,
        "skipped_files": 0,
        "errors": [],
    }

    pending = 0
    for root in cold_roots:
        root = Path(root)
        if not root.is_dir():
            stats["errors"].append(f"{root}: 目录不存在")
            continue
        for cold_path in sorted(root.rglob("*.card")):
            if cold_path.name.endswith(".ai_backup"):
                continue
            try:
                raw = await asyncio.to_thread(cold_path.read_text, encoding="utf-8")
                data = json.loads(raw)
            except (OSError, ValueError) as exc:
                stats["errors"].append(f"{cold_path.name}: 读取/解析失败 {exc}")
                continue
            if not isinstance(data, dict) or not data.get("card_id"):
                stats["errors"].append(f"{cold_path.name}: 缺少 card_id")
                continue
            card_id = str(data["card_id"])
            hot = hot_rows.get(card_id)
            if hot is None:
                stats["skipped_files"] += 1
                continue
            if source_book and hot["source_book"] != source_book:
                stats["skipped_files"] += 1
                continue
            stats["total_cards"] += 1
            pending += 1

            fragment = str(data.get("original_fragment") or "")
            cold_content = str(data.get("content") or "")
            if len(fragment) >= 20:
                new_content = fragment
            elif cold_content:
                new_content = cold_content
            else:
                new_content = hot["summary"]
            new_chapter = str(data.get("source_chapter") or data.get("chapter") or "")
            new_detail_path = str(cold_path)
            new_create_time = str(data.get("create_time") or "")

            changed = (
                hot["content"] != new_content
                or hot["source_chapter"] != new_chapter
                or hot["detail_path"] != new_detail_path
                or hot["create_time"] != new_create_time
            )
            if changed:
                stats["updated_cards"] += 1
            if not dry_run and changed:
                await conn.execute(
                    "UPDATE cards SET content=?, source_chapter=?, detail_path=?, create_time=? WHERE card_id=?",
                    (new_content, new_chapter, new_detail_path, new_create_time, card_id),
                )
            if not dry_run and pending >= batch_size:
                await conn.commit()
                pending = 0
    if not dry_run and pending:
        await conn.commit()
    return stats


__all__ = [
    "register_real_source_documents",
    "mark_legacy_cards_derived",
    "backfill_legacy_cards_from_cold",
    "verify_source_contract",
]
