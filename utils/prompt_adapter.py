import json

from services.prompt_template_manager import prompt_manager
from services import legacy_prompts


class PromptAdapter:
    """
    模型指令自适应拦截与重构模块。
    针对不同模型的"脾气"，将经验强锁结构 (JSON Checklist) 动态注入到 Prompt 中。

    架构整改 1.3：三个模型谱系的 system prompt 已迁移 PromptTemplateManager；
    模板缺失/渲染失败时回退旧基线硬编码（services/legacy_prompts.py）。
    """
    
    @staticmethod
    def wrap_with_experience_lock(model_name: str, original_task: str, experience_rules: str) -> str:
        """
        包装原始任务，强迫模型在分析前默写并校验历史经验。
        """
        # 基础 JSON 思维链强锁结构
        json_schema = {
            "rule_checklist": [
                "规则1内容 -> 本次任务中是否涉及 -> 是/否，打算怎么做"
            ],
            "creative_analysis": "在这里进行自由发散的文本分析...",
            "final_data": "..."
        }
        schema_str = json.dumps(json_schema, ensure_ascii=False, indent=2)
        
        # 判断模型谱系
        model_lower = model_name.lower()
        variables = {
            "experience_rules": experience_rules,
            "original_task": original_task,
            "schema_str": schema_str,
        }
        
        if "deepseek" in model_lower and "r1" in model_lower:
            # DeepSeek R1 (推理模型) 特化：强调不要让小说剧情干扰规则
            return prompt_manager.render_or_fallback(
                "prompt_adapter_deepseek_r1",
                variables,
                lambda: legacy_prompts.prompt_adapter_deepseek_r1(
                    original_task, experience_rules
                ),
            )
        elif "qwen" in model_lower:
            # Qwen 系列特化：对结构化要求高，更喜欢直接的 Markdown
            return prompt_manager.render_or_fallback(
                "prompt_adapter_qwen",
                variables,
                lambda: legacy_prompts.prompt_adapter_qwen(
                    original_task, experience_rules
                ),
            )
        else:
            # 通用回退方案
            return prompt_manager.render_or_fallback(
                "prompt_adapter_default",
                variables,
                lambda: legacy_prompts.prompt_adapter_default(
                    original_task, experience_rules
                ),
            )
