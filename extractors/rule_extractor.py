"""
extractors/rule_extractor.py — 系统自进化规则与技能挖掘大脑
===========================================================
纯函数式无状态离线分析引擎。
专门读取 reflection_trigger 采集的 _raw.json 全局快照，
利用统计算法与特征工程产出高置信度的 OptimizationRule 与 UniversalSkill，
全程零侵入、不修改任何生产数据库。
"""
from __future__ import annotations
import json
import logging
from collections import defaultdict
from typing import Any
from pathlib import Path

import yaml

from models.reflection import OptimizationRule, UniversalSkill
from utils.resource_path import get_resource_path
from utils.llm_adapter import DeepSeekClient
logger = logging.getLogger(__name__)
class RuleExtractor:
    """
    系统异常与性能瓶颈规则挖掘器。
    通过统计批次1任务的重试率与失败率，反向推导防御性调度策略。
    """
    
    def __init__(self, confidence_threshold: float = 0.75):
        # 置信度阈值保护，防止过度拟合导致生产环境调度混乱
        self.confidence_threshold = confidence_threshold
    def extract_rules(self, snapshot_data: dict[str, Any]) -> list[OptimizationRule]:
        """执行挖掘，输出一系列达标的 OptimizationRule"""
        tasks = snapshot_data.get("datasets", {}).get("tasks", [])
        if not tasks:
            logger.info("[RuleExtractor] 未采集到有效任务样本，跳过分析。")
            return []
        logger.info("[RuleExtractor] 开始从 %d 条历史任务审计流中挖掘调度规律...", len(tasks))
        
        # 1. 指标聚合统计
        command_stats: dict[str, dict[str, Any]] = defaultdict(lambda: {"total": 0, "failed": 0, "total_retries": 0})
        for task in tasks:
            # 假定从 task_id 前缀或其他字段中提取出原始 command 类型
            # 若原始任务没有记录类型，我们尝试做简单归类，例如以 'learn_' 或 'quant_' 开头
            tid = task.get("task_id", "")
            if tid.startswith("learn_"):
                cmd_type = "document_learning"
            elif tid.startswith("skillext_"):
                cmd_type = "skill_extraction"
            else:
                cmd_type = "general_command"
            command_stats[cmd_type]["total"] += 1
            command_stats[cmd_type]["total_retries"] += int(task.get("retry_count", 0) or 0)
            if task.get("status") == "FAILED":
                command_stats[cmd_type]["failed"] += 1
        new_rules: list[OptimizationRule] = []
        
        # 2. 启发式挖掘与阈值触发
        for cmd, stats in command_stats.items():
            total = stats["total"]
            if total < 5: 
                continue # 样本量太少不足以支撑大数定律置信度
                
            fail_rate = stats["failed"] / total
            avg_retry = stats["total_retries"] / total
            
            # 模式A：长文本解析任务频繁失败或重试 -> 触发降维拆分规则
            if cmd == "document_learning" and (fail_rate > 0.3 or avg_retry > 1.5):
                confidence = min(0.99, (fail_rate * 0.5) + (avg_retry * 0.3))
                if confidence >= self.confidence_threshold:
                    rule = OptimizationRule(
                        scope="TASK_SPLIT",
                        condition={"command_type": cmd, "estimated_length": {">": 2000}},
                        action={"force_split_chunks": True, "chunk_size_limit": 1000},
                        confidence=round(confidence, 3)
                    )
                    new_rules.append(rule)
            
            # 模式B：频繁重试 -> 模型可能过载或变蠢，强制降级或切换调度模式
            if avg_retry >= 2.0:
                confidence = min(0.95, avg_retry / 4.0)
                if confidence >= self.confidence_threshold:
                    rule = OptimizationRule(
                        scope="MODEL_DISPATCH",
                        condition={"command_type": cmd, "retry_count": {">": 1}},
                        action={"override_compute_mode": "rapid", "timeout_extension": 1.5},
                        confidence=round(confidence, 3)
                    )
                    new_rules.append(rule)
        # 3. 规则去重机制 (根据 condition 特征指纹)
        unique_rules = {json.dumps(r.condition, sort_keys=True): r for r in new_rules}.values()
        
        logger.info("[RuleExtractor] 挖掘完毕，共产出 %d 条高置信度自适应规则。", len(unique_rules))
        return list(unique_rules)
class SkillExtractor:
    """
    通用卡片与文学风格模板挖掘器。
    从批次2沉淀的高分知识库、批次3提取的项目画像中，收敛出普适性最强的技能 Prompt。
    """
    
    async def extract_skills(self, snapshot_data: dict[str, Any]) -> list[UniversalSkill]:
        """执行知识升华，返回可直接注册到知识库中的通用技能库（异步，避免事件循环内 new_event_loop）。"""
        cards = snapshot_data.get("datasets", {}).get("cards", [])
        parses = snapshot_data.get("datasets", {}).get("project_parses", [])
        
        logger.info("[SkillExtractor] 开始从 %d 张高频卡片与 %d 份项目反思中凝聚通用技能...", 
                    len(cards), len(parses))
                    
        new_skills: list[UniversalSkill] = []
        
        # 1. 基于高权卡片挖掘 TEMPLATE (模板类)
        # 例如找出所有标签高度一致、结构高度标准化的内容
        tag_clusters = defaultdict(list)
        for card in cards:
            tags_str = card.get("tags", "[]")
            try:
                tags = json.loads(tags_str) if isinstance(tags_str, str) else (tags_str or [])
            except Exception:
                tags = []
            
            for t in tags:
                tag_clusters[t].append(card["card_id"])
                
        # 过滤出含有聚集效应的标签（假定>3张卡片具备同一特定高级标签，可升华为特定流派模板）
        for tag, ids in tag_clusters.items():
            if len(ids) >= 3 and "skill_derived_" not in tag:
                skill = UniversalSkill(
                    type="TEMPLATE",
                    name=f"高频特征收敛: {tag}",
                    content={"required_attributes": ["核心冲突", "发展弧线"], "focus_tag": tag},
                    source_cards=ids[:10], # 记录数据血缘
                    applicability=f"适用于包含【{tag}】元素的角色生成或大纲延展场景"
                )
                new_skills.append(skill)
                
        # 2. 基于项目解析挖掘 STYLE (叙事风格特征)
        # 从 AIParseResult 的 style_analysis 字段中提取共性
        genre_clusters = defaultdict(list)
        for doc_parse in parses:
            content = doc_parse.get("parsed_content", {})
            genre = content.get("genre_inferred", "未知")
            style = content.get("style_analysis", "")
            if genre != "未知" and style:
                genre_clusters[genre].append(style)
                
        for genre, styles in genre_clusters.items():
            if len(styles) >= 2:
                # 在真实业务中，此处应调用一次 LLM 对 styles 数组进行归纳总结
                # 当前作为离线沙盒，采用拼接截断策略模拟提炼
                summary_style = " | ".join(styles)[:200]
                
                # [增量接入 DeepSeek]
                try:
                    config_path = get_resource_path("config/llm_provider.yaml")
                    if config_path.exists():
                        with open(config_path, "r", encoding="utf-8") as f:
                            ds_config = yaml.safe_load(f).get("deepseek", {})
                        if ds_config.get("enable_switch") and ds_config.get("api_key"):
                            client = DeepSeekClient(
                                api_key=ds_config.get("api_key"),
                                api_base=ds_config.get("api_base", "https://api.deepseek.com/v1"),
                                model_name=ds_config.get("model_name", "deepseek-r1")
                            )
                            prompt = f"请归纳以下同一题材小说的共同叙事风格，提取核心共性特征，字数限制在200字以内：\n{styles}"
                            
                            # 直接 await（extract_skills 已改为 async，不再 new_event_loop）
                            llm_res = await client.generate_completion(prompt)
                            if llm_res:
                                summary_style = llm_res
                except Exception as e:
                    logger.warning("DeepSeek API 风格归纳提炼失败，降级回原有拼接逻辑: %s", e)
                
                skill = UniversalSkill(
                    type="STYLE",
                    name=f"{genre}流派_黄金叙事风格",
                    content={"genre": genre, "style_directives": summary_style},
                    source_cards=[],
                    applicability=f"当创作题材被指定为【{genre}】时强制挂载该风格参数限制大模型语调"
                )
                new_skills.append(skill)
                
        # 3. 数据校验与去重
        unique_skills = {s.name: s for s in new_skills}.values()
        
        logger.info("[SkillExtractor] 挖掘完毕，共结晶出 %d 项通用创作技能。", len(unique_skills))
        return list(unique_skills)