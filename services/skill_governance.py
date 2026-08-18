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
import uuid
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
CAND_RETIRED = "RETIRED"        # 退休(不再生效,保留历史)

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
                    artifact TEXT NOT NULL DEFAULT '{}',
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
                CREATE TABLE IF NOT EXISTS skg_active_versions (
                    candidate_id TEXT PRIMARY KEY,
                    version INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'ACTIVE',
                    updated_at TEXT NOT NULL
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_skg_version_unique ON skg_skill_versions(candidate_id, version);
                CREATE TABLE IF NOT EXISTS skg_legacy_map (
                    source_system TEXT NOT NULL, legacy_id TEXT NOT NULL, candidate_id TEXT NOT NULL,
                    source_row_hash TEXT NOT NULL DEFAULT '', migrated_at TEXT NOT NULL,
                    PRIMARY KEY(source_system, legacy_id)
                );
                CREATE TABLE IF NOT EXISTS skg_projection_state (
                    projection_key TEXT PRIMARY KEY, candidate_id TEXT NOT NULL, version INTEGER NOT NULL,
                    content_hash TEXT NOT NULL, updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS skg_effect_stats (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    version INTEGER NOT NULL,               -- candidate_id@version 效果归因
                    metric TEXT NOT NULL,                   -- 效果指标(如 win_rate/acceptance)
                    value REAL NOT NULL,
                    sample_size INTEGER NOT NULL DEFAULT 0,
                    observed_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skg_effect_stats ON skg_effect_stats(candidate_id, version);
                CREATE TABLE IF NOT EXISTS skg_test_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT NOT NULL,
                    passed INTEGER NOT NULL,            -- 0/1 最新一次评估是否过测
                    passed_count INTEGER NOT NULL DEFAULT 0,
                    total_count INTEGER NOT NULL DEFAULT 0,
                    note TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_skg_test_results ON skg_test_results(candidate_id);
                """
            )
            columns = {row[1] for row in self._conn.execute("PRAGMA table_info(skg_candidates)").fetchall()}
            if "artifact" not in columns:
                self._conn.execute("ALTER TABLE skg_candidates ADD COLUMN artifact TEXT NOT NULL DEFAULT '{}'")
            for column, definition in (("version", "INTEGER"), ("salt_version", "TEXT NOT NULL DEFAULT 'skg-v1'")):
                columns = {row[1] for row in self._conn.execute("PRAGMA table_info(skg_gray_state)").fetchall()}
                if column not in columns:
                    self._conn.execute(f"ALTER TABLE skg_gray_state ADD COLUMN {column} {definition}")
            self._conn.commit()
        return self._conn

    # ------------------------------------------------------------------ 提交

    def resolve_active_skills(self, context: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        """唯一运行时技能解析器；GRAY 必须提供稳定 task_id。"""
        context = context or {}
        task_id = str(context.get("task_id") or "")
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                """SELECT c.candidate_id, c.name, c.status, a.version, v.prompt, v.snapshot,
                          g.percent, g.version AS gray_version, g.salt_version
                   FROM skg_candidates c
                   JOIN skg_active_versions a ON a.candidate_id=c.candidate_id
                   JOIN skg_skill_versions v ON v.candidate_id=a.candidate_id AND v.version=a.version
                   LEFT JOIN skg_gray_state g ON g.candidate_id=c.candidate_id
                   WHERE c.status IN (?, ?)""",
                (CAND_FULL, CAND_GRAY),
            ).fetchall()
        resolved: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            if item["status"] == CAND_GRAY:
                if not task_id or item.get("gray_version") != item["version"]:
                    continue
                bucket = int(hashlib.sha256(f"{item.get('salt_version') or 'skg-v1'}|{item['candidate_id']}|{item['version']}|{task_id}".encode()).hexdigest(), 16) % 100
                if bucket >= int(item.get("percent") or 0):
                    continue
                rollout = "GRAY"
            else:
                bucket = 0
                rollout = "FULL"
            resolved.append({
                "candidate_id": item["candidate_id"], "version": item["version"], "name": item["name"],
                "prompt": item["prompt"], "artifact": (_json_loads(item["snapshot"], {}).get("artifact") or {}),
                "rollout": rollout, "sample_bucket": bucket,
            })
        return resolved

    def import_legacy_skills(self, skills: list[dict[str, Any]], source: str = "legacy") -> dict[str, Any]:
        imported: list[str] = []
        errors: list[str] = []
        for skill in skills:
            try:
                imported.append(self.import_legacy_skill(skill, source=source))
            except Exception as exc:
                errors.append(str(exc))
        return {"imported": imported, "errors": errors, "total": len(skills)}

    def import_legacy_skill(self, skill: dict[str, Any], source: str = "legacy") -> str:
        """把旧 universal_skills/novel_agent_skills 记录导入唯一候选池。"""
        legacy_id = str(skill.get("skill_id") or skill.get("id") or "")
        conn = self._get_conn()
        if legacy_id:
            with self._lock:
                mapped = conn.execute(
                    "SELECT candidate_id FROM skg_legacy_map WHERE source_system=? AND legacy_id=?",
                    (source, legacy_id),
                ).fetchone()
            if mapped is not None:
                return str(mapped["candidate_id"])
        name = str(skill.get("name") or skill.get("skill_id") or "legacy-skill")
        payload = skill.get("payload", skill.get("content", ""))
        if isinstance(payload, dict):
            prompt = str(payload.get("prompt") or payload.get("content_text") or payload.get("content") or _json_dumps(payload))
        else:
            prompt = str(payload)
        candidate_id = self.submit_candidate(
            name,
            prompt,
            confidence=float(skill.get("confidence", skill.get("effect_score", 0.0)) or 0.0),
            source_task=source,
            source_reflection=str(skill.get("source_reflection", "legacy-import")),
            artifact={"legacy_payload": payload, "legacy_id": legacy_id, "source_system": source},
        )
        if legacy_id:
            row_hash = hashlib.sha256(_json_dumps(skill).encode("utf-8")).hexdigest()
            with self._lock:
                conn.execute(
                    "INSERT INTO skg_legacy_map(source_system, legacy_id, candidate_id, source_row_hash, migrated_at)"
                    " VALUES (?, ?, ?, ?, ?) ON CONFLICT(source_system, legacy_id) DO UPDATE SET"
                    " candidate_id=excluded.candidate_id, source_row_hash=excluded.source_row_hash, migrated_at=excluded.migrated_at",
                    (source, legacy_id, candidate_id, row_hash, _now_iso()),
                )
                conn.commit()
        return candidate_id

    def submit_candidate(
        self,
        name: str,
        prompt: str,
        *,
        confidence: float = 0.0,
        source_task: str = "",
        source_reflection: str = "",
        artifact: dict[str, Any] | None = None,
        dedup_key: str | None = None,
    ) -> str:
        """反思生成的技能候选进入缓冲池（禁止直写正式库——唯一写入入口）。

        按内容哈希去重：同内容候选重复提交直接返回既有 candidate_id。
        `dedup_key` 供版本门使用——同一技能的不同语义版本即使内容相同
        也作为独立候选记录落库（V0.3「候选 content 版本化」需要逐版本回放）。
        """
        if not name or not prompt.strip():
            raise SkillGovernanceError("技能候选 name 与 prompt 为必填项")
        content_hash = hashlib.md5(
            f"{dedup_key or ''}|{prompt.strip()}".encode("utf-8")
        ).hexdigest()[:16]
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
                " source_task, source_reflection, status, content_hash, artifact, created_at, updated_at)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    candidate_id, name, prompt.strip(), float(confidence),
                    source_task, source_reflection, CAND_PENDING,
                    content_hash, _json_dumps(artifact or {}), now, now,
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

    def set_active_version(self, candidate_id: str, version: int, operator: str = "system") -> None:
        if self._get_version(candidate_id, version) is None:
            raise SkillGovernanceError("目标技能版本不存在")
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO skg_active_versions(candidate_id, version, status, updated_at) VALUES (?, ?, 'ACTIVE', ?) ON CONFLICT(candidate_id) DO UPDATE SET version=excluded.version, status='ACTIVE', updated_at=excluded.updated_at",
                (candidate_id, int(version), now),
            )
            conn.commit()
        self._audit(candidate_id, "ACTIVE_VERSION", detail=f"active v{version}", operator=operator)

    def get_active_version(self, candidate_id: str) -> dict[str, Any] | None:
        conn = self._get_conn()
        with self._lock:
            row = conn.execute(
                "SELECT v.*, a.status AS active_status, a.updated_at AS active_updated_at FROM skg_active_versions a JOIN skg_skill_versions v ON v.candidate_id=a.candidate_id AND v.version=a.version WHERE a.candidate_id = ?",
                (candidate_id,),
            ).fetchone()
        if row is None:
            return None
        data = dict(row)
        data["snapshot"] = _json_loads(data.get("snapshot"), {})
        return data

    def list_active_versions(self) -> list[dict[str, Any]]:
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT candidate_id, version, status, updated_at FROM skg_active_versions ORDER BY updated_at DESC"
            ).fetchall()
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------ 版本门（V0.3：测试评估 + 过测才落地）

    def record_test_results(
        self,
        candidate_id: str,
        *,
        passed: bool,
        passed_count: int = 0,
        total_count: int = 0,
        note: str = "",
    ) -> dict[str, Any]:
        """追加一条测试评估记录（append-only，可回放）。版本门的过测依据以最新一条为准。"""
        if self._get_candidate(candidate_id) is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        now = _now_iso()
        with self._lock:
            conn = self._get_conn()
            conn.execute(
                "INSERT INTO skg_test_results(candidate_id, passed, passed_count, total_count, note, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (candidate_id, 1 if passed else 0, int(passed_count), int(total_count), note, now),
            )
            conn.commit()
        record = {
            "candidate_id": candidate_id,
            "passed": bool(passed),
            "passed_count": int(passed_count),
            "total_count": int(total_count),
            "note": note,
            "created_at": now,
        }
        self._audit(
            candidate_id, "TEST_EVALUATION",
            detail=f"过测={bool(passed)} {passed_count}/{total_count}",
            operator="evaluator",
        )
        return record

    def test_results(self, candidate_id: str) -> dict[str, Any]:
        """回放候选的全部测试评估：按时间返回记录 + 最新过测标志 + 聚合通过率。"""
        conn = self._get_conn()
        with self._lock:
            rows = conn.execute(
                "SELECT passed, passed_count, total_count, note, created_at"
                " FROM skg_test_results WHERE candidate_id = ? ORDER BY id ASC",
                (candidate_id,),
            ).fetchall()
        records = [
            {
                "passed": bool(r["passed"]),
                "passed_count": int(r["passed_count"]),
                "total_count": int(r["total_count"]),
                "note": r["note"],
                "created_at": r["created_at"],
            }
            for r in rows
        ]
        total = sum(int(r["total_count"] or 0) for r in records)
        passed = sum(int(r["passed_count"] or 0) for r in records)
        return {
            "candidate_id": candidate_id,
            "records": records,
            "latest_passed": bool(records[-1]["passed"]) if records else None,
            "pass_rate": (passed / total) if total else None,
            "evaluation_count": len(records),
        }

    def passed_tests(self, candidate_id: str) -> bool:
        """版本门依据：最新一条测试评估是否过测。无记录 → False（fail-closed）。"""
        result = self.test_results(candidate_id)
        return bool(result["latest_passed"])

    def promote_through_gate(self, candidate_id: str, operator: str = "author") -> int:
        """作者审核后的版本门落地（V0.3 收口）。

        候选须**同时**满足 ① 人工批准（MANUAL_APPROVED）② 已记录过测（passed_tests）
        才版本化快照并设为激活版；任一不满足即抛错（fail-closed），
        激活版不受影响。与既有 `promote`（面向未带测试的旧流程）并存。
        """
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] != CAND_MANUAL_APPROVED:
            raise SkillGovernanceError(
                f"候选状态 {cand['status']} 不可版本门落地（须作者人工批准 MANUAL_APPROVED）"
            )
        if not self.passed_tests(candidate_id):
            raise SkillGovernanceError("候选未过测试，版本门拒绝落地（fail-closed）")
        version = self._next_version(candidate_id)
        conn = self._get_conn()
        now = _now_iso()
        # 在锁外先取测试评估摘要，避免在持锁状态下再进入 self._lock（非重入锁死锁）
        test_summary = self.test_results(candidate_id)
        artifact = _json_loads(cand.get("artifact"), {})
        with self._lock:
            conn.execute(
                "INSERT INTO skg_skill_versions(candidate_id, version, prompt, snapshot, created_at)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    candidate_id, version, cand["prompt"],
                    _json_dumps(
                        {
                            "name": cand["name"],
                            "semantic_version": artifact.get("semantic_version") or f"v{version}",
                            "confidence": cand["confidence"],
                            "source_task": cand["source_task"],
                            "source_reflection": cand["source_reflection"],
                            "reviewer": cand["reviewer"],
                            "review_note": cand["review_note"],
                            "tests": test_summary,
                            "artifact": artifact,
                        }
                    ),
                    now,
                ),
            )
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_PROMOTED, now, candidate_id),
            )
            conn.commit()
        self._audit(
            candidate_id, "PROMOTE",
            detail=f"版本门落地 v{version}（已过测 + 作者批准）", operator=operator,
        )
        self.set_active_version(candidate_id, version, operator=operator)
        return version

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
                            "artifact": _json_loads(cand.get("artifact"), {}),
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
        self.set_active_version(candidate_id, version, operator=operator)
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
        self.set_active_version(candidate_id, new_version, operator=operator)
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
        version = self.get_active_version(candidate_id)
        if version is None:
            raise SkillGovernanceError("候选没有可绑定的 active version")
        conn = self._get_conn()
        now = _now_iso()
        with self._lock:
            conn.execute(
                "INSERT INTO skg_gray_state(candidate_id, percent, version, salt_version, started_at, updated_at)"
                " VALUES (?, ?, ?, 'skg-v1', ?, ?)"
                " ON CONFLICT(candidate_id) DO UPDATE SET percent=excluded.percent, version=excluded.version,"
                " salt_version=excluded.salt_version, updated_at=excluded.updated_at",
                (candidate_id, percent, int(version["version"]), now, now),
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

    # ------------------------------------------------------------------ 退休/恢复

    def retire(self, candidate_id: str, operator: str = "system", reason: str = "") -> str:
        """退休技能：仅允许从生效态（FULL/GRAY/PROMOTED）进入。

        置 skg_candidates.status=RETIRED、skg_active_versions.status='RETIRED'，
        写审计 RETIRE。退休后不再被 resolve_active_skills 解析，但历史全量保留。
        """
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] not in (CAND_FULL, CAND_GRAY, CAND_PROMOTED):
            raise SkillGovernanceError(
                f"候选状态 {cand['status']} 不可退休（仅生效态 FULL/GRAY/PROMOTED 可退休）"
            )
        conn = self._get_conn()
        now = _now_iso()
        with self._lock:
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_RETIRED, now, candidate_id),
            )
            conn.execute(
                "UPDATE skg_active_versions SET status = ?, updated_at = ? WHERE candidate_id = ?",
                ("RETIRED", now, candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "RETIRE", detail=reason or "技能退休", operator=operator)
        logger.info("[SkillGovernance] 技能退休: %s (%s)", cand["name"], candidate_id)
        return CAND_RETIRED

    def restore(self, candidate_id: str, operator: str = "system") -> str:
        """恢复退休技能：仅允许从 RETIRED 恢复。

        恢复到 PROMOTED（已落地态，保留 active version），写审计 RESTORE；
        恢复生效由调用方按需重新灰度/全量（PROMOTED 不被运行时解析）。
        """
        cand = self._get_candidate(candidate_id)
        if cand is None:
            raise SkillGovernanceError(f"候选不存在: {candidate_id}")
        if cand["status"] != CAND_RETIRED:
            raise SkillGovernanceError(
                f"候选状态 {cand['status']} 不可恢复（仅 RETIRED 可恢复）"
            )
        conn = self._get_conn()
        now = _now_iso()
        with self._lock:
            conn.execute(
                "UPDATE skg_candidates SET status = ?, updated_at = ? WHERE candidate_id = ?",
                (CAND_PROMOTED, now, candidate_id),
            )
            conn.execute(
                "UPDATE skg_active_versions SET status = ?, updated_at = ? WHERE candidate_id = ?",
                ("ACTIVE", now, candidate_id),
            )
            conn.commit()
        self._audit(candidate_id, "RESTORE", detail=f"恢复至 {CAND_PROMOTED}", operator=operator)
        logger.info("[SkillGovernance] 技能恢复: %s (%s)", cand["name"], candidate_id)
        return CAND_PROMOTED

    # ------------------------------------------------------------------ 效果统计

    def record_effect(self, candidate_id: str, version: int, metric: str, value: float, sample_size: int = 0) -> None:
        """写入一条效果观测（按 candidate_id@version 归因）。"""
        if not candidate_id or not metric:
            raise SkillGovernanceError("candidate_id 与 metric 为必填项")
        conn = self._get_conn()
        with self._lock:
            conn.execute(
                "INSERT INTO skg_effect_stats(candidate_id, version, metric, value, sample_size, observed_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (candidate_id, int(version), metric, float(value), int(sample_size or 0), _now_iso()),
            )
            conn.commit()

    def effect_stats(self, candidate_id: str, version: int | None = None) -> list[dict[str, Any]]:
        """按 candidate_id（可限定 version）聚合效果统计（mean/sum/count per metric）。"""
        conn = self._get_conn()
        if version is None:
            sql = (
                "SELECT candidate_id, metric, COUNT(*) AS count, AVG(value) AS mean,"
                " SUM(value) AS sum, SUM(sample_size) AS sample_size"
                " FROM skg_effect_stats WHERE candidate_id = ? GROUP BY metric ORDER BY metric"
            )
            params: tuple[Any, ...] = (candidate_id,)
        else:
            sql = (
                "SELECT candidate_id, version, metric, COUNT(*) AS count, AVG(value) AS mean,"
                " SUM(value) AS sum, SUM(sample_size) AS sample_size"
                " FROM skg_effect_stats WHERE candidate_id = ? AND version = ?"
                " GROUP BY metric ORDER BY metric"
            )
            params = (candidate_id, int(version))
        with self._lock:
            rows = conn.execute(sql, params).fetchall()
        out: list[dict[str, Any]] = []
        for r in rows:
            item = dict(r)
            item["count"] = int(item["count"])
            item["mean"] = round(float(item["mean"]), 6) if item["mean"] is not None else None
            item["sum"] = round(float(item["sum"]), 6) if item["sum"] is not None else 0.0
            item["sample_size"] = int(item["sample_size"] or 0)
            out.append(item)
        return out

    def list_retired(self) -> list[dict[str, Any]]:
        """列出 RETIRED 候选（保留历史）。"""
        return self.list_candidates(status=CAND_RETIRED)

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
    "CAND_PROMOTED", "CAND_GRAY", "CAND_FULL", "CAND_RETIRED",
]
