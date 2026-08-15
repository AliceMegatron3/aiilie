from typing import Any
import json
import logging
from models.orchestrator_schemas import SubAgentRole
from utils.prompt_adapter import PromptAdapter

logger = logging.getLogger(__name__)

class ExpertAgents:
    """
    专职子智能体库（Sub-Agent Registry）。
    各个专员负责从原文中提取属于自己负责领域的书库卡片数据，绝不越权。
    """
    
    @staticmethod
    def get_expert_prompt(role: SubAgentRole, content: str) -> str:
        """根据不同的专员角色，生成其专职的提取指令。

        架构整改 1.3：各角色 system prompt 已迁移 PromptTemplateManager；
        模板缺失/渲染失败时回退旧基线硬编码（services/legacy_prompts.py）。
        """
        from services.prompt_template_manager import prompt_manager
        from services import legacy_prompts

        _MAP: dict[SubAgentRole, tuple[str, Any]] = {
            SubAgentRole.LORE_EXPERT: (
                "expert_lore", legacy_prompts.expert_lore,
            ),
            SubAgentRole.COMBAT_EXPERT: (
                "expert_combat", legacy_prompts.expert_combat,
            ),
            SubAgentRole.EMOTION_EXPERT: (
                "expert_emotion", legacy_prompts.expert_emotion,
            ),
            SubAgentRole.EVENT_EXPERT: (
                "expert_event", legacy_prompts.expert_event,
            ),
        }
        mapped = _MAP.get(role)
        if mapped:
            template_id, fallback = mapped
            return prompt_manager.render_or_fallback(
                template_id,
                {"content": content},
                lambda f=fallback, c=content: f(c),
            )

        return "请分析原文内容。\n" + content

    @staticmethod
    def format_task_with_rules(role: SubAgentRole, model_name: str, content: str, rules: str) -> str:
        """调用底层的自适应强锁，把专员任务和历史经验包装起来"""
        raw_task = ExpertAgents.get_expert_prompt(role, content)
        # 将专属任务和历史规则传入大锁
        return PromptAdapter.wrap_with_experience_lock(model_name, raw_task, rules)

expert_agents = ExpertAgents()
