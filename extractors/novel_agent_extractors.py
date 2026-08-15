"""
extractors/novel_agent_extractors.py — 多智能体小说自学习提取器插件 (第十部分)
==========================================================================
以插件式接入批次4 ReflectionEngine：
1. AgentRuleExtractor：
   - 分析审计样本，产出 scope='NOVEL_AGENT_SCHEDULE' 的 OptimizationRule
   - 控制子Agent启停、算力模式、权重
   - 遵守置信度阈值，低置信度进入补丁B人工审核队列
2. NovelAgentSkillExtractor：
   - 从高命中率任务样本沉淀 NovelAgentSkill 创作技能卡片
   - 以批次2 InfoCard (card_sub_type='novel_agent_skill') 保存
约束：必须达到配置 min_sample_count 最小样本数量才允许生成规则/技能。
纯叠加式：不修改批次4提取器核心逻辑。
"""
from __future__ import annotations
import json
import logging
import uuid
from typing import Any
from models.reflection import OptimizationRule
from models.novel_agent import NovelAgentSkill
logger = logging.getLogger(__name__)
# 默认最小样本数（可被 config novel_agent.min_sample_count 覆盖）
DEFAULT_MIN_SAMPLE_COUNT = 5
# 置信度阈值：低于此值进入人工审核队列
DEFAULT_CONFIDENCE_THRESHOLD = 0.75
def _gen_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"
class AgentRuleExtractor:
    """
    从多智能体审计样本中提炼流程调度优化规则 (scope='NOVEL_AGENT_SCHEDULE')。
    """
    def __init__(
        self,
        confidence_threshold: float = DEFAULT_CONFIDENCE_THRESHOLD,
        min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT,
    ) -> None:
        self.confidence_threshold = confidence_threshold
        self.min_sample_count = min_sample_count
    def extract_rules(self, samples: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """
        输入：审计样本列表（dict 形式）
        输出：规则字典列表，字段同 optimization_rules 表 + 元信息
            {rule_id, scope, condition, action, confidence, status}
        """
        if len(samples) < self.min_sample_count:
            logger.info(
                "[AgentRuleExtractor] 样本数 %d < 最小要求 %d，跳过规则生成（防小样本误判）",
                len(samples), self.min_sample_count,
            )
            return []
        # ── 统计子Agent表现 ──
        agent_errors: dict[str, int] = {}
        agent_calls: dict[str, int] = {}
        agent_ooc: dict[str, int] = {}
        agent_total_hits: dict[str, float] = {}
        total_samples = len(samples)
        for sample in samples:
            for call in sample.get("agent_calls", []):
                role = call.get("agent_role", "unknown")
                agent_calls[role] = agent_calls.get(role, 0) + int(call.get("calls", 0))
                agent_errors[role] = agent_errors.get(role, 0) + int(call.get("error_count", 0))
                agent_ooc[role] = agent_ooc.get(role, 0) + int(call.get("ooc_warnings", 0))
                agent_total_hits[role] = agent_total_hits.get(role, 0.0) + float(call.get("hit_score", 0.0))
        # 熔断率统计
        circuit_break_count = sum(1 for s in samples if s.get("circuit_break"))
        # 用户反馈统计
        feedback_count = sum(1 for s in samples if s.get("user_feedback_score") is not None)
        feedback_avg = (
            sum(s.get("user_feedback_score", 0.0) for s in samples if s.get("user_feedback_score") is not None)
            / max(feedback_count, 1)
        )
        new_rules: list[dict[str, Any]] = []
        # ── 模式A：某子Agent 高错误率 → 降权/关闭该子Agent ──
        for role, calls in agent_calls.items():
            if calls < self.min_sample_count:
                continue
            error_rate = agent_errors.get(role, 0) / calls
            ooc_rate = agent_ooc.get(role, 0) / calls
            if error_rate > 0.3 or ooc_rate > 0.2:
                confidence = round(min(0.99, 0.5 + error_rate * 0.5 + ooc_rate * 0.5), 3)
                rule = {
                    "rule_id": _gen_id("rule"),
                    "scope": "NOVEL_AGENT_SCHEDULE",
                    "condition": {"agent_role": role, "error_rate": {">": 0.3}},
                    "action": {
                        "agent_overrides": {role: {"enabled": False}},
                        "reason": f"子Agent {role} 历史错误率 {error_rate:.2f} 或 OOC 率 {ooc_rate:.2f} 过高，自动降权",
                    },
                    "confidence": confidence,
                    "status": "ACTIVE" if confidence >= self.confidence_threshold else "PENDING_REVIEW",
                }
                new_rules.append(rule)
        # ── 模式B：熔断率过高 → 降低算力模式（防过度消耗）──
        if total_samples >= self.min_sample_count and circuit_break_count / total_samples > 0.3:
            ratio = circuit_break_count / total_samples
            confidence = round(min(0.99, 0.4 + ratio * 0.6), 3)
            rule = {
                "rule_id": _gen_id("rule"),
                "scope": "NOVEL_AGENT_SCHEDULE",
                "condition": {"circuit_break_rate": {">": 0.3}},
                "action": {
                    "compute_mode": "rapid",
                    "max_rounds": 1,
                    "reason": f"熔断比例 {ratio:.2f} 过高，自动降为 rapid 模式并压缩轮次",
                },
                "confidence": confidence,
                "status": "ACTIVE" if confidence >= self.confidence_threshold else "PENDING_REVIEW",
            }
            new_rules.append(rule)
        # ── 模式C：用户反馈均值偏低 → 保留场景级调度优化（待人工审核）──
        if feedback_count >= self.min_sample_count and feedback_avg < 2.5:
            confidence = round(min(0.9, 0.3 + (5.0 - feedback_avg) * 0.12), 3)
            rule = {
                "rule_id": _gen_id("rule"),
                "scope": "NOVEL_AGENT_SCHEDULE",
                "condition": {"user_feedback_avg": {"<": 2.5}},
                "action": {
                    "max_rounds": 2,
                    "compute_mode": "think",
                    "reason": f"用户平均反馈 {feedback_avg:.2f} 偏低，提升算力与轮次以改善质量",
                },
                "confidence": confidence,
                "status": "PENDING_REVIEW",  # 涉及用户主观体验，一律人工审核
            }
            new_rules.append(rule)
        # 去重
        seen = set()
        unique = []
        for r in new_rules:
            key = json.dumps(r["condition"], sort_keys=True)
            if key not in seen:
                seen.add(key)
                unique.append(r)
        logger.info(
            "[AgentRuleExtractor] 样本 %d 条，产出 %d 条调度规则（ACTIVE %d / 待审核 %d）",
            len(samples),
            len(unique),
            sum(1 for r in unique if r["status"] == "ACTIVE"),
            sum(1 for r in unique if r["status"] == "PENDING_REVIEW"),
        )
        return unique
class NovelAgentSkillExtractor:
    """
    从高命中率任务样本中沉淀 NovelAgentSkill 创作技能卡片。
    """
    def __init__(self, min_sample_count: int = DEFAULT_MIN_SAMPLE_COUNT) -> None:
        self.min_sample_count = min_sample_count
    def extract_skills(self, samples: list[dict[str, Any]]) -> list[NovelAgentSkill]:
        """
        输入：审计样本列表
        输出：NovelAgentSkill 列表（高命中率样本沉淀）
        """
        if len(samples) < self.min_sample_count:
            logger.info(
                "[NovelAgentSkillExtractor] 样本数 %d < 最小要求 %d，跳过技能沉淀",
                len(samples), self.min_sample_count,
            )
            return []
        # 按题材聚合高命中样本
        genre_groups: dict[str, list[dict[str, Any]]] = {}
        for sample in samples:
            if sample.get("hit_score", 0.0) < 0.7:
                continue  # 只从高命中样本沉淀
            genre = sample.get("genre", "") or "通用"
            genre_groups.setdefault(genre, []).append(sample)
        skills: list[NovelAgentSkill] = []
        for genre, group in genre_groups.items():
            if len(group) < self.min_sample_count:
                continue
            # 统计该题材下各子Agent的表现
            role_hits: dict[str, list[float]] = {}
            role_ooc: dict[str, int] = {}
            role_calls: dict[str, int] = {}
            for sample in group:
                for call in sample.get("agent_calls", []):
                    role = call.get("agent_role", "unknown")
                    role_hits.setdefault(role, []).append(float(call.get("hit_score", 0.0)))
                    role_ooc[role] = role_ooc.get(role, 0) + int(call.get("ooc_warnings", 0))
                    role_calls[role] = role_calls.get(role, 0) + int(call.get("calls", 0))
            # 高命中子Agent → 加权开启；高OOC/错误子Agent → 降权关闭
            agent_overrides = {}
            for role, hits in role_hits.items():
                avg_hit = sum(hits) / max(len(hits), 1)
                if avg_hit >= 0.8 and role_ooc.get(role, 0) == 0:
                    agent_overrides[role] = {"enabled": True, "priority": "high"}
                elif role_ooc.get(role, 0) > 0:
                    agent_overrides[role] = {"enabled": False, "reason": "历史OOC过高"}
            skill = NovelAgentSkill(
                skill_id=_gen_id("skill"),
                type="PATTERN",
                name=f"{genre}题材_高命中创作组合",
                content={
                    "genre": genre,
                    "hit_samples": len(group),
                    "agent_config": agent_overrides,
                },
                agent_overrides=agent_overrides,
                applicability=f"适用于【{genre}】题材的多智能体小说创作",
                source_cards=[],
                rag_filter={"category": "worldview"} if genre else {},
                trigger_conditions={"genre": genre},
                effect_stats={
                    "apply_count": 0,
                    "hit_sum": sum(s.get("hit_score", 0.0) for s in group),
                    "avg_hit_score": round(
                        sum(s.get("hit_score", 0.0) for s in group) / len(group), 4
                    ),
                    "total_tokens": sum(s.get("total_tokens", 0) for s in group),
                    "ooc_sum": sum(s.get("ooc_warnings", 0) for s in group),
                    "circuit_break_count": sum(1 for s in group if s.get("circuit_break")),
                    "user_feedback_count": 0,
                    "user_feedback_sum": 0.0,
                    "effect_score": 0.0,
                },
                status="ACTIVE",
            )
            skills.append(skill)
        logger.info("[NovelAgentSkillExtractor] 产出 %d 项创作技能卡片", len(skills))
        return skills