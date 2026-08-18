"""services/quantify_reflection.py — 量化完成 → 唯一 ReflectionSession 闭环（P1/A3）。

断点
----
量化的完成触发此前仅统计卡片 + 写 Experience（services/quantifier.py::_trigger_quantize_reflection），
不创建可追踪、可回放的 ReflectionSession，缺少 run/source/metrics 等 provenance。

本模块把「每轮量化 → 唯一 ReflectionSession」收敛为确定性、幂等、可回放的节点：
- run_id = sha256(book_id | source_hash | mode)：同输入同键不产生重复会话/副作用；
- provenance（run_id/source_hash/book/mode/model/metrics）持久化到独立表
  ``quantify_reflection_runs``（避免改动既有 reflection_sessions 表的列结构）；
- 首次创建在既有 ``reflection_sessions`` 写入基础行（trigger_type=QUANTIFY），链接到 run_id。

设计约束
- 不伪造成功：写入失败不得返回 created=True；无 source_hash 拒建。
- 幂等：重复运行返回 created=False + 既有 session_id（replay=True），不产生第二个会话。
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from typing import Any, Callable

from pydantic import BaseModel, Field, ConfigDict

_RUNS_TABLE = "quantify_reflection_runs"
_SESSIONS_TABLE = "reflection_sessions"


class QuantifyReflectionRecord(BaseModel):
    """一次量化反思会话的持久化记录（显式契约，禁未知字段透传）。"""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(..., description="确定性运行标识（幂等/回放键）")
    session_id: str = Field(..., description="反射会话 id")
    book_id: str = Field(..., description="被量化书籍/文档 id")
    source_hash: str = Field(..., description="来源快照指纹")
    mode: str = Field(default="both")
    model: str = Field(default="", description="模型 id（空=纯规则/无 LLM）")
    metrics: dict[str, Any] = Field(default_factory=dict, description="本轮量化指标快照")
    created_at: str = Field(default="")
    status: str = Field(default="RUNNING")


def quantify_run_id(book_id: str, source_hash: str, mode: str) -> str:
    """确定性运行标识：同 book+source_hash+mode 必得同 run_id（幂等）。"""
    digest = hashlib.sha256(f"{book_id}|{source_hash}|{mode}".encode("utf-8")).hexdigest()
    return f"quantify_reflect_{digest[:24]}"


async def _ensure_schema(conn: Any) -> None:
    """幂等建表；reflection_sessions 仅用其既有基础列，避免破坏存量结构。"""
    await conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_SESSIONS_TABLE} (
            session_id TEXT PRIMARY KEY, trigger_type TEXT NOT NULL,
            start_time TEXT NOT NULL, status TEXT NOT NULL, report_path TEXT
        )"""
    )
    await conn.execute(
        f"""CREATE TABLE IF NOT EXISTS {_RUNS_TABLE} (
            run_id TEXT PRIMARY KEY, session_id TEXT NOT NULL, book_id TEXT NOT NULL,
            source_hash TEXT NOT NULL, mode TEXT NOT NULL,
            metrics_json TEXT NOT NULL DEFAULT '{{}}', model TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'RUNNING', created_at TEXT NOT NULL
        )"""
    )
    await conn.commit()


async def create_quantify_reflection(
    db: Any,
    *,
    book_id: str,
    source_hash: str,
    mode: str = "both",
    metrics: dict[str, Any] | None = None,
    model: str = "",
    session_factory: Callable[[], BaseModel] | None = None,
) -> dict[str, Any]:
    """为一次量化创建/复用唯一 ReflectionSession（幂等、可回放）。

    :param session_factory: 可选的 ReflectionSession 工厂（复用既有模型），缺省按 run_id 派生。
    :return: ``{created, run_id, session_id, replay}``。
    """
    if not source_hash:
        raise ValueError("source_hash 不能为空：无法建立稳定来源指纹")
    conn = getattr(db, "conn", db)
    await _ensure_schema(conn)

    run_id = quantify_run_id(book_id, source_hash, mode)
    cur = await conn.execute(f"SELECT session_id FROM {_RUNS_TABLE} WHERE run_id = ?", (run_id,))
    row = await cur.fetchone()
    if row:
        return {"created": False, "run_id": run_id, "session_id": str(row[0]), "replay": True}

    now = datetime.now(timezone.utc).isoformat()
    session = session_factory() if session_factory else None
    session_id = getattr(session, "session_id", None) or f"rs_{run_id}"
    metrics_json = json.dumps(metrics or {}, ensure_ascii=False)
    try:
        await conn.execute(
            f"INSERT INTO {_SESSIONS_TABLE} (session_id, trigger_type, start_time, status, report_path) "
            "VALUES (?, ?, ?, ?, ?)",
            (session_id, "QUANTIFY", now, "RUNNING", ""),
        )
        await conn.execute(
            f"INSERT INTO {_RUNS_TABLE} (run_id, session_id, book_id, source_hash, mode, metrics_json, model, status, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'RUNNING', ?)",
            (run_id, session_id, book_id, source_hash, mode, metrics_json, model, now),
        )
        await conn.commit()
    except Exception as exc:  # noqa: BLE001 - 写入失败绝不返回成功
        raise RuntimeError(f"量化反思会话持久化失败: {type(exc).__name__}: {exc}") from exc
    return {"created": True, "run_id": run_id, "session_id": session_id, "replay": False}


async def list_reflection_runs(db: Any, book_id: str | None = None) -> list[QuantifyReflectionRecord]:
    """查询量化反思运行记录（可回放）；按 book_id 过滤，最新在前。"""
    conn = getattr(db, "conn", db)
    await _ensure_schema(conn)
    sql = f"SELECT run_id, session_id, book_id, source_hash, mode, metrics_json, model, status, created_at FROM {_RUNS_TABLE}"
    args: tuple = ()
    if book_id:
        sql += " WHERE book_id = ?"
        args = (book_id,)
    sql += " ORDER BY created_at DESC"
    cursor = await conn.execute(sql, args)
    rows = await cursor.fetchall()
    result: list[QuantifyReflectionRecord] = []
    for r in rows:
        try:
            result.append(
                QuantifyReflectionRecord(
                    run_id=r[0], session_id=r[1], book_id=r[2], source_hash=r[3], mode=r[4],
                    metrics=json.loads(r[5] or "{}"), model=r[6], status=r[7], created_at=r[8],
                )
            )
        except (json.JSONDecodeError, TypeError, ValueError):
            continue
    return result


__all__ = ["QuantifyReflectionRecord", "quantify_run_id", "create_quantify_reflection", "list_reflection_runs"]