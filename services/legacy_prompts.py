"""
services/legacy_prompts.py — 硬编码 Prompt 兜底集中区（架构整改 1.3）
====================================================================
架构整改 1.3 的兼容层：全部业务 system prompt 已迁移至
PromptTemplateManager（data/prompts/builtin/ 下的 JSON 模板）。

本模块仅保留**模板缺失/渲染失败时的兜底文本**（与原硬编码逐字一致），
保证打包环境模板文件意外缺失时业务行为完全回退旧基线。
审计白名单：core/prompt_audit.py 对本模块豁免（此处硬编码属预期）。
"""
from __future__ import annotations

import json


# ── 批次3 文档学习引擎 ─────────────────────────────────────────
def learning_doc_analyze(text_segment: str) -> str:
    return (
        "你是一个专业的文学内容分析AI。请深度分析以下文档段落的内容，并提取出核心属性。\n"
        "【输出格式限制】:\n"
        "你必须且只能输出合法的 JSON 格式。请不要输出任何额外的文本、Markdown 标记或代码块语法(如 ```json)。\n"
        "【目标结构】:\n"
        "{\n"
        '  "genre_inferred": "推断的题材(如: 现代言情、科幻修真等)",\n'
        '  "style_analysis": "叙事风格的深度解析(100字左右)",\n'
        '  "key_characters": [\n'
        '      {"name": "角色名", "desc": "角色简介及核心特征"}\n'
        "  ],\n"
        '  "core_demands": ["提取出的核心创作需求点1", "需求点2"],\n'
        '  "index_keywords": ["高亮关键词1", "关键词2"]\n'
        "}\n"
        "【待分析文本】:\n"
        f"{text_segment}\n"
    )


# ── 批次2 书籍提取：四位专职智能体 ─────────────────────────────
def expert_lore(content: str) -> str:
    return (
        "【设定纪检委】\n"
        "你的唯一任务是提取新的世界观设定、地点、派系、法宝、境界等静态信息（Info Cards）。\n"
        "不要提取任何对话或情感起伏。\n"
        "输出示例：\n"
        '{"final_data": {"地点": "灵台山", "法宝": "天帝圣剑"}}\n\n'
        "原文：\n" + content
    )


def expert_combat(content: str) -> str:
    return (
        "【战力评估师】\n"
        "你的唯一任务是分析文中的战斗场面、双方实力对比、招式使用以及胜负关系。\n"
        "如果没有发生任何战斗，请在 JSON 的 final_data 中返回空字典 {}\n"
        "输出示例：\n"
        '{"final_data": {"参与者": ["王腾", "叶凡"], "胜负关系": "王腾胜出", "招式使用": {"王腾": "天帝碎灭斩"}}}\n\n'
        "原文：\n" + content
    )


def expert_emotion(content: str) -> str:
    return (
        "【人际情感导师】\n"
        "你的唯一任务是提取人物之间的对话交锋、好感度升降、仇恨拉扯等情感起伏。\n"
        "忽略所有纯风景描写或世界观设定。\n"
        "输出示例：\n"
        '{"final_data": {"姬紫月": {"情感": "悲伤", "描述": "看到战斗心如刀绞，流下眼泪"}}}\n\n'
        "原文：\n" + content
    )


def expert_event(content: str) -> str:
    return (
        "【事件简报专员】\n"
        "你的唯一任务是用上帝视角，以不超过50个字的短陈述，一句话总结本段落发生的核心事件（粗颗粒度大纲）。\n"
        "忽略战斗招式细节或长篇大论的设定描述，只保留主线。\n"
        "输出示例：\n"
        '{"final_data": {"简报": "王腾在灵台山施展绝招重创叶凡，姬紫月在旁悲伤旁观。"}}\n\n'
        "原文：\n" + content
    )


# ── 批次4 反思优化链路：经验强锁包装（三模型谱系） ─────────────
def _schema_str() -> str:
    json_schema = {
        "rule_checklist": [
            "规则1内容 -> 本次任务中是否涉及 -> 是/否，打算怎么做"
        ],
        "creative_analysis": "在这里进行自由发散的文本分析...",
        "final_data": "...",
    }
    return json.dumps(json_schema, ensure_ascii=False, indent=2)


def prompt_adapter_deepseek_r1(original_task: str, experience_rules: str) -> str:
    return (
        f"【系统最高优先级指令: MANDATORY_RULES】\n"
        f"你是一个高级量化智能体。你拥有极强的剧情共情力，但你必须**绝对服从**以下历史经验规则，绝不能被剧情带偏：\n"
        f"{experience_rules}\n\n"
        f"【任务内容】\n"
        f"{original_task}\n\n"
        f"【强制输出格式】\n"
        f"为了防止你忽略规则，你必须严格输出合法的 JSON 格式。而且在 JSON 的开头，你必须先用 rule_checklist 字段逐条审视上述经验规则！\n"
        f"参考 JSON 结构：\n{_schema_str()}"
    )


def prompt_adapter_qwen(original_task: str, experience_rules: str) -> str:
    return (
        f"你是一个高效的数据处理智能体。请严格执行以下规则：\n"
        f"{experience_rules}\n\n"
        f"任务要求：\n{original_task}\n\n"
        f"请按如下 JSON 结构返回你的结果。必须包含 rule_checklist 字段进行规则自检：\n{_schema_str()}"
    )


def prompt_adapter_default(original_task: str, experience_rules: str) -> str:
    return (
        f"系统经验规则：\n{experience_rules}\n\n"
        f"任务内容：\n{original_task}\n\n"
        f"请以严格的 JSON 格式回复，并且在 JSON 顶层必须包含 'rule_checklist' 键来检查你是否遵守了上述规则。"
    )


# ── 发散引擎（繁杂/思考模式） ─────────────────────────────────
def divergent_brainstorm(cmd_text: str) -> str:
    return (
        f"你是一个拥有顶级逻辑思维的网文主编。现在收到作者的指令：\n"
        f"【指令】{cmd_text}\n"
        f"请不要直接写作。你需要进行深度发散思维：\n"
        f"- 分析这个指令背后需要什么维度的设定（世界观、角色弧光、战力拉扯、剧情悬念）。\n"
        f"- 推演可能的坑和矛盾点。\n"
        f"- 给出几个你需要查阅的关键词或设定的核心方向。\n"
        f"请必须以 JSON 格式输出，包含 thinking 和 search_keywords 字段，例如：\n"
        f"{{\n"
        f'  "thinking": "发散思考的内容...",\n'
        f'  "search_keywords": ["关键词1", "关键词2"]\n'
        f"}}\n"
    )


def divergent_think(cmd_text: str) -> str:
    return (
        f"你是一个拥有顶级逻辑思维的网文审核主编。\n"
        f"【作者指令/问题】{cmd_text}\n"
        f"请不要代写小说正文。你需要发散思考：\n"
        f"1. 作者的这个诉求在逻辑上是否有漏洞？\n"
        f"2. 当前设定如果继续走下去，剧情张力和战力会不会崩塌？\n"
        f"3. 总结出一个通用的『发散思维经验』，提炼成以后写作时可以参考的指导原则。\n"
    )


# ── 批次7 深度思考阶段（五阶段状态机单阶段模板） ─────────────
def deep_think_stage_prompt(stage: str, prompt: str, previous_stages: str = "") -> str:
    stage_labels = {
        "MAPPING": "全景构建",
        "SCANNING": "冲突扫描",
        "ANALYZING": "深度分析",
        "VALIDATING": "交叉验证",
        "CONCLUDING": "生成分析报告",
    }
    label = stage_labels.get(stage, stage)
    return (
        f"你是小说创作系统的元认知分析器，正在执行『{label}』阶段（{stage}）。\n"
        f"【作者指令/创作诉求】\n{prompt}\n\n"
        f"【前序阶段产出】\n{previous_stages or '（无，此为第一阶段）'}\n\n"
        f"请仅针对本阶段职责输出分析内容（不要代写小说正文）：\n"
        f"- MAPPING：拆解指令的目标与需要分析的全部维度；\n"
        f"- SCANNING：扫描剧情逻辑、人物状态、时间线矛盾点；\n"
        f"- ANALYZING：对发现的问题逐条深度归因；\n"
        f"- VALIDATING：交叉验证设定一致性，复核结论；\n"
        f"- CONCLUDING：汇总为结构化分析报告。\n"
        f"请用简洁的中文输出，控制在 600 字以内。\n"
    )
