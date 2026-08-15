"""
services/skill_governance.py — 技能库版本化 + 审核流标准化（P4-3）
====================================================================
三驾马车「反思学习系统（后置成长层）」的安全沉淀闭环，纯增量模块。

核心链路（与 P0-2 已落地的「临时审核缓冲层」平滑升级）：
    反思候选 → 技能缓冲池（禁止直写正式库）
            → 规则自动初审（字段完整性/置信度/重复检测）
            → 人工复核（approve / reject）
            → 版本化落地（v1 快照，此后每轮迭代新版本）
            → 灰度落地（小范围验证 → 全量）
            → 审计日志全程可查

兼容性约定（P0-P3 成果不回滚、不重构）：
- 独立数据文件 skill_governance.db，独立表前缀 skg_*；
- 不修改 reflection_trigger / rule_extractor / novel_agent_skill_store 的任何逻辑；
- 本模块作为「正式技能库」的受控写入闸口，调用方须经由本模块落地，
  直接写库路径保持禁止（P0-2 规则延续）。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("ai_v4_skill_gov")


class SkillGovernanceError(RuntimeError):
    """技能治理异常（重复提交/非法审核流转/版本不存在等）。"""


# -- 状态常量 ----------------------------------------------------------------

CAND_PENDING = "PENDING"        # 待初审
CAND_AUTO_APPROVED = "AUTO_APPROVED"  # 规则初审通过，待人工复核
CAND_AUTO_REJECTED = "AUTO_REJECTED"  # 规则初审拦截
CAND_MANUAL_APPROVED = "MANUAL_APPROVED"  # 人工复核通过，待落地
CAND_MANUAL_REJECTED = "MANUAL_REJECTED"  # 人工复核驳回
CAND_PROMOTED = "PROMOTED"      # 已版本化落地正式库
CAND_GRAY = "GRAY"              # 灰度验证中
CAND_FULL = "FULL"              # 已全量生效

# 人工复核可操作的前置状态
_REVIEWABLE = (CAND_AUTO_APPROVED,)


def _now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, default=str)


def _json_loads(text: str | None, default: Any = None) -> Any:
    if not text:
        return default
    try:
        return json.loads(text)
    except (ValueError, TypeError):
        return default


class SkillGovernance:
    """技能缓冲池 + 双层审核 + 版本化 + 灰度 + 审计 一体化治理闸口。"""

    def __init__(
        self,
        db_path: str | Path | None = None,
        *,
        auto_review_min_confidence: float = 0.6,
    ) -> None:
        if db_path is None:
            from core.path_resolver import get_app_data_dir
            db_path = Path(get_app_data_dir()) / "skill_governance.db"
        self._db_path = Path(db_path)
        self._auto_review_min_confidence = auto_review_min_confidence
        self._conn: sqlite3.Connection | None = None
        self._lock = threading.Lock()

    # ------------------------------------------------------------------ 存储

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._db_path.parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(self._db_path), check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
            self._conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS skg_candidates (
                    candidate_id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    prompt TEXT NOT NULL,
                    confidence REAL NOT NULL DEFAULT 0,
                    source_task TEXT NOT NULL DEFAULT '',   -- 触发任务
                    source_reflection TEXT NOT NULL DEFAULT '',  -- 反思会话
                    status TEXT NOT NULL DEFAULT 'PENDING',
                    review_note TEXT NOT NULL DEFAULT '',
                    reviewer TEXT NOT NULL DEFAULT '',
                    content_hash TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skg_status ON skg_candidates(status);
                CREATE TABLE IF NOT EXISTS skg_skill_versions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    version INTEGER NOT NULL,               -- 1,2,3...
                    prompt TEXT NOT NULL,
                    snapshot TEXT NOT NULL DEFAULT '{}',    -- 版本快照（完整上下文）
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skg_ver ON skg_skill_versions(candidate_id);
                CREATE TABLE IF NOT EXISTS skg_gray_state (
                    candidate_id TEXT PRIMARY KEY,
                    percent INTEGER NOT NULL DEFAULT 0,     -- 0=未灰度, 1-99=验证中, 100=全量
                    started_at TEXT NOT NULL DEFAULT '',
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skg_audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    event TEXT NOT NULL,        -- SUBMIT/AUTO_REVIEW/APPROVE/REJECT/PROMOTE/
                                               -- NEW_VERSION/ROLLBACK/GRAY_START/GRAY_FULL/GRAY_STOP
                    detail TEXT NOT NULL DEFAULT '',
                    operator TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skg_audit ON skg_audit_log(candidate_id);
                """
            )
            self._conn.commit()
        return self._conn

    # ------------------------------------------------------------------ 提交

    def submit_candidate(
        self,
        name: str,
        prompt: str,
        *,
        confidence: float = 0.0,
        source_task: str = "",
        source_reflection: str = "",
    ) -> str:
        """反思生成的技能候选进入缓冲池（禁止直写正式库——唯一写入入口）。

        按内容哈希去重：同内容候选重复提交直接返回既有 candidate_id。
        """
        if not name or not prompt.strip():
            raise SkillGovernanceError("技能候选 name 与 prompt 为必填项")
        content_hash = hashlib.md5(prompt.strip().encode("utf-8")).hexdigest()[:16]
        conn = self._get_conn()
        now = _now_iso()
        with self._lock:
            dup = conn.execute(
                "SELECT candidate_id FROM skg_candidates WHERE content_hash = ?",
                (content_hash,),
            ).fetchone()
            if dup is not None:
                return str(dup["candidate_id"])
            candidate_id = f"skillcand_{content_hash}"
            conn.execute(
                "INSERT INTO skg_candidates(candidate_id, name, prompt, confidence,"
                " source_task, source_reflection, status, content_hash, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id, name, prompt.strip(), float(confidence),
                    source_task, source_reflection, CAND_PENDING,
                    content_hash, now, now,
                ),
            )
            conn.commit()
        self._audit(candidate_id, "SUBMIT", detail=f"来源任务={source_task} 反思={source_reflection}")
        logger.info("[SkillGovernance] 技能候选进入缓冲池: %s (%s)", name, candidate_id)
        return candidate_id

    # ------------------------------------------------------------------ 双层审核

    def auto_review(self, candidate_id: str) -> str:
        """规则自动初审：字段完整性 / 置信度阈值 / 内容长度 / 高危占位拦截。

        通过 → AUTO_APPROVED（待人工复核）；拦截 → AUTO_REJECTED（附原因）。
        """
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] != CAND_PENDING:
            raise SkillGovernanceError(f"候选状态 {cand['status']} 不可初审")
        reasons: list[str] = []
        prompt = cand["prompt"] or ""
        if len(prompt) < 20:
            reasons.append("内容过短（<20 字），疑似残缺样本")
        if cand["confidence"] < self._auto_review_min_confidence:
            reasons.append(
                f"置信度 {cand['confidence']} 低于阈值 {self._auto_review_min_confidence}"
            )
        if any(marker in prompt for marker in ("{{待填", "<TODO>", "PLACEHOLDER")):
            reasons.append("包含未填充占位符")
        if any(word in prompt for word in ("攻击", "越狱", "绕过", "泄露密钥")):
            reasons.append("命中高危语义拦截词")
        if reasons:
            self._update_status(candidate_id, CAND_AUTO_REJECTED, note="；".join(reasons))
            self._audit(candidate_id, "AUTO_REVIEW", detail="拦截: " + "；".join(reasons), operator="auto")
            return CAND_AUTO_REJECTED
        self._update_status(candidate_id, CAND_AUTO_APPROVED, note="规则初审通过，待人工复核")
        self._audit(candidate_id, "AUTO_REVIEW", detail="通过", operator="auto")
        return CAND_AUTO_APPROVED

    def manual_approve(self, candidate_id: str, reviewer: str, note: str = "") -> str:
        """人工复核通过。"""
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] not in _REVIEWABLE:
            raise SkillGovernanceError(f"候选状态 {cand['status']} 不可人工复核")
        self._update_status(candidate_id, CAND_MANUAL_APPROVED, note=note, reviewer=reviewer)
        self._audit(candidate_id, "APPROVE", detail=note, operator=reviewer)
        return CAND_MANUAL_APPROVED

    def reject(self, candidate_id: str, reviewer: str, reason: str) -> str:
        """人工复核驳回（可驳回初审通过或待落地状态的候选）。"""
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] not in (*_REVIEWABLE, CAND_MANUAL_APPROVED):
            raise SkillGovernanceError(f"候选状态 {cand['status']} 不可驳回")
        self._update_status(candidate_id, CAND_MANUAL_REJECTED, note=reason, reviewer=reviewer)
        self._audit(candidate_id, "REJECT", detail=reason, operator=reviewer)
        return CAND_MANUAL_REJECTED

    # ------------------------------------------------------------------ 版本化落地

    def promote(self, candidate_id: str, operator: str = "system") -> int:
        """审核通过 → 版本化落地正式库（首个版本 v1；再次调用产生新版本快照）。"""
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] != CAND_MANUAL_APPROVED and not self._has_versions(candidate_id):
            raise SkillGovernanceError(
                f"候选状态 {cand['status']} 不可落地（须人工复核通过）"
            )
        version = self._next_version(candidate_id)
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO skg_skill_versions(candidate_id, version, prompt, snapshot, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    candidate_id, version, cand["prompt"],
                    _json_dumps(
                        {
                            "name": cand["name"],
                            "confidence": cand["confidence"],
                            "source_task": cand["source_task"],
                            "source_reflection": cand["source_reflection"],
                            "reviewer": cand["reviewer"],
                            "review_note": cand["review_note"],
                        }
                    ),
                    _now_iso(),
                ),
            )
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_PROMOTED, _now_iso(), candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "PROMOTE", detail=f"落地版本 v{version}", operator=operator)
        return version

    def compare_versions(self, candidate_id: str, v1: int, v2: int) -> dict[str, Any]:
        """新旧版本对比：返回两版 prompt 与差异摘要。"""
        ver_a = self._get_version(candidate_id, v1)
        ver_b = self._get_version(candidate_id, v2)
        if ver_a is None or ver_b is None:
            raise SkillGovernanceError("对比版本不存在")
        pa, pb = ver_a["prompt"], ver_b["prompt"]
        return {
            "version_a": v1,
            "version_b": v2,
            "prompt_a": pa,
            "prompt_b": pb,
            "changed": pa != pb,
            "len_delta": len(pb) - len(pa),
        }

    def rollback(self, candidate_id: str, target_version: int, operator: str = "system") -> int:
        """版本回滚：将目标版本内容作为新版本快照落地（历史溯源不丢失）。"""
        ver = self._get_version(candidate_id, target_version)
        if ver is None:
            raise SkillGovernanceError(f"目标版本不存在: v{target_version}")
        conn = self._get_conn()
        new_version = self._next_version(candidate_id)
        with self._lock:
            conn.execute(
                "INSERT INTO skg_skill_versions(candidate_id, version, prompt, snapshot, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    candidate_id, new_version, ver["prompt"],
                    _json_dumps({"rollback_from": target_version, **ver["snapshot"]}),
                    _now_iso(),
                ),
            )
            conn.commit()
        self._audit(
            candidate_id, "ROLLBACK",
            detail=f"回滚至 v{target_version}，生成新版本 v{new_version}",
            operator=operator,
        )
        return new_version

    def version_history(self, candidate_id: str) -> list[dict[str, Any]]:
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT * FROM skg_skill_versions WHERE candidate_id = ? ORDER BY version ASC",
                (candidate_id,),
            ).fetchall()
        return [
            {
                "version": r["version"],
                "prompt": r["prompt"],
                "snapshot": _json_loads(r["snapshot"], {}),
                "created_at": r["created_at"],
            }
            for r in rows
        ]

    # ------------------------------------------------------------------ 灰度

    def start_grayscale(self, candidate_id: str, percent: int) -> None:
        """灰度落地：小范围验证（percent 1-99）。候选须已有版本。"""
        if not 1 <= percent <= 99:
            raise SkillGovernanceError("灰度比例必须在 1-99 之间")
        if not self._has_versions(candidate_id):
            raise SkillGovernanceError("候选尚未落地任何版本，无法灰度")
        conn = self._get_conn()
        now = _now_iso()
        with self._lock:
            conn.execute(
                "INSERT INTO skg_gray_state(candidate_id, percent, started_at, updated_at)"
                " VALUES (?, ?, ?, ?)"
                " ON CONFLICT(candidate_id) DO UPDATE SET percent = excluded.percent,"
                " updated_at = excluded.updated_at",
                (candidate_id, percent, now, now),
            )
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_GRAY, now, candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "GRAY_START", detail=f"{percent}%")

    def promote_full(self, candidate_id: str) -> None:
        """灰度验证稳定 → 全量落地（100%）。"""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE skg_gray_state SET percent = 100, updated_at = ? WHERE candidate_id = ?",
                (_now_iso(), candidate_id),
            )
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_FULL, _now_iso(), candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "GRAY_FULL", detail="全量落地")

    def stop_grayscale(self, candidate_id: str) -> None:
        """灰度中止：回退灰度，候选回到已落地状态。"""
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE skg_gray_state SET percent = 0, updated_at = ? WHERE candidate_id = ?",
                (_now_iso(), candidate_id),
            )
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_PROMOTED, _now_iso(), candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "GRAY_STOP", detail="灰度中止")

    # ------------------------------------------------------------------ 审计/查询

    def audit_trail(self, candidate_id: str, limit: int = 50) -> list[dict[str, Any]]:
        """沉淀审计：生成来源/触发任务/审核记录/落地时间/迭代版本全程可查。"""
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT * FROM skg_audit_log WHERE candidate_id = ? ORDER BY id DESC LIMIT ?",
                (candidate_id, int(limit)),
            ).fetchall()
        return [dict(r) for r in rows]

    def list_candidates(self, status: str | None = None) -> list[dict[str, Any]]:
        conn = self._get_conn()
        with self._lock:
            if status:
                rows = conn.execute(
                    "SELECT * FROM skg_candidates WHERE status = ? ORDER BY created_at DESC",
                    (status,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM skg_candidates ORDER BY created_at DESC"
                ).fetchall()
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------ 内部

    def _get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT * FROM skg_candidates WHERE candidate_id = ?", (candidate_id,)
            ).fetchone()
        return dict(row) if row else None

    def _update_status(
        self,
        candidate_id: str,
        status: str,
        note: str = "",
        reviewer: str = "",
    ) -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "UPDATE skg_candidates SET status = ?, review_note = ?, reviewer = ?,"
                " updated_at = ? WHERE candidate_id = ?",
                (status, note, reviewer, _now_iso(), candidate_id),
            )
            conn.commit()

    def _has_versions(self, candidate_id: str) -> bool:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT 1 FROM skg_skill_versions WHERE candidate_id = ? LIMIT 1",
                (candidate_id,),
            ).fetchone()
        return row is not None

    def _next_version(self, candidate_id: str) -> int:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT MAX(version) AS mv FROM skg_skill_versions WHERE candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        return int(row["mv"] or 0) + 1

    def _get_version(self, candidate_id: str, version: int) -> dict[str, Any] | None:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT * FROM skg_skill_versions WHERE candidate_id = ? AND version = ?",
                (candidate_id, int(version)),
            ).fetchone()
        if row is None:
            return None
        return {
            "version": row["version"],
            "prompt": row["prompt"],
            "snapshot": _json_loads(row["snapshot"], {}),
            "created_at": row["created_at"],
        }

    def _audit(self, candidate_id: str, event: str, detail: str = "", operator: str = "") -> None:
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO skg_audit_log(candidate_id, event, detail, operator, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (candidate_id, event, detail, operator, _now_iso()),
            )
            conn.commit()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None


# -- 异步适配（管家调度层可按需 await） -------------------------------------

class AsyncSkillGovernance:
    """SkillGovernance 的 asyncio 适配壳（核心逻辑同步，DB 操作入线程池）。"""

    def __init__(self, governance: SkillGovernance) -> None:
        self._g = governance

    def __getattr__(self, name: str) -> Any:
        attr = getattr(self._g, name)
        if callable(attr) and not name.startswith("_"):
            async def _runner(*args, **kwargs):
                return await asyncio.to_thread(attr, *args, **kwargs)
            return _runner
        return attr


__all__ = [
    "SkillGovernance",
    "AsyncSkillGovernance",
    "SkillGovernanceError",
    "CAND_PENDING", "CAND_AUTO_APPROVED", "CAND_AUTO_REJECTED",
    "CAND_MANUAL_APPROVED", "CAND_MANUAL_REJECTED",
    "CAND_PROMOTED", "CAND_GRAY", "CAND_FULL",
]
