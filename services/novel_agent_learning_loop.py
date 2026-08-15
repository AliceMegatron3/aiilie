"""
services/novel_agent_learning_loop.py — 多智能体小说自学习闭环服务 (第十部分)
==========================================================================
1. run_reflection_learn():
   - 读取审计样本 → 调用 AgentRuleExtractor / NovelAgentSkillExtractor
   - 规则部署：高置信度 → 生效；低置信度 → 补丁B人工审核队列
   - 技能沉淀：保存为批次2 InfoCard
2. evaluate_and_lifecycle():
   - 联动补丁B RuleLifecycleManager：effect_score 过低自动降权、标记归档
   - 只降权不删除，支持用户手动恢复启用
   - 新生成策略对比历史基线，命中率没有提升则拒绝自动启用
3. 约束：
   - 开关 novel_agent.enable_self_reflect_learn=false 时整套关闭
   - 反思任务封装为批次1低优先级 CommandTask (priority=7)
   - 只新增规则、技能卡片；禁止修改内置模板与子Agent底层代码
"""
from __future__ import annotations
import json
import logging
import uuid
from typing import Any
from core.config_manager import config_manager
from extractors.novel_agent_extractors import AgentRuleExtractor, NovelAgentSkillExtractor
from models.task import ReflectionTask
from models.novel_agent import NovelAgentSkill
logger = logging.getLogger(__name__)
# 规则生命周期状态
RULE_STATUS_ACTIVE = "ACTIVE"
RULE_STATUS_PENDING_REVIEW = "PENDING_REVIEW"
RULE_STATUS_ARCHIVED = "ARCHIVED"
RULE_STATUS_DEPRECATED = "DEPRECATED"
class NovelAgentLearningLoop:
    """自学习闭环总成：提取 → 部署 → 效果评估 → 优胜劣汰。"""
    def __init__(
        self,
        db,
        audit_store,
        skill_store,
        optimization_applier,
        task_manager=None,
        indexer=None,
    ) -> None:
        self.db = db
        self.audit_store = audit_store
        self.skill_store = skill_store
        self.optimization_applier = optimization_applier
        self.task_manager = task_manager
        self.indexer = indexer
        # 解析配置
        self.min_sample_count = config_manager.get_int("novel_agent.min_sample_count", 5)
        conf = config_manager.get("novel_agent.confidence_threshold", 0.75)
        try:
            self.confidence_threshold = float(conf or 0.75)
        except (TypeError, ValueError):
            self.confidence_threshold = 0.75
        self.rule_extractor = AgentRuleExtractor(
            confidence_threshold=self.confidence_threshold,
            min_sample_count=self.min_sample_count,
        )
        self.skill_extractor = NovelAgentSkillExtractor(
            min_sample_count=self.min_sample_count,
        )
    # ============================================================
    # 总开关
    # ============================================================
    @staticmethod
    def is_learn_enabled() -> bool:
        """子开关 novel_agent.enable_self_reflect_learn。"""
        return config_manager.get_bool("novel_agent.enable_self_reflect_learn", True)
    # ============================================================
    # 提交反思任务（批次1低优先级 CommandTask / ReflectionTask）
    # ============================================================
    async def submit_reflection_task(self) -> str | None:
        """
        将反思学习封装为批次1低优先级任务 (priority=7)，不抢占用户创作算力。
        返回 task_id / session_id；开关关闭或未装配队列时返回 None。
        """
        if not self.is_learn_enabled():
            logger.info("[NovelAgentLearningLoop] enable_self_reflect_learn=false，跳过反思任务提交")
            return None
        if self.task_manager is None:
            logger.warning("[NovelAgentLearningLoop] task_manager 未装配，无法投递反思任务")
            return None
        session_id = f"novellearn_{uuid.uuid4().hex[:12]}"
        task = ReflectionTask(
            task_id=session_id,
            priority=7,  # 极低优先级，不抢占用户创作
            status="PENDING",
            session_id=session_id,
        )
        await self.task_manager.submit_task(task)
        logger.info("[NovelAgentLearningLoop] 反思任务已投递 (priority=7): %s", session_id)
        return session_id
    # ============================================================
    # 反思执行：提取 → 部署
    # ============================================================
    async def process_reflection_task(self) -> dict[str, Any]:
        """
        反思任务核心执行体（供 TaskManager 回调）。
        返回部署统计。
        """
        if not self.is_learn_enabled():
            return {"skipped": True, "reason": "enable_self_reflect_learn=false"}
        # 1. 读取审计样本（须达到最小样本数）
        samples = await self.audit_store.export_samples(self.min_sample_count)
        if not samples:
            logger.info("[NovelAgentLearningLoop] 审计样本不足最小样本数 %d，跳过反思", self.min_sample_count)
            return {"skipped": True, "reason": "insufficient_samples", "sample_count": len(samples)}
        result = {"sample_count": len(samples), "rules_deployed": 0, "skills_deployed": 0}
        # 2. 提炼调度规则
        try:
            rules = self.rule_extractor.extract_rules(samples)
            deployed = await self._deploy_rules(rules)
            result["rules_deployed"] = deployed
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 规则提炼失败: %s", exc)
        # 3. 沉淀创作技能
        try:
            skills = self.skill_extractor.extract_skills(samples)
            for skill in skills:
                await self.skill_store.save_skill(skill)
            result["skills_deployed"] = len(skills)
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 技能沉淀失败: %s", exc)
        # 4. 效果评估与优胜劣汰
        try:
            await self.evaluate_and_lifecycle()
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 效果评估失败: %s", exc)
        logger.info("[NovelAgentLearningLoop] 反思完成: %s", json.dumps(result, ensure_ascii=False))
        return result
    async def _deploy_rules(self, rules: list[dict[str, Any]]) -> int:
        """规则部署：高置信度生效，低置信度进入人工审核队列。"""
        deployed = 0
        for rule_dict in rules:
            rule_id = rule_dict.get("rule_id") or f"rule_{uuid.uuid4().hex[:12]}"
            status = rule_dict.get("status", RULE_STATUS_ACTIVE)
            is_active = 1 if status == RULE_STATUS_ACTIVE else 0
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            try:
                await self.db.conn.execute(
                    """INSERT INTO optimization_rules
                       (rule_id, scope, condition, action, confidence, is_active, created_at, feedback_score)
                       VALUES (?, ?, ?, ?, ?, ?, ?, 0.0)
                       ON CONFLICT(rule_id) DO NOTHING""",
                    (
                        rule_id,
                        rule_dict.get("scope", "NOVEL_AGENT_SCHEDULE"),
                        json.dumps(rule_dict.get("condition", {}), ensure_ascii=False),
                        json.dumps(rule_dict.get("action", {}), ensure_ascii=False),
                        float(rule_dict.get("confidence", 0.0)),
                        is_active,
                        now,
                    ),
                )
                await self.db.conn.commit()
                deployed += 1
                if status == RULE_STATUS_PENDING_REVIEW:
                    # 低置信度规则 → 补丁B人工审核队列
                    await self._enqueue_manual_review(rule_id, rule_dict)
            except Exception as exc:
                logger.warning("[NovelAgentLearningLoop] 规则 %s 部署失败: %s", rule_id, exc)
        return deployed
    async def _enqueue_manual_review(self, rule_id: str, rule_dict: dict[str, Any]) -> None:
        """低置信度规则进入补丁B人工审核队列（pending 表）。"""
        try:
            await self.db.conn.execute(
                """CREATE TABLE IF NOT EXISTS rule_review_queue (
                    review_id TEXT PRIMARY KEY,
                    rule_id TEXT NOT NULL,
                    rule_payload TEXT NOT NULL,
                    review_status TEXT DEFAULT 'PENDING',
                    created_at TEXT NOT NULL
                )"""
            )
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc).isoformat()
            await self.db.conn.execute(
                """INSERT OR IGNORE INTO rule_review_queue
                   (review_id, rule_id, rule_payload, review_status, created_at)
                   VALUES (?, ?, ?, 'PENDING', ?)""",
                (f"review_{uuid.uuid4().hex[:12]}", rule_id, json.dumps(rule_dict, ensure_ascii=False), now),
            )
            await self.db.conn.commit()
            logger.info("[NovelAgentLearningLoop] 规则 %s 已进入人工审核队列", rule_id)
        except Exception as exc:
            logger.warning("[NovelAgentLearningLoop] 规则入审核队列失败: %s", exc)
    # ============================================================
    # 效果评估与优胜劣汰（联动补丁B RuleLifecycleManager）
    # ============================================================
    async def evaluate_and_lifecycle(self) -> dict[str, Any]:
        """
        评估每一条 NOVEL_AGENT_SCHEDULE 规则和每个 NovelAgentSkill 的 effect_score：
        - effect_score 过低 → 自动降权、标记归档（只降权不删除，用户可手动恢复）
        - 新生成策略对比历史基线，命中率未提升则拒绝自动启用
        """
        outcome = {"rules_archived": 0, "skills_archived": 0, "auto_enable_rejected": 0}
        try:
            # ── 规则评估 ──
            cursor = await self.db.conn.execute(
                "SELECT * FROM optimization_rules WHERE scope = 'NOVEL_AGENT_SCHEDULE'"
            )
            rows = await cursor.fetchall()
            columns = [d[0] for d in cursor.description]
            for row in rows:
                rule = dict(zip(columns, row))
                rule_id = rule["rule_id"]
                # 统计该规则的历史任务样本
                samples_cursor = await self.db.conn.execute(
                    "SELECT hit_score, ooc_warnings, circuit_break FROM novel_agent_audits WHERE used_rule_ids LIKE ?",
                    (f"%{rule_id}%",),
                )
                s_rows = await samples_cursor.fetchall()
                rule_samples = [
                    {
                        "hit_score": r[0],
                        "ooc_warnings": r[1],
                        "circuit_break": bool(r[2]),
                    }
                    for r in s_rows
                ]
                if not rule_samples:
                    continue
                rule_effect = self._compute_rule_effect(rule_samples)
                # 联动补丁B：effect_score 过低 → 自动降权归档
                if rule_effect < 0.25 and rule.get("is_active") == 1:
                    await self.db.conn.execute(
                        "UPDATE optimization_rules SET is_active = 0 WHERE rule_id = ?",
                        (rule_id,),
                    )
                    await self.db.conn.commit()
                    outcome["rules_archived"] += 1
                    logger.warning("[NovelAgentLearningLoop] 规则 %s 效果分 %.3f 过低，已自动降权归档", rule_id, rule_effect)
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 规则效果评估失败: %s", exc)
        # ── 技能评估 ──
        try:
            skills = await self.skill_store.list_skills(limit=200)
            for skill in skills:
                payload = skill.get("payload", {})
                stats = payload.get("effect_stats", {}) or {}
                effect_score = self.skill_store.compute_effect_score(stats)
                if skill.get("status") == "ACTIVE" and effect_score < 0.25:
                    await self.skill_store.toggle_skill(skill["skill_id"], "ARCHIVED")
                    outcome["skills_archived"] += 1
                    logger.warning("[NovelAgentLearningLoop] 技能 %s 效果分 %.3f 过低，已归档（可手动恢复）", skill["skill_id"], effect_score)
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 技能效果评估失败: %s", exc)
        # ── 基线对比：拒绝未提升的自动启用 ──
        try:
            # 基线：历史全部审计平均命中
            all_samples = await self.audit_store.export_samples(self.min_sample_count)
            if all_samples:
                baseline_hit = sum(s.get("hit_score", 0.0) for s in all_samples) / len(all_samples)
                # 新规则平均命中低于基线 → 拒绝生效
                cursor = await self.db.conn.execute(
                    "SELECT rule_id FROM optimization_rules WHERE scope = 'NOVEL_AGENT_SCHEDULE' AND is_active = 1"
                )
                active_rows = await cursor.fetchall()
                for (rule_id,) in active_rows:
                    samples_c = await self.db.conn.execute(
                        "SELECT hit_score FROM novel_agent_audits WHERE used_rule_ids LIKE ?",
                        (f"%{rule_id}%",),
                    )
                    s_rows = await samples_c.fetchall()
                    if s_rows:
                        avg_hit = sum(r[0] for r in s_rows) / len(s_rows)
                        if avg_hit < baseline_hit:
                            await self.db.conn.execute(
                                "UPDATE optimization_rules SET is_active = 0 WHERE rule_id = ?",
                                (rule_id,),
                            )
                            await self.db.conn.commit()
                            outcome["auto_enable_rejected"] += 1
                            logger.info("[NovelAgentLearningLoop] 规则 %s 命中率 %.3f 未超基线 %.3f，拒绝自动启用", rule_id, avg_hit, baseline_hit)
        except Exception as exc:
            logger.exception("[NovelAgentLearningLoop] 基线对比失败: %s", exc)
        return outcome
    @staticmethod
    def _compute_rule_effect(rule_samples: list) -> float:
        """基于审计样本（dict 列表）计算规则效果分（简化：命中率 - OOC率 - 熔断率）。"""
        if not rule_samples:
            return 0.0
        total = len(rule_samples)
        hit_sum = 0.0
        ooc_sum = 0
        circuit_sum = 0
        for s in rule_samples:
            if isinstance(s, dict):
                d = s
            else:
                try:
                    d = dict(s)
                except Exception:
                    d = {}
            hit_sum += float(d.get("hit_score", 0.0) or 0.0)
            ooc_sum += int(d.get("ooc_warnings", 0) or 0)
            circuit_sum += 1 if d.get("circuit_break") else 0
        avg_hit = hit_sum / total
        ooc_rate = ooc_sum / total
        circuit_rate = circuit_sum / total
        return round(max(0.0, min(1.0, avg_hit * 0.6 - ooc_rate * 0.2 - circuit_rate * 0.2)), 4)