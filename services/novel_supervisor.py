"""
services/novel_supervisor.py — 多智能体小说创作调度器 (第十部分)
=============================================================
NovelSupervisor 总监督管多智能体分散思维创作流程：
1. 任务初始化阶段：
   - 读取批次4规则库，匹配上下文加载 scope='NOVEL_AGENT_SCHEDULE' 的规则
     → 动态控制子Agent开关、轮次上限、算力参数
   - 根据当前项目、题材、场景条件，调用 CardIndexer 检索匹配的 NovelAgentSkill
     → 合并技能配置覆盖子Agent参数、模板ID、RAG检索过滤
2. 执行阶段：
   - 按规则/技能组合出的 DAG 执行多智能体创作
3. 收尾阶段：
   - 生成 NovelAgentExecutionAudit，追加写入批次1审计报告链路
   - 连同本次使用的 rule_id / skill_id 一并记录，供后续效果统计
设计约束（纯叠加式）：
- 不修改原始批次4核心逻辑
- 不修改内置模板与子Agent底层代码，全部为叠加式覆盖
- 受 feature.novel_multi_agent_enable 总开关控制
"""
from __future__ import annotations
import asyncio
import json
import logging
import time
import uuid
from typing import Any
from core.config_manager import config_manager
from models.novel_agent import NovelAgentExecutionAudit, NovelAgentSkill, SubAgentCallRecord
from models.orchestrator_schemas import SubAgentRole
from services.indexer import CardIndexer
from services.optimization_applier import OptimizationApplier
logger = logging.getLogger(__name__)
# 默认子Agent配置（内置基线，不修改旧代码，仅作为叠加的基准）
_DEFAULT_AGENT_ROLES: dict[str, dict[str, Any]] = {
    "lore_expert": {"enabled": True, "model": "worker", "max_rounds": 1, "template_id": "expert_lore"},
    "combat_expert": {"enabled": True, "model": "worker", "max_rounds": 1, "template_id": "expert_combat"},
    "emotion_expert": {"enabled": True, "model": "worker", "max_rounds": 1, "template_id": "expert_emotion"},
    "event_expert": {"enabled": True, "model": "worker", "max_rounds": 1, "template_id": "expert_event"},
}
# 允许动态调整的算力模式
_COMPUTE_MODES = ("rapid", "think", "complex", "deep")
class NovelSupervisor:
    """多智能体小说创作总监督管。"""
    def __init__(
        self,
        indexer: CardIndexer,
        optimization_applier: OptimizationApplier,
        dispatcher=None,         # ModelDispatcher（可选）
        divergent_engine=None,   # DivergentEngine（可选）
    ) -> None:
        self.indexer = indexer
        self.optimization_applier = optimization_applier
        self.dispatcher = dispatcher
        self.divergent_engine = divergent_engine
        self._batch1_task_manager = None  # 由 bootstrap 注入，用于生成批次1审计报告
        self._skill_store = None  # 由 bootstrap 注入，用于技能应用效果统计
        # 本次任务使用的 rule_id / skill_id（任务级暂存，收尾写入审计）
        self._active_rule_ids: list[str] = []
        self._active_skill_ids: list[str] = []
        self._audit_store = None  # 由 bootstrap 注入，用于异步落库审计
    # ============================================================
    # 多智能体总开关
    # ============================================================
    @staticmethod
    def is_enabled() -> bool:
        """feature.novel_multi_agent_enable 总开关。"""
        return config_manager.get_bool("feature.novel_multi_agent_enable", False)
    @staticmethod
    def is_self_reflect_learn_enabled() -> bool:
        """novel_agent.enable_self_reflect_learn 子开关（自学习闭环）。"""
        return config_manager.get_bool("novel_agent.enable_self_reflect_learn", False)
    # ============================================================
    # 1. 任务初始化：加载规则 + 检索技能
    # ============================================================
    async def _load_schedule_rules(
        self, project_id: str, genre: str, scenario: str
    ) -> list[dict[str, Any]]:
        """加载 NOVEL_AGENT_SCHEDULE 作用域规则，动态控制子Agent启停/算力/权重。"""
        if not self.optimization_applier:
            return []
        try:
            rules = await self.optimization_applier.fetch_active_rules(
                scope="NOVEL_AGENT_SCHEDULE",
                context_features={
                    "project_id": project_id or "",
                    "genre": genre or "",
                    "scenario": scenario or "",
                },
            )
            matched = []
            for rule in rules:
                matched.append(rule)
                if rule.rule_id not in self._active_rule_ids:
                    self._active_rule_ids.append(rule.rule_id)
            return matched
        except Exception as exc:
            logger.warning("[NovelSupervisor] 加载调度规则失败（不阻断创作）: %s", exc)
            return []
    async def _search_matching_skills(
        self, project_id: str, genre: str, scenario: str
    ) -> list[NovelAgentSkill]:
        """
        调用 CardIndexer 检索匹配的 NovelAgentSkill。
        - 全部以 InfoCard (card_sub_type='novel_agent_skill') 形式保存在卡片库中
        - 通过 payload 反序列化为 NovelAgentSkill
        """
        if not self.indexer:
            return []
        # 注意：子开关 enable_self_reflect_learn 只控制"生成新规则/技能"，
        # 不控制"应用已沉淀技能"——关闭自学习时多智能体照常运行，技能加载不受影响。
        try:
            cards = await self.indexer.search_cards(
                card_type="info",
                subtype="novel_agent_skill",
                limit=30,
            )
            skills = []
            for card_row in cards:
                detail = await self.indexer.get_card_detail(card_row["card_id"])
                if not detail:
                    continue
                payload = detail.get("payload", {})
                try:
                    skill = NovelAgentSkill.model_validate(payload)
                except Exception:
                    continue
                # 状态过滤：仅 ACTIVE 技能可被加载
                if skill.status != "ACTIVE":
                    continue
                # 触发条件匹配
                trig = skill.trigger_conditions or {}
                if trig.get("genre") and genre and trig.get("genre") != genre:
                    continue
                if trig.get("scenario") and scenario and trig.get("scenario") != scenario:
                    continue
                skills.append(skill)
                if skill.skill_id not in self._active_skill_ids:
                    self._active_skill_ids.append(skill.skill_id)
            return skills
        except Exception as exc:
            logger.warning("[NovelSupervisor] 检索技能卡片失败（不阻断创作）: %s", exc)
            return []
    # ============================================================
    # 2. 构建执行配置（规则 + 技能叠加覆盖）
    # ============================================================
    def _build_agent_config(
        self,
        rules: list[dict[str, Any]],
        skills: list[NovelAgentSkill],
    ) -> dict[str, dict[str, Any]]:
        """
        合并规则与技能，输出最终的子Agent配置：
        规则 action 形如:
        {
          "agent_overrides": {"combat_expert": {"enabled": false}},
          "compute_mode": "rapid",
          "max_rounds": 2
        }
        技能 agent_overrides 作为叠加覆盖（技能优先级高于规则）。
        """
        config: dict[str, dict[str, Any]] = json.loads(json.dumps(_DEFAULT_AGENT_ROLES))
        # 规则覆盖
        for rule in rules:
            action = getattr(rule, "action", None) or {}
            agent_overrides = action.get("agent_overrides", {}) or {}
            for role, over in agent_overrides.items():
                if role in config:
                    base = dict(config[role])
                    base.update(over or {})
                    # 覆盖模板
                    if "template_id" in (over or {}):
                        base["template_id"] = over["template_id"]
                    config[role] = base
            if action.get("compute_mode") in _COMPUTE_MODES:
                config["_compute_mode"] = action["compute_mode"]
            if action.get("max_rounds"):
                config["_max_rounds"] = int(action["max_rounds"])
        # 技能覆盖（优先级更高）
        for skill in skills:
            for role, over in (skill.agent_overrides or {}).items():
                if role in config:
                    base = dict(config[role])
                    base.update(over or {})
                    if "template_id" in (over or {}):
                        base["template_id"] = over["template_id"]
                    config[role] = base
            # 模板覆盖（叠加式：不修改内置模板文件，仅任务内生效）
            if skill.template_overrides:
                config["_template_overrides"] = dict(
                    config.get("_template_overrides", {})
                )
                config["_template_overrides"].update(skill.template_overrides)
            # 计算模式覆盖
            if skill.content and skill.content.get("compute_mode") in _COMPUTE_MODES:
                config["_compute_mode"] = skill.content["compute_mode"]
        return config
    def _build_rag_filters(self, skills: list[NovelAgentSkill]) -> list[dict[str, Any]]:
        """收集技能中的 RAG 过滤条件（叠加式）。"""
        return [s.rag_filter for s in skills if s.rag_filter]

    @staticmethod
    def _merge_rag_filters(rag_filters: list[dict[str, Any]]) -> dict[str, Any] | None:
        """阶段1：合并多个技能的 rag_filter 为单一生效集（后者覆盖同名字段）。

        合并结果经 dispatcher.card_filters 下推 WorldContextBuilder，
        实现技能对卡片检索范围的定向（此前构建后从未消费）。
        """
        merged: dict[str, Any] = {}
        for flt in rag_filters or []:
            if isinstance(flt, dict):
                merged.update(flt)
        return merged or None

    # 角色 -> 创作模板ID / 内置人设兜底（模板缺失时）
    _ROLE_CREATION_META: dict[str, tuple[str, str]] = {
        "lore_expert": ("novel_role_lore", "设定专家（世界观、时代质感、制度地理器物）"),
        "combat_expert": ("novel_role_combat", "动作与冲突专家（战斗场面、冲突张力编排）"),
        "emotion_expert": ("novel_role_emotion", "情感与人物专家（情感、对话交锋、心理刻画）"),
        "event_expert": ("novel_role_event", "情节与结构专家（事件推进、因果链、节奏）"),
    }

    def _render_role_creation_prompt(
        self,
        role_key: str,
        cfg: dict[str, Any],
        template_overrides: dict[str, Any],
        content: str,
    ) -> str:
        """阶段1：为子智能体渲染创作导向的专职 prompt。

        优先级链：技能模板覆盖（现有行为保留）→ 内置创作角色模板
        （novel_role_*，data/prompts/builtin）→ 内置人设兜底 → 原始指令兜底。
        注意：cfg.template_id 默认指向 expert_* 抽取模板（为卡片抽取设计），
        创作场景改用 novel_role_* 模板，不复用抽取模板。
        """
        template_id = cfg.get("template_id", f"expert_{role_key}")
        if template_overrides.get(template_id):
            try:
                return template_overrides[template_id].format(content=content)
            except Exception as exc:
                logger.warning(
                    "[NovelSupervisor] 技能模板 %s 渲染失败，回退角色模板: %s",
                    template_id, exc,
                )
        meta = self._ROLE_CREATION_META.get(role_key)
        if meta:
            creation_template_id, persona = meta
            try:
                from services.prompt_template_manager import prompt_manager

                return prompt_manager.render_or_fallback(
                    creation_template_id,
                    {"content": content},
                    lambda p=persona, c=content: (
                        f"你是长篇小说创作团队中的{p}。"
                        "依据任务撰写正文章节，直接输出小说正文，"
                        "不要输出任何分析、标题编号或 JSON。\n\n任务与素材：\n" + c
                    ),
                )
            except Exception as exc:
                logger.warning(
                    "[NovelSupervisor] 创作模板 %s 渲染异常，回退原始指令: %s",
                    creation_template_id, exc,
                )
        return content
    # ============================================================
    # 3. 审计采集
    # ============================================================
    @staticmethod
    def _estimate_tokens(text: str) -> int:
        """简易 token 估算（约 3.5 字符/token）。"""
        return max(0, int(len(text or "") // 3.5))
    def build_audit(
        self,
        task_id: str,
        project_id: str,
        genre: str,
        scenario: str,
        agent_stats: list[dict[str, Any]],
        total_tokens: int,
        total_duration_ms: int,
        hit_score: float,
        ooc_warnings: int,
        circuit_break: bool = False,
        circuit_break_reason: str = "",
    ) -> NovelAgentExecutionAudit:
        """组装一条完整审计记录。"""
        calls = []
        for stat in agent_stats:
            calls.append(
                SubAgentCallRecord(
                    agent_role=stat.get("role", "unknown"),
                    is_active=bool(stat.get("is_active", True)),
                    model_assigned=stat.get("model_assigned", ""),
                    calls=int(stat.get("calls", 0)),
                    total_tokens=int(stat.get("total_tokens", 0)),
                    duration_ms=int(stat.get("duration_ms", 0)),
                    hit_score=float(stat.get("hit_score", 0.0)),
                    ooc_warnings=int(stat.get("ooc_warnings", 0)),
                    error_count=int(stat.get("error_count", 0)),
                )
            )
        return NovelAgentExecutionAudit(
            task_id=task_id,
            project_id=project_id,
            genre=genre,
            scenario=scenario,
            agent_calls=calls,
            total_tokens=total_tokens,
            total_duration_ms=total_duration_ms,
            hit_score=round(hit_score, 4),
            ooc_warnings=ooc_warnings,
            circuit_break=circuit_break,
            circuit_break_reason=circuit_break_reason,
            used_rule_ids=list(self._active_rule_ids),
            used_skill_ids=list(self._active_skill_ids),
        )
    async def persist_audit(
        self, audit: NovelAgentExecutionAudit, task_audit_report: Any = None
    ) -> str:
        """
        审计落库：
        1. 追加写入批次1审计报告（生成组合报告 JSON 文件）
        2. 持久化 audit 记录（经审计仓库）
        返回 audit_id。
        """
        audit_id = audit.audit_id
        # 1) 追加到批次1 审计报告文件
        try:
            from core.path_resolver import get_app_data_dir
            report_dir = get_app_data_dir() / "novel_agent_audits"
            report_dir.mkdir(parents=True, exist_ok=True)
            report_file = report_dir / f"{task_audit_report.task_id if task_audit_report else audit.task_id}_combined.json"
            if report_file.exists():
                try:
                    existing = json.loads(report_file.read_text(encoding="utf-8"))
                except Exception:
                    existing = {"task_id": audit.task_id, "audits": []}
            else:
                existing = {"task_id": audit.task_id, "audits": []}
            existing.setdefault("audits", []).append(audit.model_dump(mode="json"))
            if task_audit_report:
                existing["batch1_audit_report"] = (
                    task_audit_report.model_dump(mode="json")
                    if hasattr(task_audit_report, "model_dump")
                    else task_audit_report
                )
            report_file.write_text(
                json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8"
            )
            logger.info("[NovelSupervisor] 审计已追加至文件: %s", report_file)
        except Exception as exc:
            logger.warning("[NovelSupervisor] 追加审计报告失败（不阻断）: %s", exc)
        # 2) 持久化 audit 记录
        if self._audit_store is not None:
            try:
                await self._audit_store.save_audit(audit)
            except Exception as exc:
                logger.warning("[NovelSupervisor] 审计仓库落库失败（不阻断）: %s", exc)
        return audit_id
    # ============================================================
    # 4. 执行主流程
    # ============================================================
    async def execute_creation(
        self,
        task_id: str,
        cmd_text: str,
        project_id: str = "",
        genre: str = "",
        scenario: str = "",
    ) -> dict[str, Any]:
        """
        多智能体小说创作主入口。
        返回 {success, result, audit_id, used_rule_ids, used_skill_ids}
        """
        start_ms = int(time.time() * 1000)
        self._active_rule_ids = []
        self._active_skill_ids = []
        ooc_warnings = 0
        agent_stats: list[dict[str, Any]] = []
        try:
            # 1. 加载调度规则 + 检索匹配技能
            rules = await self._load_schedule_rules(project_id, genre, scenario)
            skills = await self._search_matching_skills(project_id, genre, scenario)
            agent_config = self._build_agent_config(rules, skills)
            rag_filters = self._build_rag_filters(skills)
            compute_mode = agent_config.get("_compute_mode", "think")
            max_rounds = int(agent_config.get("_max_rounds", 1))
            template_overrides = agent_config.get("_template_overrides", {}) or {}
            logger.info(
                "[NovelSupervisor] 任务 %s 初始化：规则 %d 条、技能 %d 项、算力=%s、轮次上限=%d",
                task_id, len(rules), len(skills), compute_mode, max_rounds,
            )
            # 2. 执行各子 Agent（基于规则/技能叠加后的配置）
            # 阶段1：串行精修——后续专家在前序产出草稿上完善，产出汇合为最终正文
            #（此前子代理输出被整体丢弃，仅保留计数与汇总字符串）
            total_tokens = 0
            accumulated_draft: str | None = None
            merged_rag_filters = self._merge_rag_filters(rag_filters)
            # P5(情感标本):确定性情绪推断→标本少样本块(项目级优先,硬顶2枚);
            # 同时作为 TriggerContext 情绪维供打磨插件触发。失败静默降级。
            emotion_state = None
            specimen_block = ""
            if self.indexer is not None:
                try:
                    from services.emotion_specimens import (
                        emotion_context_block,
                        infer_emotion_state,
                    )

                    emotion_state = infer_emotion_state(cmd_text)
                    if emotion_state:
                        specimen_block = await emotion_context_block(
                            self.indexer, emotion_state, project_id=project_id
                        )
                except Exception as exc:
                    logger.warning("[NovelSupervisor] 情绪标本上下文构建失败(跳过): %s", exc)
            for role_key, cfg in agent_config.items():
                if role_key.startswith("_"):
                    continue
                if not cfg.get("enabled", True):
                    agent_stats.append({
                        "role": role_key, "is_active": False, "model_assigned": "none",
                        "calls": 0, "total_tokens": 0, "duration_ms": 0,
                        "hit_score": 0.0, "ooc_warnings": 0, "error_count": 0,
                    })
                    continue
                role_start = int(time.time() * 1000)
                role_calls = 0
                role_tokens = 0
                role_hit_sum = 0.0
                role_errors = 0
                role_ooc = 0
                try:
                    # 阶段1：创作内容构造——首轮为原始指令，后续轮携带前序草稿做精修
                    if accumulated_draft:
                        content = (
                            f"{cmd_text}\n\n"
                            "【当前草稿（前序专家产出）】\n"
                            f"{accumulated_draft}\n\n"
                            "请在保留草稿优点的基础上，以你的专职视角完善重写，输出完整正文。"
                        )
                    else:
                        # P5:首轮携带情绪标本少样本块(学手法不学词句)
                        content = (specimen_block + cmd_text) if specimen_block else cmd_text
                    # 阶段1：渲染创作导向角色 prompt（技能覆盖→novel_role_*→兜底）
                    prompt = self._render_role_creation_prompt(
                        role_key, cfg, template_overrides, content
                    )
                    for r in range(max(1, max_rounds)):
                        if self.dispatcher is not None:
                            result = await self.dispatcher.dispatch(
                                prompt,
                                project_id=project_id or None,
                                override_mode=compute_mode if compute_mode in ("rapid", "think") else None,
                                card_filters=merged_rag_filters,
                            )
                        else:
                            # 无调度器时的本地兜底（模拟/直通模式）
                            result = cmd_text
                        role_calls += 1
                        role_tokens += self._estimate_tokens(result or "")
                        if result and str(result).strip():
                            accumulated_draft = str(result)
                        # 简易命中评估：包含指令关键词视为命中
                        base_hit = 0.9 if (result and len(result) > 50) else 0.5
                        role_hit_sum += base_hit
                        # 简易 OOC 探测：检测到明显越权/角色漂移标记
                        if any(kw in (result or "") for kw in ("【越权】", "[OOC]", "ooc")):
                            role_ooc += 1
                            ooc_warnings += 1
                except Exception as exc:
                    role_errors += 1
                    logger.warning("[NovelSupervisor] 子Agent %s 执行异常: %s", role_key, exc)
                role_duration = int(time.time() * 1000) - role_start
                total_tokens += role_tokens
                agent_stats.append({
                    "role": role_key,
                    "is_active": True,
                    "model_assigned": cfg.get("model", ""),
                    "calls": role_calls,
                    "total_tokens": role_tokens,
                    "duration_ms": role_duration,
                    "hit_score": round(role_hit_sum / max(role_calls, 1), 4),
                    "ooc_warnings": role_ooc,
                    "error_count": role_errors,
                })
            # P1(专精能手施工图):行为插件编辑通行——精修链末端打磨。
            # 验收不过自动回退通行前文本(保底不伤稿);开关默认开,异常不阻断创作。
            polish_records: list = []
            if accumulated_draft and self.dispatcher is not None:
                if config_manager.get_bool("feature.behavior_plugins_enable", True):
                    try:
                        from models.behavior_plugin import TriggerContext
                        from services.behavior_plugins import apply_polish_passes

                        accumulated_draft, polish_records = await apply_polish_passes(
                            accumulated_draft, self.dispatcher,
                            TriggerContext(task_id=task_id, emotion_state=emotion_state),
                        )
                        # P2:运行落账(效果统计原料,失败不阻断)
                        from services.behavior_plugins import persist_run_records
                        persist_run_records(polish_records)
                    except Exception as exc:
                        logger.warning("[NovelSupervisor] 打磨链异常(跳过,不阻断): %s", exc)

            # 3. 汇总结果（叠加发散引擎可选）
            total_duration_ms = int(time.time() * 1000) - start_ms
            total_calls = sum(s.get("calls", 0) for s in agent_stats)
            hit_score = sum(s.get("hit_score", 0.0) for s in agent_stats if s.get("is_active"))
            active_count = sum(1 for s in agent_stats if s.get("is_active"))
            hit_score = hit_score / max(active_count, 1)
            circuit_break = False
            circuit_break_reason = ""
            if total_calls == 0 or active_count == 0:
                circuit_break = True
                circuit_break_reason = "所有子Agent均被规则关闭或无可用执行通道"
            # 4. 生成审计
            audit = self.build_audit(
                task_id=task_id,
                project_id=project_id,
                genre=genre,
                scenario=scenario,
                agent_stats=agent_stats,
                total_tokens=total_tokens,
                total_duration_ms=total_duration_ms,
                hit_score=hit_score,
                ooc_warnings=ooc_warnings,
                circuit_break=circuit_break,
                circuit_break_reason=circuit_break_reason,
            )
            # 5. 联动批次1审计报告 + 落库
            report = None
            if self._batch1_task_manager is not None:
                try:
                    report = await self._batch1_task_manager.generate_task_audit_report(task_id)
                except Exception:
                    report = None
            audit_id = await self.persist_audit(audit, task_audit_report=report)
            # 6. 技能应用效果统计（记录本次技能生效样本）
            if self._skill_store is not None:
                audit_row = audit.model_dump(mode="json")
                for skill_id in self._active_skill_ids:
                    try:
                        await self._skill_store.record_skill_apply(skill_id, audit_row)
                    except Exception as exc:
                        logger.warning("[NovelSupervisor] 技能 %s 效果统计失败: %s", skill_id, exc)
            # 阶段1：返回汇合后的真实正文（串行精修的最终草稿），
            # 汇总信息降级为元数据（此前正文位只是一句汇总字符串）
            summary = (
                f"[NovelSupervisor] 多智能体创作完成 "
                f"(task={task_id}, agents={active_count}, tokens={total_tokens})"
            )
            final_text = (accumulated_draft or "").strip() or summary
            return {
                "success": True,
                "result": final_text,
                "summary": summary,
                "audit_id": audit_id,
                "used_rule_ids": list(self._active_rule_ids),
                "used_skill_ids": list(self._active_skill_ids),
                "circuit_break": circuit_break,
                "polish": [
                    {"plugin_id": r.plugin_id, "accepted": r.accepted, "reason": r.reason}
                    for r in polish_records
                ],
            }
        except Exception as exc:
            logger.exception("[NovelSupervisor] 创作执行异常")
            return {
                "success": False,
                "result": "",
                "audit_id": "",
                "used_rule_ids": list(self._active_rule_ids),
                "used_skill_ids": list(self._active_skill_ids),
                "error": str(exc),
            }
# 全局单例（由 bootstrap 装配）
novel_supervisor: NovelSupervisor | None = None